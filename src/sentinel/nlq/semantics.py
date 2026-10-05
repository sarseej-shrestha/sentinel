"""Development-grounded slot parsing with a closed vocabulary and fixed semantics.

Unknown qualifiers are not dropped. This module supplies only canonical plan data;
the existing contract, compiler, AST guard and read-only executor remain authoritative.
"""

import calendar
import re
from datetime import date, timedelta

from jsonschema import ValidationError

from sentinel.config import AS_OF

NUMBERS = dict(
    zip(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        range(1, 21),
    )
)
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTH = "(?:" + "|".join(MONTHS) + ")"
ENTITY = re.compile(
    r"\b(?P<product>(?:synthetic\s+)?product\s+(?:p?\d+|[a-z])|p\d+)\b|"
    r"\b(?P<warehouse>warehouse\s+(?:w?\d+|[a-z])|w\d+)\b|"
    r"\b(?P<supplier>supplier\s+(?:[a-z]|s\d+)|s\d+)\b"
)
COMMON = set(
    "a an the for at in of by to with and all each every across as per is are be it its their our current me i you can please give show list find report use using on over next this what which how who does would might should need want have has from during through between into unchanged only".split()
)
VOCABULARY = {
    "supplier_delay": "rank suppliers supplier vendors vendor delivery deliveries late arrival arrivals lateness missed promises dates rate rates fraction divided evaluable percentage percent share comparison compare calculate summarize largest highest most often worst proportion",
    "stockout": "inventory positions items stock days day cover coverage balances below fewer than less under short buffer products product locations location sites warehouse warehouses combinations units average daily sales demand review prioritize help risk queue cutoff threshold highlight unlikely likely out stockout within remaining full last lasts low estimated",
    "forecast": "build daily day demand projection project forecast forecasting predict estimate fortnight units sales volume outlook horizon ahead expect prepare give located located next two weeks week fourteen days days by level like i'd",
    "supplier_risk": "back up reliability assessment shipment evidence observations explain explains risk rating assigned historical delivery review why dependability supporting records late fraction sample count understand facts behind being flagged trail support actual operational console recommends reviewing identify missing considered high risky supplier",
    "what_if": "stock stocks fixed simulate demand higher more units daily happens inventory coverage calculate impact rising rises grows increases increase half assume uplift leave replenishment consuming twice times much becomes present level try without changing run scenario additional keep prices raise compare today today's percent percent more up if needs with above unchanged grows growth",
}


COMMON |= {"had", "we", "many", "when"}
VOCABULARY["supplier_delay"] += " shipments promised"
VOCABULARY["stockout"] += " whose hand quantity covers there fall"
VOCABULARY["forecast"] += " look"
VOCABULARY["what_if"] += " cover remains day model"


def normalize(question):
    q = question.casefold().replace("’", "'")
    if re.search(r"(?<!\w)-\d", q):
        raise ValueError("Negative quantities are unsupported")
    q = re.sub(r"(?<!\d)-|-(?!\d)", " ", q)
    for word, number in NUMBERS.items():
        q = re.sub(rf"\b{word}\b", str(number), q)
    return re.sub(r"\s+", " ", q).strip()


def is_unsafe(question):
    # Action/injection cues from the curated development negatives.
    return bool(
        re.search(
            r"\b(delete|drop|truncate|insert|update|alter|attach|copy|install|load|buy|purchase|ship|send|create|remove|dispatch|forge)\b|"
            r"\b(ignore.*(?:restrictions|rules)|system override|hide.*audit|change.*lead time)\b",
            question.casefold(),
        )
    )


def extract_entities(q):
    from sentinel.nlq.query_plan import canonical_entity

    entities = dict.fromkeys(("supplier_id", "warehouse_id", "product_id"))

    def replace(match):
        kind = match.lastgroup + "_id"
        value = canonical_entity(kind, match[0])
        if entities[kind] is not None and entities[kind] != value:
            raise ValueError("Multiple entities for one filter")
        entities[kind] = value
        return " "

    return entities, ENTITY.sub(replace, q).replace("'s", " ")


def month_end(year, month):
    return date(year + (month == 12), 1 if month == 12 else month + 1, 1)


def date_range(q):
    """Consume exactly one supported date expression and leave all other text visible."""
    today = date.fromisoformat(AS_OF)
    patterns = [
        (
            r"(?:last month|previous calendar month|calendar month before ("
            + MONTH
            + r") (\d{4}))",
            "relative",
        ),
        (rf"first (\d+) days of ({MONTH}) (\d{{4}})", "prefix"),
        (r"(first|second|third|fourth) quarter of (\d{4})", "quarter"),
        (
            r"(\d{4}-\d{2}-\d{2})\s+(?:through|to)\s+(\d{4}-\d{2}-\d{2})\s+(inclusive|exclusive)",
            "iso",
        ),
        (
            rf"({MONTH}) (\d+) and ({MONTH}) (\d+) inclusive\??[.;]? use (\d{{4}}) dates",
            "named_days",
        ),
        (rf"({MONTH})(?: of)? (\d{{4}})", "month"),
    ]
    for pattern, kind in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        if kind == "relative":
            end = date(int(match[2]), MONTHS[match[1]], 1) if match[1] else today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        elif kind == "prefix":
            start = date(int(match[3]), MONTHS[match[2]], 1)
            end = date(start.year, start.month, int(match[1])) + timedelta(days=1)
        elif kind == "quarter":
            month = 1 + 3 * ("first second third fourth".split().index(match[1]))
            start = date(int(match[2]), month, 1)
            end = month_end(start.year, month + 2)
        elif kind == "iso":
            start, end = date.fromisoformat(match[1]), date.fromisoformat(match[2])
            end += timedelta(days=match[3] == "inclusive")
        elif kind == "named_days":
            start = date(int(match[5]), MONTHS[match[1]], int(match[2]))
            end = date(int(match[5]), MONTHS[match[3]], int(match[4])) + timedelta(days=1)
        else:
            start = date(int(match[2]), MONTHS[match[1]], 1)
            end = month_end(start.year, start.month)
        if not start < end <= today + timedelta(days=1):
            raise ValueError("Invalid or future date range")
        return {"start": start.isoformat(), "end": end.isoformat()}, q[: match.start()] + " " + q[
            match.end() :
        ]
    raise ValueError("Missing or ambiguous date range")


