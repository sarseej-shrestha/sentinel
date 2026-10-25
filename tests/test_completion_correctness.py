"""Release regressions: never accept a known wrong interpretation or bad observation."""

from datetime import date

import duckdb
import pytest

from sentinel.data.build_duckdb import TABLES, build_database, synthetic_records
from sentinel.data.normalize import flag_quality, normalize
from sentinel.nlq.authority import resolve
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


@pytest.mark.parametrize(
    "question",
    [
        "Show products unlikely to stock out within the next 14 days",
        "Show products whose stock lasts 14 days",
        "What if demand increases to 15 percent at Warehouse 3",
        "What if demand increases by 2 times at Warehouse 3",
        "Show products likely to stock out within the next 14 days at Warehouse 3",
        "Forecast demand for Product P99 at Warehouse 3",
        "Forecast demand",
    ],
)
def test_unsupported_semantics_never_execute(question):
    assert resolve(question)["state"] != "supported"


def test_positive_percentage_and_inclusive_boundary():
    result = resolve("What happens if demand increases by 15% at Warehouse 3?")
    assert result["plan"]["scenario"] == {"demand_increase": 0.15}
    result = resolve("Show Supplier A late delivery rate between 2026-08-01 and 2026-08-31")
    assert result["plan"]["time_range"] == {"start": "2026-08-01", "end": "2026-09-01"}


def test_disconnected_join_is_rejected():
    with pytest.raises(SQLBlocked):
        guard_sql(
            "SELECT r.product_id FROM risk_view r JOIN supplier_view s "
            "ON r.supplier_id=s.supplier_id JOIN shipment_view sh "
            "ON r.supplier_id=s.supplier_id"
        )


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("demand_daily", "units", -1),
        ("demand_daily", "units", float("inf")),
        ("inventory_daily", "inventory_date", date(2026, 10, 5)),
        ("inventory_daily", "on_hand", -1),
        ("inventory_daily", "warehouse_id", "UNKNOWN"),
    ],
)
def test_invalid_observations_are_flagged(table, field, value):
    rows = synthetic_records()
    row = dict(zip(TABLES[table], rows[table][-1]))
    row[field] = value
    rows[table][-1] = tuple(row.values())
    frames = flag_quality({n: normalize(n, rows[n], cols) for n, cols in TABLES.items()})
    assert frames[table].iloc[-1]["data_quality_flag"] != "ok"


def test_bad_demand_propagates_to_coverage(tmp_path):
    rows = synthetic_records()
    row = list(rows["demand_daily"][-1])
    row[-1] = -1000
    rows["demand_daily"][-1] = tuple(row)
    db = build_database(tmp_path / "bad.duckdb", records=rows)
    with duckdb.connect(str(db), read_only=True) as con:
        row = con.execute(
            "SELECT data_quality_flag, excluded_demand_count FROM risk_view "
            "WHERE product_id='P6' AND warehouse_id='W3'"
        ).fetchone()
    assert row[0] != "ok" and row[1] == 1


def test_future_promised_arrival_is_not_future_observation():
    rows = synthetic_records()
    frames = flag_quality({n: normalize(n, rows[n], cols) for n, cols in TABLES.items()})
    assert set(frames["purchase_orders"]["data_quality_flag"]) == {"ok"}


def test_duplicate_daily_key_is_flagged():
    rows = synthetic_records()
    rows["demand_daily"].append(rows["demand_daily"][-1])
    frames = flag_quality({n: normalize(n, rows[n], cols) for n, cols in TABLES.items()})
    assert "duplicate" in frames["demand_daily"].iloc[-1]["data_quality_flag"]
