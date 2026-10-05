"""Small synthetic catalog. Retrieval can supply candidates, not authority."""

import re

from sentinel.nlq.schema import SCHEMA

ENTITIES = {
    "supplier_id": {f"S{i}": [f"S{i}", f"Supplier {chr(64 + i)}"] for i in range(1, 4)},
    "warehouse_id": {f"W{i}": [f"W{i}", f"Warehouse {i}", f"Warehouse W{i}"] for i in range(1, 4)},
    "product_id": {
        f"P{i}": [f"P{i}", f"Product {i}", f"Product P{i}", f"Synthetic Product {i}"]
        for i in range(1, 7)
    },
}

METRICS = {
    "late_delivery_rate": {
        "aliases": ["late deliveries", "late delivery rate", "late arrival share", "lateness"],
        "view": "shipment_view",
        "columns": ["supplier_id", "promised_date", "is_late"],
        "definition": "Count of is_late=true divided by count(is_late), grouped by supplier and filtered by promised_date. NULL is_late observations are excluded from both counts.",
    },
    "days_of_cover": {
        "aliases": ["running out", "stockout risk", "days of cover"],
        "view": "risk_view",
        "columns": ["on_hand", "avg_daily_demand", "days_of_cover"],
    },
    "units": {
        "aliases": ["daily demand", "demand units"],
        "view": "demand_view",
        "columns": ["demand_date", "units"],
    },
    "supplier_reliability": {
        "aliases": ["supplier evidence", "supplier reliability"],
        "view": "supplier_view",
        "columns": ["late_delivery_rate", "evaluable_shipments"],
    },
    "scenario_days_of_cover": {
        "aliases": ["demand what if", "scenario coverage"],
        "view": "risk_view",
        "columns": ["days_of_cover", "avg_daily_demand", "on_hand"],
    },
}
DIMENSIONS = {
    key: [name for name, columns in SCHEMA.items() if key in columns]
    for key in ("supplier_id", "warehouse_id", "product_id", "demand_date", "shipment_id")
}
EVIDENCE_FIELDS = {key for columns in SCHEMA.values() for key in columns} | {
    "late_shipments",
    "evaluable_shipments",
    "late_delivery_rate",
}


def normalized(value):
    if not isinstance(value, str):
        raise ValueError("Catalog values must be strings")
    return re.sub(r"\s+", " ", value.strip().casefold().replace("-", " "))


def resolve_entity(kind, value, candidates=()):
    if value is None:
        return None
    catalog = ENTITIES[kind]
    key = normalized(value)
    exact = [identifier for identifier in catalog if key == identifier.casefold()]
    if exact:
        return exact[0]
    aliases = [
        identifier for identifier, names in catalog.items() if key in {normalized(n) for n in names}
    ]
    if len(aliases) == 1:
        return aliases[0]
    # Retrieval-assisted matching is permitted only with lexical catalog evidence.
    # A nearest embedding alone cannot map an unknown company or product name.
    stripped = re.sub(r"^(?:the|named)\s+", "", key)
    if not candidates:
        candidates = retrieve_entity_candidates(kind, stripped)
    matches = {
        identifier
        for identifier in candidates
        if identifier in catalog and stripped in {normalized(n) for n in catalog[identifier]}
    }
    if len(matches) == 1:
        return matches.pop()
    raise ValueError(f"Unknown or ambiguous {kind}: {value}")


def retrieve_entity_candidates(kind, text):
    """Lexical catalog retrieval; ambiguity is retained, never top-one guessed."""
    tokens = set(normalized(text).split())
    return [
        identifier
        for identifier, names in ENTITIES[kind].items()
        if any(tokens <= set(normalized(name).split()) for name in names)
    ]


def resolve_metric(value):
    key = normalized(value)
    if value in METRICS:
        return value
    found = [
        name for name, spec in METRICS.items() if key in {normalized(a) for a in spec["aliases"]}
    ]
    if len(found) != 1:
        raise ValueError(f"Unknown or ambiguous metric: {value}")
    return found[0]


def validate_registry():
    return all(set(spec["columns"]) <= set(SCHEMA[spec["view"]]) for spec in METRICS.values())