def coverage_horizon(q):
    matches = list(re.finditer(r"\b(\d+)\s+(?:full\s+)?days?\b", q))
    if len(matches) != 1:
        raise ValueError("One explicit day horizon required")
    match = matches[0]
    days = int(match[1])
    return days, q[: match.start()] + " " + q[match.end() :]


def forecast_horizon(q):
    matches = list(re.finditer(r"\b(?:(\d+)\s+(days?|weeks?)|fortnight)\b", q))
    if len(matches) > 1:
        raise ValueError("Conflicting forecast horizons")
    if not matches:
        return 14, q
    match = matches[0]
    days = 14 if match[1] is None else int(match[1]) * (7 if match[2].startswith("week") else 1)
    if days != 14:
        raise ValueError("Only daily 14-day forecasts are supported")
    return days, q[: match.start()] + " " + q[match.end() :]


def demand_increase(q):
    patterns = [
        (r"\b(\d+(?:\.\d+)?)\s*(?:%|percent\b)", "percent"),
        (r"\b(\d+(?:\.\d+)?)\s+times\b", "times"),
        (r"\b(?:1 half|twice)\b", "word"),
    ]
    found = []
    for pattern, kind in patterns:
        for match in re.finditer(pattern, q):
            amount = (
                float(match[1]) / 100
                if kind == "percent"
                else round(float(match[1]) - 1, 10)
                if kind == "times"
                else 0.5
                if match[0] == "1 half"
                else 1.0
            )
            found.append((amount, match))
    if len(found) != 1 or not 0 <= found[0][0] <= 2:
        raise ValueError("One explicit demand increase between 0 and 200 percent required")
    amount, match = found[0]
    return {"demand_increase": amount}, q[: match.start()] + " " + q[match.end() :]


def parse_semantics(question):
    from sentinel.nlq.query_plan import make_plan, validate_query_plan

    try:
        q = normalize(question)
        if re.search(
            r"\b(or|not|excluding|except|decrease|decreases|lower|lowering|less demand)\b", q
        ):
            return make_plan()
        entities, remainder = extract_entities(q)
        if re.search(r"\b(demand|coverage|inventory|units)\b", q) and re.search(
            r"%|percent|\b(increase|increases|higher|uplift|twice|times|half|additional|rises|rising)\b",
            q,
        ):
            intent = "what_if"
        elif re.search(
            r"\b(forecast|forecasting|predict|projection|project|outlook|estimate)\b", q
        ) or (
            entities["product_id"]
            and entities["warehouse_id"]
            and re.search(r"\b(demand|units|sales)\b", q)
            and re.search(r"\b(weeks?|fortnight|daily|day)\b", q)
        ):
            intent = "forecast"
        elif entities["supplier_id"] and re.search(
            r"\b(risk|reliability|dependability|evidence|observations|flagged|reviewing)\b", q
        ):
            intent = "supplier_risk"
        elif (re.search(r"\b(suppliers?|vendors?)\b", q) or "delivery promises" in q) and re.search(
            r"\b(late|lateness|missed)\b", q
        ):
            intent = "supplier_delay"
        elif re.search(r"\b(stock|inventory|stockout|coverage|covers?)\b", q):
            intent = "stockout"
        else:
            return make_plan()
        result = make_plan(intent, entities=entities)
        if intent == "supplier_delay":
            result["time_range"], remainder = date_range(remainder)
        elif intent == "stockout":
            # Do not turn "above N days" into a below-threshold query.
            if re.search(
                r"\b(more|above|greater|at least)\b|\b(last|past|previous) \d+ days?\b", q
            ):
                return make_plan()
            result["horizon_days"], remainder = coverage_horizon(remainder)
        elif intent == "forecast":
            result["horizon_days"], remainder = forecast_horizon(remainder)
        elif intent == "what_if":
            if not re.search(
                r"\b(up|increase|increases|higher|rising|rises|uplift|additional|twice|times|raise|grows|more)\b",
                q,
            ):
                return make_plan()
            result["scenario"], remainder = demand_increase(remainder)
            remainder = remainder.replace("without changing inventory", " ")
            if "without" in remainder.split():
                return make_plan()
        # No unconsumed numbers, filters, negations or unknown qualifier vocabulary.
        words = set(re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:\.\d+)?|[%=<>]", remainder))
        if not words <= COMMON | set(VOCABULARY[intent].split()):
            return make_plan()
        return validate_query_plan(result)
    except (ValueError, TypeError, ValidationError):
        return make_plan()
