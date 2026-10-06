"""SQL-free deterministic resolution and stable user-facing outcome contract."""

from jsonschema import Draft202012Validator

from sentinel.nlq.execution_contract import (
    EXECUTION_SCHEMA,
    REASONS,
    resolve_execution_plan,
    validate_execution_plan,
)
from sentinel.nlq.intent_planners import clarification
from sentinel.nlq.query_plan import object_schema, request_plan, validate_query_plan
from sentinel.nlq.registry import DIMENSIONS, ENTITIES, METRICS

RESOLUTION_SCHEMA = object_schema(
    {
        "state": {
            "enum": ["supported", "clarification_required", "unsupported", "unsafe", "unavailable"]
        },
        "plan": EXECUTION_SCHEMA,
        "reason_code": {
            "enum": [
                *REASONS,
                "empty_result",
                "execution_timeout",
                "execution_error",
                "invalid_evidence",
                "invalid_analysis_evidence",
                "resolution_unavailable",
                "sql_safety_rejection",
                "analysis_unavailable",
            ]
        },
        "fields": {"type": "array", "items": {"type": "string"}},
        "allowed_choices": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "message": {"type": "string"},
    }
)
CHOICES = {
    **{key: list(values) for key, values in ENTITIES.items()},
    "entities": [value for values in ENTITIES.values() for value in values],
    "metrics": list(METRICS),
    "group_by": list(DIMENSIONS),
    "time_range": [
        "an explicit historical start and end date",
        "last month",
        "this month",
        "past N days",
        "previous quarter",
    ],
    "scenario.demand_increase": ["an explicit increase from 0% through 200%"],
    "horizon_days": ["stockout: 1 through 90 days", "forecast: 14 days"],
}
CLARIFIABLE = {
    "malformed_input",
    "missing_entity",
    "missing_scenario",
    "missing_horizon",
    "missing_or_ambiguous_date",
    "unknown_or_ambiguous_entity",
    "ambiguous_intent",
}


def validate_resolution(value):
    Draft202012Validator(RESOLUTION_SCHEMA).validate(value)
    validate_execution_plan(value["plan"])
    if value["state"] == "supported":
        if value["plan"]["abstain"] or value["reason_code"] or value["fields"]:
            raise ValueError("Supported resolution requires a complete validated plan")
    elif not value["reason_code"] or not value["fields"] or not value["message"]:
        raise ValueError("Abstention requires reason, field and explanation")
    if (
        set(value["fields"])
        - set(CHOICES)
        - {"request", "sql", "evidence", "granularity", "intent"}
    ):
        raise ValueError("Unknown resolution field")
    if value["state"] in {"clarification_required", "unsupported", "unsafe"}:
        if not value["plan"]["abstain"] or (value["state"] == "unsafe") != (
            value["plan"]["intent"] == "unsafe"
        ):
            raise ValueError("Resolution state conflicts with abstention plan")
    for field, choices in value["allowed_choices"].items():
        if field not in value["fields"] or choices != CHOICES.get(field):
            raise ValueError("Choices must come from the canonical registry")
    return value


def resolve(question):
    plan = validate_query_plan(request_plan(question), question)
    full = resolve_execution_plan(plan, question)
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        full["abstention_reason"] = "malformed_input"
    reason = full["abstention_reason"]
    detail = clarification(question) if plan["abstain"] else None
    fields = (detail or {}).get("required_fields") or (["request"] if plan["abstain"] else [])
    state = (
        "supported"
        if not plan["abstain"]
        else "unsafe"
        if plan["intent"] == "unsafe"
        else "clarification_required"
        if reason in CLARIFIABLE
        else "unsupported"
    )
    return validate_resolution(
        {
            "state": state,
            "plan": full,
            "reason_code": reason,
            "fields": fields,
            "allowed_choices": {field: CHOICES[field] for field in fields if field in CHOICES},
            "message": "Validated request within the supported grammar."
            if state == "supported"
            else "Destructive or external actions are not supported. No SQL executed."
            if state == "unsafe"
            else (detail or {}).get("question", "Please provide a supported supply-chain request."),
        }
    )


def execution_abstention(record, reason, field="evidence"):
    record["resolution"] = validate_resolution(
        {
            **record["resolution"],
            "state": "unavailable",
            "reason_code": reason,
            "fields": [field],
            "allowed_choices": {},
            "message": record["failure_behavior"]
            or "Verified evidence is unavailable; no recommendation was created.",
        }
    )
