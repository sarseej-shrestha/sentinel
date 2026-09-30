"""Normalize only independently generated synthetic entities; flag bad records."""

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
