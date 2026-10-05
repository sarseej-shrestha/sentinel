"""Versioned execution contract; legacy proposal labels remain byte-compatible.

Every executable proposal is upgraded and validated here before compilation.
Metadata is deterministic application data, never permission to broaden SQL.
"""

import re
from datetime import date

from jsonschema import Draft202012Validator, FormatChecker

from sentinel.nlq.query_plan import (
    QUERY_PLAN_SCHEMA,
    make_plan,
    object_schema,
    parse_output,
    validate_query_plan,
)

REASONS = [
    None,
    "unsafe_request",
    "unsupported_or_incomplete",
    "malformed_input",
    "missing_or_ambiguous_date",
    "future_history",
    "unsupported_granularity",
    "unknown_or_ambiguous_entity",
    "missing_entity",
    "missing_scenario",
    "missing_horizon",
    "unsupported_metric",
    "unsupported_modifier",
    "ambiguous_intent",
]
EXECUTION_SCHEMA = object_schema(
    {
        **QUERY_PLAN_SCHEMA["properties"],
        "contract_version": {"const": 2},
        "granularity": {
            "enum": ["none", "snapshot", "day", "range", "month", "quarter", "all_history"]
        },
        "date_basis": {
            "enum": [None, "promised_date", "demand_date", "inventory_date", "all_observed"]
        },
        "supplier_scope": {"enum": [None, "all", "single"]},
        "abstention_reason": {"enum": REASONS},
    }
)


def abstention_reason(question, intent):
    if intent == "unsafe":
        return "unsafe_request"
    if question is None:
        return "unsupported_or_incomplete"
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        return "malformed_input"
    from sentinel.nlq.dates import historical_window
    from sentinel.nlq.semantics import normalize

    if re.search(r"\b(?:daily|weekly) history\b", question.casefold()):
        return "unsupported_granularity"
    if re.search(r"\b(late|lateness)\b", question.casefold()):
        try:
            historical_window(normalize(question))
        except ValueError as exc:
            return "future_history" if "future" in str(exc) else "missing_or_ambiguous_date"
    from sentinel.nlq.intent_planners import interpret

    return interpret(question).reason or "unsupported_or_incomplete"


def metadata(plan, question=None):
    intent = plan["intent"]
    grain, basis, scope = "none", None, None
    if intent == "supplier_delay":
        grain, basis = "range", "promised_date"
        start, end = (date.fromisoformat(plan["time_range"][k]) for k in ("start", "end"))
        months = 12 * (end.year - start.year) + end.month - start.month
        if start.day == end.day == 1:
            grain = (
                "quarter"
                if months == 3 and start.month in (1, 4, 7, 10)
                else "month"
                if months == 1
                else "range"
            )
        if question is not None:
            from sentinel.nlq.dates import historical_window
            from sentinel.nlq.semantics import normalize

            window, _ = historical_window(normalize(question))
            if window.boundaries() != plan["time_range"]:
                raise ValueError("Date metadata must match the validated request")
            grain = window.granularity
        scope = "single" if plan["entities"]["supplier_id"] else "all"
    elif intent == "forecast":
        grain, basis = "day", "demand_date"
    elif intent in {"stockout", "what_if"}:
        grain, basis = "snapshot", "inventory_date"
    elif not plan["abstain"]:
        grain, basis = "all_history", "all_observed"
        scope = "single" if plan["entities"]["supplier_id"] else "all"
    return {
        "contract_version": 2,
        "granularity": grain,
        "date_basis": basis,
        "supplier_scope": scope,
        "abstention_reason": abstention_reason(question, intent) if plan["abstain"] else None,
    }


def resolve_execution_plan(value=None, question=None):
    plan = validate_query_plan(
        make_plan() if value is None else value, question if value is not None else None
    )
    return validate_execution_plan({**plan, **metadata(plan, question)}, question)


def validate_execution_plan(value, question=None):
    result = parse_output(value)
    Draft202012Validator(EXECUTION_SCHEMA, format_checker=FormatChecker()).validate(result)
    core = validate_query_plan({k: result[k] for k in QUERY_PLAN_SCHEMA["properties"]}, question)
    expected = metadata(core, question)
    for field in expected:
        if field == "granularity" and question is None and core["intent"] == "supplier_delay":
            # Explicit date ranges may coincide with a calendar month/quarter.
            # Preserve the request's range label instead of inventing a grain.
            if result[field] not in {"range", expected[field]}:
                raise ValueError("Granularity is incompatible with date boundaries")
        elif field == "abstention_reason" and question is None and core["abstain"]:
            if result[field] is None or (core["intent"] == "unsafe") != (
                result[field] == "unsafe_request"
            ):
                raise ValueError("Abstention requires a compatible explicit reason")
        elif result[field] != expected[field]:
            raise ValueError(f"Execution contract has incompatible {field}")
    return {**result, **core}
