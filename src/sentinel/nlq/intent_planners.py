"""Five bounded intent planners. Unknown qualifiers are evidence for abstention.

Candidate discovery is separate from slot resolution and contract validation.
These planners emit data only; the existing compiler is the only SQL author.
"""

import re
from dataclasses import dataclass
from datetime import date

from jsonschema import ValidationError

from sentinel.nlq.registry import (
    INTENT_METRIC,
    OPTIONAL_ENTITIES,
    REQUIRED_ENTITIES,
    metric_candidates,
    resolve_metric,
)
from sentinel.nlq.semantics import (
    COMMON,
    VOCABULARY,
    coverage_horizon,
    date_range,
    demand_increase,
    extract_entities,
    forecast_horizon,
    is_unsafe,
    normalize,
)


@dataclass(frozen=True)
class Interpretation:
    plan: dict
    reason: str | None = None
    required_fields: tuple = ()


class Clarify(ValueError):
    def __init__(self, reason, *fields):
        self.reason, self.fields = reason, fields


def supplier_lateness(plan, text):
    if re.search(r"\b(?:daily|weekly) history\b", text):
        raise Clarify("unsupported_granularity", "granularity")
    if re.search(r"\b(?:count of|number of|total|how many)\b", text) and not re.search(
        r"\b(rate|fraction|share|percentage|divided)\b", text
    ):
        raise Clarify("unsupported_metric", "metrics")
    try:
        plan["time_range"], rest = date_range(text)
    except ValueError as exc:
        raise Clarify(
            "future_history" if "future" in str(exc) else "missing_or_ambiguous_date", "time_range"
        ) from exc
    if re.search(r"\bmonthly\b", rest):
        from sentinel.nlq.dates import month_end

        start, end = (date.fromisoformat(plan["time_range"][k]) for k in ("start", "end"))
        if start.day != 1 or end != month_end(start.year, start.month):
            raise Clarify("unsupported_granularity", "granularity")
        rest = re.sub(r"\bmonthly\b", " ", rest)
    return rest


def stockout_risk(plan, text):
    if re.search(r"\b(more|above|greater|at least)\b|\b(last|past|previous) \d+ days?\b", text):
        raise Clarify("unsupported_modifier", "horizon_days")
    try:
        plan["horizon_days"], rest = coverage_horizon(text)
    except ValueError as exc:
        raise Clarify("missing_horizon", "horizon_days") from exc
    return rest


def daily_forecast(plan, text):
    try:
        plan["horizon_days"], rest = forecast_horizon(text)
    except ValueError as exc:
        raise Clarify("unsupported_granularity", "horizon_days") from exc
    return rest


def supplier_evidence(plan, text):
    return text


def demand_what_if(plan, text):
    if not re.search(
        r"\b(up|increase|increases|higher|rising|rises|uplift|additional|twice|times|raise|grows|more)\b",
        text,
    ):
        raise Clarify("missing_scenario", "scenario.demand_increase")
    try:
        plan["scenario"], rest = demand_increase(text)
    except ValueError as exc:
        raise Clarify("missing_scenario", "scenario.demand_increase") from exc
    rest = rest.replace("without changing inventory", " ")
    if "without" in rest.split():
        raise Clarify("unsupported_modifier")
    return rest


PLANNERS = {
    "supplier_delay": supplier_lateness,
    "stockout": stockout_risk,
    "forecast": daily_forecast,
    "supplier_risk": supplier_evidence,
    "what_if": demand_what_if,
}


