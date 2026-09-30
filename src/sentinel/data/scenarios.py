"""Small mutations of reproducible synthetic fixtures; no research records."""

import argparse
from datetime import date, timedelta
from pathlib import Path

from sentinel.config import AS_OF
from sentinel.data.build_duckdb import TABLES, build_database, synthetic_records

DATA_SCENARIOS = (
    "missing_supplier",
    "missing_promised_date",
    "duplicate_shipment",
    "impossible_delivery_date",
    "unknown_product",
    "stale_inventory",
    "demand_spike",
    "supplier_delay_burst",
)


def scenario_records(name):
    if name not in DATA_SCENARIOS:
        raise ValueError(f"Unknown data scenario: {name}")
    rows = synthetic_records()

    def change(table, index, **values):
        row = dict(zip(TABLES[table], rows[table][index]))
        row.update(values)
        rows[table][index] = tuple(row.values())

    if name == "missing_supplier":
        change("shipments", 0, supplier_id=None)
    elif name == "missing_promised_date":
        change("shipments", 0, promised_date=None)
    elif name == "duplicate_shipment":
        rows["shipments"].append(rows["shipments"][0])
    elif name == "impossible_delivery_date":
        change("shipments", 0, delivered_date=date(2020, 1, 1))
    elif name == "unknown_product":
        change("inventory_daily", -1, product_id="P_UNKNOWN")
    elif name == "stale_inventory":
        cutoff = date.fromisoformat(AS_OF) - timedelta(days=10)
        rows["inventory_daily"] = [row for row in rows["inventory_daily"] if row[1] <= cutoff]
    elif name == "demand_spike":
        for i in range(len(rows["demand_daily"]) - 18, len(rows["demand_daily"])):
            change("demand_daily", i, units=rows["demand_daily"][i][-1] * 4)
    elif name == "supplier_delay_burst":
        for i in range(7):
            rows["supplier_events"].append(
                (f"BURST{i}", "S1", date.fromisoformat(AS_OF) - timedelta(days=i), "delay", 5 + i)
            )
        for i in range(len(rows["shipments"]) - 21, len(rows["shipments"]), 3):
            change(
                "shipments",
                i,
                promised_date=date.fromisoformat(AS_OF) - timedelta(days=8),
                delivered_date=date.fromisoformat(AS_OF),
            )
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name", choices=DATA_SCENARIOS)
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    print(build_database(args.path, records=scenario_records(args.name)))
