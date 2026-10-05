"""SQL-free model contract, bounded request grounding, and trusted SQL templates."""

import copy
import json
import re
from datetime import date, timedelta

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from sentinel.config import AS_OF
from sentinel.nlq.registry import ENTITIES, OPTIONAL_ENTITIES, REQUIRED_ENTITIES, resolve_entity

# Metric/dimension/evidence names describe semantics, never interpolated SQL identifiers.
SPECS = {
    "supplier_delay": (
        ["late_delivery_rate"],
        ["supplier_id"],
        ["supplier_id", "late_shipments", "evaluable_shipments", "late_delivery_rate"],
    ),
    "stockout": (
        ["days_of_cover"],
        ["product_id", "warehouse_id"],
        ["source_record_id", "on_hand", "avg_daily_demand", "days_of_cover"],
    ),
    "forecast": (["units"], ["demand_date"], ["source_record_id", "demand_date", "units"]),
    "supplier_risk": (
        ["supplier_reliability"],
        ["supplier_id"],
        ["source_record_id", "late_delivery_rate", "evaluable_shipments"],
    ),
    "what_if": (
        ["scenario_days_of_cover"],
        ["product_id", "warehouse_id"],
        ["source_record_id", "on_hand", "avg_daily_demand"],
    ),
    "shipments": (
        ["delay_days"],
        ["shipment_id"],
        ["source_record_id", "promised_date", "delivered_date"],
    ),
    "missing_dates": (
        ["missing_promised_date"],
        ["shipment_id"],
        ["source_record_id", "promised_date"],
    ),
    "unsupported": ([], [], []),
    "unsafe": ([], [], []),
}


