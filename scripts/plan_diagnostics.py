"""Proposal diagnostics only: partial field credit never grants execution authority."""

from collections import Counter

from sentinel.nlq.query_plan import canonical_entity, parse_output, validate_query_plan

FIELDS = (
    "intent",
    "entities",
    "time_range",
    "metrics",
    "group_by",
    "scenario",
    "evidence_requirements",
    "abstain",
    "horizon_days",
)
TAXONOMY = (
    "wrong_intent",
    "wrong_entity_normalization",
    "missing_entity_filter",
    "wrong_date_range",
    "wrong_metric",
    "wrong_grouping",
    "missing_evidence_requirement",
    "missing_what_if_parameter",
    "incorrect_abstention",
    "unsupported_request_incorrectly_accepted",
    "formatting_failure",
    "wrong_what_if_parameter",
    "contract_violation",
)


def diagnose(raw, expected):
    expected = validate_query_plan(expected)
    errors = set()
    try:
        parsed = parse_output(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Expected object")
    except (ValueError, TypeError):
        parsed = {}
        errors.add("formatting_failure")
    normalized = dict(parsed)
    if isinstance(parsed.get("entities"), dict):
        entities = dict(parsed["entities"])
        for key, value in entities.items():
            try:
                entities[key] = canonical_entity(key, value)
            except (KeyError, ValueError, AttributeError, TypeError):
                pass
        normalized["entities"] = entities
    for key in ("metrics", "group_by", "evidence_requirements"):
        value = normalized.get(key)
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            normalized[key] = sorted(value)
    fields = {
        key: key in normalized
        and normalized[key] == expected[key]
        and (key != "abstain" or isinstance(normalized[key], bool))
        for key in FIELDS
    }
    try:
        valid = validate_query_plan(raw)
    except Exception:
        valid = None
        errors.add("contract_violation")
    if not parsed or not set(parsed).intersection(FIELDS):
        errors.add("formatting_failure")
    if "formatting_failure" not in errors:
        for key, label in {
            "intent": "wrong_intent",
            "time_range": "wrong_date_range",
            "metrics": "wrong_metric",
            "group_by": "wrong_grouping",
        }.items():
            if not fields[key]:
                errors.add(label)
        actual = normalized.get("entities")
        actual = actual if isinstance(actual, dict) else {}
        for key, wanted in expected["entities"].items():
            if wanted is not None and actual.get(key) is None:
                errors.add("missing_entity_filter")
            elif actual.get(key) != wanted:
                errors.add("wrong_entity_normalization")
        evidence = parsed.get("evidence_requirements")
        if not isinstance(evidence, list) or any(
            x not in evidence for x in expected["evidence_requirements"]
        ):
            errors.add("missing_evidence_requirement")
        if not fields["scenario"]:
            scenario = parsed.get("scenario")
            errors.add(
                "missing_what_if_parameter"
                if expected["scenario"] is not None
                and (not isinstance(scenario, dict) or "demand_increase" not in scenario)
                else "wrong_what_if_parameter"
            )
        if parsed.get("abstain") is True and not expected["abstain"]:
            errors.add("incorrect_abstention")
        if valid is not None and not valid["abstain"] and expected["abstain"]:
            errors.add("unsupported_request_incorrectly_accepted")
    return {
        "fields": fields,
        "applicable": {
            key: (
                any(v is not None for v in expected[key].values())
                if key == "entities"
                else True
                if key in {"intent", "abstain"}
                else expected[key] not in (None, [])
            )
            for key in FIELDS
        },
        "errors": sorted(errors),
        "schema_valid": valid is not None,
        "whole_plan_exact": valid == expected,
    }


def aggregate(diagnostics):
    count = len(diagnostics)
    errors = Counter(error for d in diagnostics for error in d["errors"])
    return {
        "cases": count,
        "whole_plan_exact": sum(d["whole_plan_exact"] for d in diagnostics),
        "field_matches": {
            key: {"correct": sum(d["fields"][key] for d in diagnostics), "total": count}
            for key in FIELDS
        },
        "field_matches_applicable": {
            key: {
                "correct": sum(d["fields"][key] for d in diagnostics if d["applicable"][key]),
                "total": sum(d["applicable"][key] for d in diagnostics),
            }
            for key in FIELDS
        },
        "error_taxonomy": {error: errors[error] for error in TAXONOMY},
    }
