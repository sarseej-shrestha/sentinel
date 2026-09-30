"""Semantic catalog shared by retrieval, planner and the SQL guard."""
from sentinel.data.build_duckdb import TABLES
from sentinel.data.normalize import PROVENANCE

SCHEMA = {
    "shipment_view": TABLES["shipments"] | PROVENANCE | {"supplier_name": "VARCHAR", "is_late": "BOOLEAN", "delay_days": "BIGINT"},
    "supplier_view": TABLES["suppliers"] | PROVENANCE | {"shipment_count": "BIGINT", "evaluable_shipments": "BIGINT", "missing_or_invalid_shipments": "BIGINT", "late_delivery_rate": "DOUBLE", "mean_delay_days": "DOUBLE", "recent_delay_events": "BIGINT"},
    "demand_view": TABLES["demand_daily"] | PROVENANCE | {"product_name": "VARCHAR", "warehouse_name": "VARCHAR", "day_of_week": "INTEGER", "is_weekend": "BOOLEAN"},
    "risk_view": {"product_id": "VARCHAR", "product_name": "VARCHAR", "warehouse_id": "VARCHAR", "warehouse_name": "VARCHAR", "supplier_id": "VARCHAR", "inventory_date": "DATE", "on_hand": "INTEGER", "reorder_point": "INTEGER", "avg_daily_demand": "DOUBLE", "history_days": "BIGINT", "days_of_cover": "DOUBLE", "inventory_age_days": "BIGINT"} | PROVENANCE,
}
METRICS = {
    "late_delivery_rate": "Late evaluable deliveries / all evaluable deliveries; NULL promised dates and invalid records excluded. Last month uses promised_date, from 2026-08-01 inclusive to 2026-09-01 exclusive.",
    "days_of_cover": "Latest on_hand / mean demand over the last 28 observed days. Zero demand yields NULL. Below 14 days triggers review, not a guaranteed stockout.",
    "supplier_reliability": "Historical evaluable delivery rate and sample count; observations are not future probabilities.",
    "what_if": "Scale demand by an explicit fraction, holding stock, prices and replenishment constant. A scenario is not a causal forecast.",
}
ALIASES = {"shipment_view": "late delivery delayed orders shipments promised ETA last month", "supplier_view": "supplier reliability high risk Supplier A why", "risk_view": "inventory stock stockout coverage warehouse what happens demand increases", "demand_view": "forecast demand daily units sales seasonality"}
JOINS = [{"left": "risk_view.supplier_id", "right": "supplier_view.supplier_id", "cardinality": "many-to-one"}, {"left": "shipment_view.supplier_id", "right": "supplier_view.supplier_id", "cardinality": "many-to-one"}]

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["intent", "view", "sql", "parameters", "evidence_fields", "needs_clarification", "abstain"],
    "properties": {
        "intent": {"enum": ["supplier_delay", "stockout", "what_if", "supplier_risk", "shipments", "forecast", "unsupported", "unsafe"]},
        "view": {"enum": [*SCHEMA, None]},
        "sql": {"type": ["string", "null"], "maxLength": 12000},
        "parameters": {"type": "object", "maxProperties": 10, "propertyNames": {"pattern": "^[a-z][a-z0-9_]*$"}, "additionalProperties": {"type": ["string", "number", "boolean", "null"]}},
        "evidence_fields": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "needs_clarification": {"type": "boolean"}, "abstain": {"type": "boolean"},
    },
}
