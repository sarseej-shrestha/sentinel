"""Normalize only independently generated synthetic entities; flag bad records."""

import numpy as np
import pandas as pd

from sentinel.config import AS_OF

PROVENANCE = {
    "source_dataset": "VARCHAR",
    "source_record_id": "VARCHAR",
    "synthetic_entity_id": "VARCHAR",
    "data_quality_flag": "VARCHAR",
    "missing_field_count": "INTEGER",
    "record_timestamp": "TIMESTAMP",
}


def normalize(table, records, columns, as_of=AS_OF):
    frame = pd.DataFrame(records, columns=columns)
    frame["missing_field_count"] = frame.isna().sum(axis=1)
    frame["data_quality_flag"] = frame["missing_field_count"].map(
        lambda n: "missing_fields" if n else "ok"
    )
    frame["source_dataset"] = "sentinel_synthetic_v1"
    frame["source_record_id"] = [f"{table}:{i}" for i in range(len(frame))]
    frame["synthetic_entity_id"] = [f"syn:{table}:{i}" for i in range(len(frame))]
    frame["record_timestamp"] = as_of + " 12:00:00"
    return frame


def flag_quality(frames, as_of=AS_OF):
    def flag(table, mask, reason):
        frame = frames[table]
        frame.loc[mask, "data_quality_flag"] = frame.loc[mask, "data_quality_flag"].map(
            lambda v: reason if v == "ok" else v + ";" + reason
        )

    # Gross demand is consumption, not an adjustment ledger. Negative values are invalid.
    from sentinel.data.build_duckdb import TABLES

    references = {
        "supplier_id": ("suppliers", "supplier_id"),
        "product_id": ("products", "product_id"),
        "warehouse_id": ("warehouses", "warehouse_id"),
        "order_id": ("orders", "order_id"),
    }
    observations = {
        "inventory_date",
        "demand_date",
        "event_date",
        "delivered_date",
        "shipped_date",
        "order_date",
    }
    for table, frame in frames.items():
        if frame.empty:
            continue
        primary = next(iter(TABLES[table]))
        flag(table, frame.duplicated(primary, keep=False), "duplicate_record")
        for field, kind in TABLES[table].items():
            if kind in {"INTEGER", "DOUBLE"}:
                values = pd.to_numeric(frame[field], errors="coerce")
                invalid = ~np.isfinite(values) | (values < 0)
                if kind == "INTEGER":
                    invalid |= values.mod(1).ne(0)
                if field in {"traffic_index", "discount_fraction"}:
                    invalid |= values > 1
                flag(table, invalid, "invalid_" + field)
                frame.loc[~np.isfinite(values), field] = None
            if kind == "DATE":
                parsed = pd.to_datetime(frame[field], errors="coerce")
                flag(table, parsed.isna(), "invalid_" + field)
                if field in observations:
                    flag(table, parsed > pd.Timestamp(as_of), "future_" + field)
                frame[field] = parsed.dt.date
            if field in references and table != references[field][0]:
                target, key = references[field]
                flag(
                    table,
                    ~frame[field].isin(frames[target][key]),
                    "unknown_" + field.removesuffix("_id"),
                )
        if table in {"inventory_daily", "demand_daily"}:
            day = "inventory_date" if table == "inventory_daily" else "demand_date"
            flag(
                table,
                frame.duplicated(["product_id", "warehouse_id", day], keep=False),
                "duplicate_daily_observation",
            )

    suppliers = set(frames["suppliers"]["supplier_id"])
    products = set(frames["products"]["product_id"])
    for table in ("products", "shipments", "purchase_orders", "supplier_events"):
        flag(table, ~frames[table]["supplier_id"].isin(suppliers), "unknown_supplier")
    for table in (
        "order_items",
        "inventory_daily",
        "demand_daily",
        "purchase_orders",
        "promotions",
    ):
        flag(table, ~frames[table]["product_id"].isin(products), "unknown_product")
    shipments = frames["shipments"]
    flag("shipments", shipments.duplicated("shipment_id", keep=False), "duplicate_shipment")
    flag(
        "shipments",
        pd.to_datetime(shipments["delivered_date"]) < pd.to_datetime(shipments["shipped_date"]),
        "impossible_delivery_date",
    )
    flag(
        "inventory_daily",
        pd.to_datetime(frames["inventory_daily"]["inventory_date"])
        < pd.Timestamp(as_of) - pd.Timedelta(days=3),
        "stale_inventory",
    )
    return frames
