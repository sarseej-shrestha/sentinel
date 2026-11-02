"""Completed-delivery metrics must not imply known outstanding status."""

from sentinel.application import Application
from sentinel.data.build_duckdb import build_database, synthetic_records
from sentinel.tasks import Task


def test_missing_delivery_date_does_not_claim_known_outstanding_count(tmp_path):
    rows = synthetic_records()
    selected = next(r for r in rows["shipments"] if r[2] == "S1" and r[5].month == 8)
    rows["shipments"] = [r[:6] + (None,) + r[7:] if r == selected else r for r in rows["shipments"]]
    service = Application(
        build_database(tmp_path / "analytics.duckdb", records=rows), tmp_path / "state.sqlite3"
    )
    result = service.analyze(
        Task(kind="supplier_delay", start_date="2026-08-01", end_date="2026-08-31")
    )
    assert result["quality"]["observations"]["shipment"]["excluded"] >= 1
    assert any(
        "outstanding" in warning.lower()
        and "unknown" in warning.lower()
        and "receipt" in warning.lower()
        for warning in result["warnings"]
    )