def object_schema(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


QUERY_PLAN_SCHEMA = object_schema(
    {
        "intent": {"enum": list(SPECS)},
        "entities": object_schema(
            {key: {"type": ["string", "null"], "maxLength": 64} for key in ENTITIES}
        ),
        "time_range": {
            "anyOf": [
                {"type": "null"},
                object_schema(
                    {key: {"type": "string", "format": "date"} for key in ["start", "end"]}
                ),
            ]
        },
        "metrics": {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": sorted({x for spec in SPECS.values() for x in spec[0]})},
        },
        "group_by": {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": sorted({x for spec in SPECS.values() for x in spec[1]})},
        },
        "scenario": {
            "anyOf": [
                {"type": "null"},
                object_schema({"demand_increase": {"type": "number", "minimum": 0, "maximum": 2}}),
            ]
        },
        "horizon_days": {"type": ["integer", "null"], "minimum": 1, "maximum": 90},
        "evidence_requirements": {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": sorted({x for spec in SPECS.values() for x in spec[2]})},
        },
        "abstain": {"type": "boolean"},
    }
)


def canonical_entity(kind, value):
    return resolve_entity(kind, value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_output(value):
    if isinstance(value, str):
        if len(value) > 12000:
            raise ValueError("QueryPlan output exceeds 12,000 characters")
        value = value.strip()
        if value.startswith("```"):
            match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", value, re.DOTALL)
            if not match:
                raise ValueError("Expected one JSON object, optionally in one JSON fence")
            value = match[1]
        value = json.loads(value, object_pairs_hook=_unique_object)
    # Freeze caller-owned values; reject NaN/Infinity even in direct Python inputs.
    json.dumps(value, allow_nan=False)
    return copy.deepcopy(value)


def make_plan(intent="unsupported", **values):
    metrics, dimensions, evidence = SPECS[intent]
    result = {
        "intent": intent,
        "entities": dict.fromkeys(ENTITIES),
        "time_range": None,
        "metrics": sorted(metrics),
        "group_by": sorted(dimensions),
        "scenario": None,
        "horizon_days": None,
        "evidence_requirements": sorted(evidence),
        "abstain": intent in {"unsupported", "unsafe"},
    }
    result.update(values)
    return result


def validate_query_plan(value, question=None):
    result = parse_output(value)
    Draft202012Validator(QUERY_PLAN_SCHEMA, format_checker=FormatChecker()).validate(result)
    intent = result["intent"]
    for kind, entity in result["entities"].items():
        result["entities"][kind] = canonical_entity(kind, entity)
    for field, allowed in zip(("metrics", "group_by", "evidence_requirements"), SPECS[intent]):
        if set(result[field]) != set(allowed):
            raise ValueError(f"{intent} requires exactly these {field}: {allowed}")
        result[field] = sorted(result[field])
    required_entities = REQUIRED_ENTITIES[intent]
    present = {key for key, value in result["entities"].items() if value is not None}
    if not required_entities <= present or present - required_entities - OPTIONAL_ENTITIES.get(
        intent, set()
    ):
        raise ValueError(
            f"{intent} requires exactly these entity filters: {sorted(required_entities)}"
        )
    if result["abstain"] != (intent in {"unsafe", "unsupported"}):
        raise ValueError("Abstention must use an unsafe or unsupported intent")
    if intent == "supplier_delay":
        span = result["time_range"]
        if span is None or not span["start"] < span["end"]:
            raise ValueError("Monthly rates require a nonempty half-open date range")
        if date.fromisoformat(span["end"]) > date.fromisoformat(AS_OF) + timedelta(days=1):
            raise ValueError("Historical delivery rates cannot include future dates")
    elif result["time_range"] is not None:
        raise ValueError("This intent does not accept a time-range filter")
    if (result["scenario"] is not None) != (intent == "what_if"):
        raise ValueError("A demand what-if requires an explicit demand_increase fraction")
    if intent == "stockout":
        if result["horizon_days"] is None:
            raise ValueError("Stockout requires a coverage horizon")
    elif intent == "forecast":
        if result["horizon_days"] != 14:
            raise ValueError("The demo forecast supports a 14-day horizon")
    elif result["horizon_days"] is not None:
        raise ValueError("This intent does not accept a horizon")
    if question is not None and result != request_plan(question):
        raise ValueError(
            "QueryPlan does not match the requested intent, entities, dates or scenario"
        )
    return result


def request_plan(question):
    """Conservative full-request grammar. Unknown qualifiers never silently disappear.

    This independently bounds semantic validation and provides the labeled fallback.
    It is deliberately not an open-domain language understanding claim.
    """
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        return make_plan()
    q = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.!")
    from sentinel.nlq.semantics import is_unsafe, parse_semantics

    if is_unsafe(q):
        return make_plan("unsafe")
    patterns = {
        "supplier_delay": [
            r"which suppliers had the highest late[- ]delivery rate (last month)",
            r"rank suppliers by late[- ]delivery rate in (august 2026)",
            r"which suppliers were late most often during (last month)",
            r"rank suppliers by (last month)'s late delivery rate",
        ],
        "stockout": [
            r"show products likely to stock out within the next (\d+) days",
            r"find products with fewer than (\d+) days of stock remaining",
            r"list inventory at risk of stockout over the coming (\d+) days",
        ],
        "forecast": [
            r"forecast demand for (.+) at (.+)",
            r"predict demand for (.+) in (.+)",
            r"give me a demand forecast for (.+) at (.+)",
        ],
        "supplier_risk": [
            r"why is (.+) considered high risk",
            r"explain the risk for (.+)",
            r"show the evidence behind (.+)'s supplier risk",
        ],
        "what_if": [
            r"what happens if demand increases by (\d+(?:\.\d+)?)% at (.+)",
            r"simulate demand up (\d+(?:\.\d+)?)% in (.+)",
            r"what if (.+) demand rises by (\d+(?:\.\d+)?)%",
        ],
        "shipments": [r"show shipments for (.+)"],
        "missing_dates": [r"show shipments missing promised delivery dates"],
    }
    for intent, forms in patterns.items():
        for index, pattern in enumerate(forms):
            match = re.fullmatch(pattern, q)
            if not match:
                continue
            result = make_plan(intent)
            try:
                if intent == "supplier_delay":
                    end = date.fromisoformat(AS_OF).replace(day=1)
                    start = (end - timedelta(days=1)).replace(day=1)
                    if match[1] == "august 2026":
                        start, end = date(2026, 8, 1), date(2026, 9, 1)
                    result["time_range"] = {"start": start.isoformat(), "end": end.isoformat()}
                elif intent == "stockout":
                    result["horizon_days"] = int(match[1])
                elif intent == "forecast":
                    result["entities"]["product_id"] = canonical_entity("product_id", match[1])
                    result["entities"]["warehouse_id"] = canonical_entity("warehouse_id", match[2])
                    result["horizon_days"] = 14
                elif intent in {"supplier_risk", "shipments"}:
                    result["entities"]["supplier_id"] = canonical_entity("supplier_id", match[1])
                elif intent == "what_if":
                    percent, warehouse = (
                        (match[2], match[1]) if index == 2 else (match[1], match[2])
                    )
                    result["entities"]["warehouse_id"] = canonical_entity("warehouse_id", warehouse)
                    result["scenario"] = {"demand_increase": float(percent) / 100}
                return validate_query_plan(result)
            except (ValueError, ValidationError):
                # Unknown names, absent filters, and out-of-range values must clarify.
                return parse_semantics(question)
    return parse_semantics(question)


def compile_query_plan(value):
    """Only trusted literal templates become SQL. No model text is interpolated."""
    # The execution contract makes implicit legacy defaults explicit. V1 proposal
    # compatibility is retained for existing labels and model adapters.
    from sentinel.nlq.execution_contract import resolve_execution_plan, validate_execution_plan
    from sentinel.nlq.planner import plan
    from sentinel.nlq.sql_guard import guard_sql

    resolved = (
        validate_execution_plan(value)
        if isinstance(value, dict) and "contract_version" in value
        else resolve_execution_plan(value)
    )
    value = {key: resolved[key] for key in QUERY_PLAN_SCHEMA["properties"]}
    intent, entities = value["intent"], value["entities"]
    if value["abstain"]:
        return plan(intent, abstain=True, needs_clarification=intent == "unsupported")
    if intent == "supplier_delay":
        view = "shipment_view"
        sql = "SELECT supplier_id, supplier_name, count(is_late) AS evaluable_shipments, sum(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_shipments, avg(CASE WHEN is_late THEN 1.0 WHEN is_late = false THEN 0.0 END) AS late_delivery_rate FROM shipment_view WHERE promised_date >= $start_date AND promised_date < $end_date GROUP BY supplier_id, supplier_name HAVING count(is_late) > 0 ORDER BY late_delivery_rate DESC"
        params = {
            "start_date": value["time_range"]["start"],
            "end_date": value["time_range"]["end"],
        }
        if entities["supplier_id"] is not None:
            sql = sql.replace(" GROUP BY", " AND supplier_id = $supplier GROUP BY")
            params["supplier"] = entities["supplier_id"]
    elif intent == "stockout":
        view, sql, params = (
            "risk_view",
            "SELECT * FROM risk_view WHERE days_of_cover < $horizon ORDER BY days_of_cover",
            {"horizon": value["horizon_days"]},
        )
    elif intent == "forecast":
        view, sql = (
            "demand_view",
            "SELECT * FROM demand_view WHERE product_id = $product AND warehouse_id = $warehouse ORDER BY demand_date",
        )
        params = {"product": entities["product_id"], "warehouse": entities["warehouse_id"]}
    elif intent == "what_if":
        view = "risk_view"
        sql = "SELECT *, avg_daily_demand * (1 + $increase) AS scenario_daily_demand, days_of_cover / (1 + $increase) AS scenario_days_of_cover FROM risk_view WHERE warehouse_id = $warehouse"
        params = {
            "increase": value["scenario"]["demand_increase"],
            "warehouse": entities["warehouse_id"],
        }
    elif intent == "supplier_risk":
        view, sql, params = (
            "supplier_view",
            "SELECT * FROM supplier_view WHERE supplier_id = $supplier",
            {"supplier": entities["supplier_id"]},
        )
    elif intent == "shipments":
        view, sql, params = (
            "shipment_view",
            "SELECT * FROM shipment_view WHERE supplier_id = $supplier",
            {"supplier": entities["supplier_id"]},
        )
    else:
        view, sql, params = (
            "shipment_view",
            "SELECT * FROM shipment_view WHERE promised_date IS NULL",
            {},
        )
    guard_sql(sql, params)
    return plan(
        "shipments" if intent == "missing_dates" else intent,
        view,
        sql,
        params,
        value["evidence_requirements"],
    )
