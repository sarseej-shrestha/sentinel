"""Generate a reproducible synthetic database. Raw research data is never loaded."""
import argparse
from datetime import date, timedelta
from pathlib import Path
import random

import duckdb

from sentinel.config import AS_OF, DATABASE, SEED
from sentinel.data.normalize import PROVENANCE, normalize, flag_quality

TABLES = {
    "orders": {"order_id": "VARCHAR", "order_date": "DATE", "warehouse_id": "VARCHAR", "status": "VARCHAR"},
    "order_items": {"item_id": "VARCHAR", "order_id": "VARCHAR", "product_id": "VARCHAR", "quantity": "INTEGER", "unit_price": "DOUBLE"},
    "shipments": {"shipment_id": "VARCHAR", "order_id": "VARCHAR", "supplier_id": "VARCHAR", "warehouse_id": "VARCHAR", "shipped_date": "DATE", "promised_date": "DATE", "delivered_date": "DATE", "distance_km": "DOUBLE", "traffic_index": "DOUBLE"},
    "products": {"product_id": "VARCHAR", "product_name": "VARCHAR", "supplier_id": "VARCHAR", "unit_price": "DOUBLE"},
    "suppliers": {"supplier_id": "VARCHAR", "supplier_name": "VARCHAR", "lead_time_days": "INTEGER"},
    "warehouses": {"warehouse_id": "VARCHAR", "warehouse_name": "VARCHAR"},
    "inventory_daily": {"inventory_id": "VARCHAR", "inventory_date": "DATE", "product_id": "VARCHAR", "warehouse_id": "VARCHAR", "on_hand": "INTEGER", "reorder_point": "INTEGER"},
    "demand_daily": {"demand_id": "VARCHAR", "demand_date": "DATE", "product_id": "VARCHAR", "warehouse_id": "VARCHAR", "units": "DOUBLE"},
    "purchase_orders": {"purchase_order_id": "VARCHAR", "supplier_id": "VARCHAR", "product_id": "VARCHAR", "warehouse_id": "VARCHAR", "quantity": "INTEGER", "expected_date": "DATE", "status": "VARCHAR"},
    "supplier_events": {"event_id": "VARCHAR", "supplier_id": "VARCHAR", "event_date": "DATE", "event_type": "VARCHAR", "delay_days": "INTEGER"},
    "promotions": {"promotion_id": "VARCHAR", "product_id": "VARCHAR", "start_date": "DATE", "end_date": "DATE", "discount_fraction": "DOUBLE"},
    "calendar": {"calendar_date": "DATE", "day_of_week": "INTEGER", "is_weekend": "BOOLEAN"},
    "risk_events": {"risk_event_id": "VARCHAR", "entity_id": "VARCHAR", "risk_type": "VARCHAR", "risk_level": "VARCHAR", "detected_at": "TIMESTAMP"},
    "audit_events": {"event_id": "VARCHAR", "event_type": "VARCHAR", "actor": "VARCHAR", "payload": "VARCHAR", "previous_hash": "VARCHAR", "event_hash": "VARCHAR"},
}

VIEWS = {
"shipment_view": """
 SELECT sh.*, s.supplier_name,
 CASE WHEN sh.promised_date IS NULL OR sh.delivered_date IS NULL OR sh.data_quality_flag <> 'ok'
      THEN NULL ELSE sh.delivered_date > sh.promised_date END AS is_late,
 CASE WHEN sh.data_quality_flag = 'ok' THEN date_diff('day', sh.promised_date, sh.delivered_date) END AS delay_days
 FROM shipments sh LEFT JOIN suppliers s USING(supplier_id)
""",
"supplier_view": """
 SELECT s.supplier_id, s.supplier_name, s.lead_time_days,
 count(sh.shipment_id) AS shipment_count, count(sh.is_late) AS evaluable_shipments,
 count(sh.shipment_id)-count(sh.is_late) AS missing_or_invalid_shipments,
 avg(CASE WHEN sh.is_late THEN 1.0 WHEN sh.is_late = false THEN 0.0 END) AS late_delivery_rate,
 avg(sh.delay_days) AS mean_delay_days,
 coalesce(e.recent_delay_events, 0) AS recent_delay_events,
 s.source_dataset, s.source_record_id, s.synthetic_entity_id, s.data_quality_flag,
 s.missing_field_count, s.record_timestamp
 FROM suppliers s LEFT JOIN shipment_view sh USING(supplier_id)
 LEFT JOIN (SELECT supplier_id, count(*) AS recent_delay_events FROM supplier_events
 WHERE event_type = 'delay' AND event_date >= DATE '{as_of}' - INTERVAL 7 DAY
 AND event_date <= DATE '{as_of}' GROUP BY supplier_id) e USING(supplier_id)
 GROUP BY ALL
""",
"demand_view": """
 SELECT d.*, p.product_name, w.warehouse_name, c.day_of_week, c.is_weekend
 FROM demand_daily d LEFT JOIN products p USING(product_id)
 LEFT JOIN warehouses w USING(warehouse_id) LEFT JOIN calendar c ON c.calendar_date = d.demand_date
""",
"risk_view": """
 WITH recent_demand AS (
 SELECT product_id, warehouse_id, avg(units) AS avg_daily_demand, count(*) AS history_days
 FROM demand_daily WHERE demand_date > DATE '{as_of}' - INTERVAL 28 DAY AND demand_date <= DATE '{as_of}'
 GROUP BY product_id, warehouse_id), latest AS (
 SELECT *, row_number() OVER (PARTITION BY product_id, warehouse_id ORDER BY inventory_date DESC) AS rn FROM inventory_daily)
 SELECT i.product_id, p.product_name, i.warehouse_id, w.warehouse_name, p.supplier_id,
 i.inventory_date, i.on_hand, i.reorder_point, d.avg_daily_demand, d.history_days,
 i.on_hand / nullif(d.avg_daily_demand, 0) AS days_of_cover,
 date_diff('day', i.inventory_date, DATE '{as_of}') AS inventory_age_days,
 i.source_dataset, i.source_record_id, i.synthetic_entity_id, i.data_quality_flag, i.missing_field_count, i.record_timestamp
 FROM latest i LEFT JOIN products p USING(product_id) LEFT JOIN warehouses w USING(warehouse_id)
 LEFT JOIN recent_demand d USING(product_id, warehouse_id) WHERE i.rn = 1
""",
}


