"""Independent fixtures for population completeness, parent ambiguity and event validity."""

from datetime import date

from sentinel.application import Application
from sentinel.data.build_duckdb import build_database, synthetic_records
from sentinel.tasks import Task


def run(tmp_path, mutate, **task):
    rows = synthetic_records()
    mutate(rows)
    return Application(
        build_database(tmp_path / "a.duckdb", records=rows), tmp_path / "s.sqlite3"
    ).analyze(Task(**task))


def test_missing_entire_demand_series_remains_in_population(tmp_path):
    def mutate(r):
        r["demand_daily"] = [x for x in r["demand_daily"] if (x[2], x[3]) != ("P1", "W3")]

    result = run(tmp_path, mutate, kind="stockout", horizon_days=14)
    q = result["quality"]
    assert q["entities"] == {
        "total": 18,
        "assessed": 17,
        "matching": 17,
        "not_matching": 0,
        "unevaluable": 1,
    }
    assert q["missing_observations"] == 28
    assert result["state"] == "success" and len(result["recommendations"]) == 17


def test_zero_evaluable_supplier_is_not_lost(tmp_path):
    def mutate(r):
        r["shipments"] = [x[:6] + (None,) + x[7:] if x[2] == "S1" else x for x in r["shipments"]]

    result = run(
        tmp_path, mutate, kind="supplier_delay", start_date="2026-08-01", end_date="2026-08-31"
    )
    assert result["quality"]["entities"]["unevaluable"] == 1
    assert result["quality"]["entities"]["assessed"] == 2
    assert result["quality"]["observations"]["shipment"]["excluded"] == 31


def test_entirely_unevaluable_is_uncertain(tmp_path):
    def mutate(r):
        r["shipments"] = [x[:6] + (None,) + x[7:] for x in r["shipments"]]

    result = run(
        tmp_path, mutate, kind="supplier_delay", start_date="2026-08-01", end_date="2026-08-31"
    )
    assert result["state"] == "uncertain" and not result["recommendations"]
    assert result["quality"]["entities"]["unevaluable"] == 3


def test_duplicate_parent_cannot_multiply_facts(tmp_path):
    def mutate(r):
        r["suppliers"].append(("S1", "Conflicting supplier name", 99))

    result = run(
        tmp_path, mutate, kind="supplier_delay", start_date="2026-08-01", end_date="2026-08-31"
    )
    assert result["quality"]["entities"]["unevaluable"] == 1
    assert result["quality"]["observations"]["shipment"]["excluded"] == 31
    assert all(x["supplier_id"] != "S1" for x in result["rows"])


def test_invalid_events_never_raise_review_level(tmp_path):
    def mutate(r):
        r["supplier_events"] += [
            (f"BAD{i}", "S2", date(2026, 9, 30), "delay", -5) for i in range(3)
        ]

    result = run(tmp_path, mutate, kind="supplier_risk", supplier_id="S2")
    assert result["rows"][0]["recent_delay_events"] == 0
    assert result["quality"]["observations"]["supplier_event"]["excluded"] == 3
    assert result["rows"][0]["review_level"] != "high"


def test_unassignable_dates_are_not_assigned_to_period(tmp_path):
    def mutate(r):
        x = r["shipments"][0]
        r["shipments"][0] = x[:5] + (None,) + x[6:]

    result = run(
        tmp_path, mutate, kind="supplier_delay", start_date="2026-08-01", end_date="2026-08-31"
    )
    assert result["quality"]["observations"]["shipment"]["unassignable"] == 1
    assert sum(x["evaluable_shipments"] for x in result["rows"]) == 93