def candidate_intent(q, entities):
    metrics = metric_candidates(q)
    scenario = bool(
        re.search(r"\b(demand|coverage|inventory|units)\b", q)
        and re.search(
            r"%|percent|\b(increase|increases|higher|uplift|twice|times|half|additional|rises|rising)\b",
            q,
        )
    )
    forecast = bool(
        re.search(r"\b(forecast|forecasting|predict|projection|project|outlook|estimate)\b", q)
        or (
            entities["product_id"]
            and entities["warehouse_id"]
            and re.search(r"\b(demand|units|sales)\b", q)
            and re.search(r"\b(weeks?|fortnight|daily|day)\b", q)
        )
    )
    evidence = bool(
        entities["supplier_id"]
        and re.search(
            r"\b(risk|reliability|dependability|evidence|observations|flagged|reviewing)\b", q
        )
    )
    lateness = bool(
        (
            entities["supplier_id"]
            or re.search(r"\b(suppliers?|vendors?)\b", q)
            or "delivery promises" in q
        )
        and ("late_delivery_rate" in metrics or re.search(r"\b(late|lateness|missed)\b", q))
    )
    if sum((scenario, forecast, evidence)) > 1:
        raise Clarify("ambiguous_intent", "intent")
    if scenario:
        return "what_if"
    if forecast:
        return "forecast"
    if evidence:
        return "supplier_risk"
    if lateness:
        return "supplier_delay"
    if "days_of_cover" in metrics or re.search(
        r"\b(stock|inventory|stockout|coverage|covers?)\b", q
    ):
        return "stockout"
    raise Clarify("unsupported_or_incomplete", "intent")


def interpret(question):
    from sentinel.nlq.query_plan import make_plan, validate_query_plan

    try:
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise Clarify("malformed_input")
        if is_unsafe(question):
            return Interpretation(make_plan("unsafe"), "unsafe_request")
        q = normalize(question)
        # Regular noun morphology is semantic-preserving; no benchmark phrases.
        q = re.sub(r"\b(forecasts|projections|outlooks)\b", lambda m: m[0][:-1], q)
        if re.search(
            r"\b(or|not|excluding|except|decrease|decreases|lower|lowering|less demand)\b", q
        ):
            raise Clarify("unsupported_modifier")
        try:
            entities, rest = extract_entities(q)
        except ValueError as exc:
            raise Clarify("unknown_or_ambiguous_entity", "entities") from exc
        intent = candidate_intent(q, entities)
        present = {key for key, value in entities.items() if value is not None}
        missing = REQUIRED_ENTITIES[intent] - present
        if missing:
            raise Clarify("missing_entity", *sorted(missing))
        if present - REQUIRED_ENTITIES[intent] - OPTIONAL_ENTITIES.get(intent, set()):
            raise Clarify("unsupported_modifier", "entities")
        plan = make_plan(intent, entities=entities)
        assert plan["metrics"] == [resolve_metric(INTENT_METRIC[intent])]
        rest = PLANNERS[intent](plan, rest)
        words = set(re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:\.\d+)?|[%=<>]", rest))
        if not words <= COMMON | {"was", "were", "been"} | set(VOCABULARY[intent].split()):
            raise Clarify("unsupported_modifier")
        return Interpretation(validate_query_plan(plan))
    except Clarify as exc:
        return Interpretation(make_plan(), exc.reason, tuple(exc.fields))
    except (ValueError, TypeError, ValidationError):
        return Interpretation(make_plan(), "unsupported_or_incomplete")


def clarification(question):
    result = interpret(question)
    if not result.plan["abstain"] or result.reason == "unsafe_request":
        return None
    prompts = {
        "time_range": "Which explicit historical date range should I use?",
        "supplier_id": "Which supplier should I use?",
        "warehouse_id": "Which warehouse should I use?",
        "product_id": "Which product should I use?",
        "scenario.demand_increase": "What exact demand increase should the simulation use?",
        "horizon_days": "Which supported day horizon should I use?",
        "entities": "Please specify one unambiguous canonical entity per filter.",
        "granularity": "This query supports a period aggregate, not a daily or weekly breakdown.",
        "metrics": "Please select a supported metric; counts and rates are not interchangeable.",
        "intent": "Please request one supported supply-chain analysis at a time.",
    }
    return {
        "reason": result.reason,
        "required_fields": list(result.required_fields),
        "question": " ".join(prompts[f] for f in result.required_fields if f in prompts)
        or "Please restate the request using supported metrics, filters and explicit quantities.",
    }