def synthetic_records(seed=SEED, as_of=AS_OF):
    rng = random.Random(seed)
    end = date.fromisoformat(as_of)
    rows = {table: [] for table in TABLES}
    rows["suppliers"] = [(f"S{i}", f"Supplier {chr(64+i)}", 5+i*2) for i in range(1, 4)]
    rows["warehouses"] = [(f"W{i}", f"Warehouse {i}") for i in range(1, 4)]
    rows["products"] = [(f"P{i}", f"Synthetic Product {i}", f"S{(i-1)%3+1}", float(10+i*3)) for i in range(1, 7)]
    for day in range(180):
        dt = end - timedelta(days=179-day)
        rows["calendar"].append((dt, dt.weekday(), dt.weekday() >= 5))
        for p in range(1, 7):
            for w in range(1, 4):
                units = max(0, round(8+p*2+w + (6 if dt.weekday() >= 5 else 0) + rng.gauss(0, 3)))
                rows["demand_daily"].append((f"D{day}-{p}-{w}", dt, f"P{p}", f"W{w}", units))
                stock = 40+p*35+w*8 + rng.randint(-15, 15)
                rows["inventory_daily"].append((f"I{day}-{p}-{w}", dt, f"P{p}", f"W{w}", stock, 70))
        for j in range(3):
            i = day*3+j
            supplier = j+1
            shipped = dt-timedelta(days=10)
            promised = dt-timedelta(days=3)
            late = rng.random() < (0.65 if supplier == 1 else 0.15)
            delivered = promised+timedelta(days=rng.randint(1, 3) if late else -rng.randint(0, 2))
            rows["orders"].append((f"O{i}", shipped-timedelta(days=1), f"W{supplier}", "delivered"))
            rows["order_items"].append((f"OI{i}", f"O{i}", f"P{j+1}", rng.randint(1, 8), float(13+j*3)))
            rows["shipments"].append((f"SH{i}", f"O{i}", f"S{supplier}", f"W{supplier}", shipped, promised, delivered, rng.uniform(80, 800), rng.random()))
    rows["purchase_orders"] = [(f"PO{i}", f"S{i}", f"P{i}", f"W{i}", 100, end+timedelta(days=7), "simulated_open") for i in range(1, 4)]
    rows["supplier_events"] = [("SE1", "S1", end, "delay", 3)]
    rows["promotions"] = [("PR1", "P1", end-timedelta(days=10), end, 0.1)]
    return rows


def build_database(path=DATABASE, *, records=None, replace=False):
    path = Path(path)
    if path.exists() and not replace:
        raise FileExistsError(f"{path} already exists; use --replace to rebuild this synthetic demo.")
    path.parent.mkdir(parents=True, exist_ok=True)
    records = synthetic_records() if records is None else records
    frames = flag_quality({name: normalize(name, records[name], columns) for name, columns in TABLES.items()})
    # Build separately so an error cannot leave a half-built canonical database.
    import tempfile
    with tempfile.TemporaryDirectory(dir=path.parent) as scratch:
        temporary = Path(scratch) / "build.duckdb"
        with duckdb.connect(str(temporary)) as con:
            for name, columns in TABLES.items():
                all_columns = columns | PROVENANCE
                con.execute(f"CREATE TABLE {name} (" + ", ".join(f"{key} {kind}" for key, kind in all_columns.items()) + ")")
                frame = frames[name][list(all_columns)]
                con.register("incoming", frame)
                con.execute(f"INSERT INTO {name} SELECT * FROM incoming")
                con.unregister("incoming")
            for name, query in VIEWS.items():
                con.execute(f"CREATE VIEW {name} AS " + query.format(as_of=AS_OF))
        temporary.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DATABASE)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    print(build_database(args.path, replace=args.replace))


if __name__ == "__main__":
    main()
