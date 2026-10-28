from datetime import date

import pytest

from sentinel.application import Application
from sentinel.data.build_duckdb import build_database, synthetic_records
from sentinel.tasks import Task


@pytest.mark.parametrize("fault", ["trailing", "internal", "duplicate", "future", "short"])
def test_current_forecast_requires_complete_fresh_history(tmp_path, fault):
    rows = synthetic_records()
    selected = [x for x in rows["demand_daily"] if x[2:4] == ("P1", "W3")]
    others = [x for x in rows["demand_daily"] if x[2:4] != ("P1", "W3")]
    if fault == "trailing":
        selected = selected[:-7]
    if fault == "internal":
        selected = selected[:90] + selected[91:]
    if fault == "duplicate":
        selected.append(selected[-1])
    if fault == "future":
        x = selected[-1]
        selected.append(("future", date(2026, 10, 1), *x[2:]))
    if fault == "short":
        selected = selected[-7:]
    rows["demand_daily"] = others + selected
    app = Application(build_database(tmp_path / "a.duckdb", records=rows), tmp_path / "s.sqlite3")
    result = app.analyze(Task(kind="forecast", product_id="P1", warehouse_id="W3"))
    assert result["state"] == "uncertain" and not result["recommendations"]
    assert result["forecast_context"]["freshness_status"] != "current"


def test_origin_and_exact_prediction_boundaries(tmp_path):
    app = Application(build_database(tmp_path / "a.duckdb"), tmp_path / "s.sqlite3")
    result = app.analyze(Task(kind="forecast", product_id="P1", warehouse_id="W3"))
    assert result["forecast_context"] == {
        "reference_date": "2026-09-30",
        "last_observed_date": "2026-09-30",
        "forecast_origin": "2026-09-30",
        "prediction_start": "2026-10-01",
        "prediction_end": "2026-10-14",
        "freshness_status": "current",
    }
    assert [x["date"] for x in result["forecast"]["points"]] == [
        f"2026-10-{i:02}" for i in range(1, 15)
    ]
