"""Release scoring must use only complete, eligible daily series."""

from datetime import date, timedelta

import pytest

from scripts import evaluate_release
from sentinel.data.build_duckdb import build_database, synthetic_records


@pytest.mark.parametrize("fault", ["gap", "duplicate", "future", "missing_series"])
def test_release_forecast_reports_excluded_series(tmp_path, monkeypatch, fault):
    rows = synthetic_records()
    selected = next(
        r for r in rows["demand_daily"] if r[2:4] == ("P1", "W3") and r[1] == date(2026, 8, 1)
    )
    if fault == "gap":
        rows["demand_daily"].remove(selected)
    elif fault == "missing_series":
        rows["demand_daily"] = [row for row in rows["demand_daily"] if row[2:4] != ("P1", "W3")]
    elif fault == "duplicate":
        rows["demand_daily"].append(("duplicate-demand", *selected[1:]))
    else:
        rows["demand_daily"].append(
            ("future-demand", date(2026, 9, 30) + timedelta(days=1), "P1", "W3", 5)
        )

    def simple_forecast(history, horizon):
        values = [history[-7 + i % 7] for i in range(horizon)]
        return {"point": values, "baseline": values}

    monkeypatch.setattr(evaluate_release, "forecast", simple_forecast)
    report = evaluate_release.forecast_evaluation(
        build_database(tmp_path / "evaluation.duckdb", records=rows)
    )
    assert report["series_count"] == 18
    assert report["eligible_series"] == 17
    assert report["excluded_series"]["P1/W3"]
    assert "P1/W3" not in report["summary"]["final_chronological_block"]["per_series"]
    assert "P2/W1" in report["summary"]["final_chronological_block"]["per_series"]


def test_release_forecast_reports_zero_eligible_series(tmp_path):
    rows = synthetic_records()
    rows["demand_daily"] = []
    report = evaluate_release.forecast_evaluation(
        build_database(tmp_path / "evaluation.duckdb", records=rows)
    )
    assert report["series_count"] == 18
    assert report["eligible_series"] == 0
    assert len(report["excluded_series"]) == 18
    assert report["summary"]["final_chronological_block"]["forecast"] == {
        "n": 0,
        "mae": None,
        "rmse": None,
    }
