import copy

import pytest
from jsonschema import validate

from sentinel.analytics.evidence import recommendation
from sentinel.analytics.forecast import forecast, forecast_from_result, seasonal_naive, walk_forward
from sentinel.analytics.risk import RISK_SCHEMA, RiskEngine
from sentinel.analytics.what_if import demand_scenario
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.executor import execute


@pytest.fixture(scope="module")
def engine():
    return RiskEngine().fit()


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("analytics") / "demo.duckdb")


@pytest.mark.parametrize(
    "kind,row",
    [
        ("stockout", {"days_of_cover": 5, "inventory_age_days": 0}),
        ("late_delivery", {"distance_km": 500, "traffic_index": 0.9}),
        ("supplier_reliability", {"late_delivery_rate": 0.6, "evaluable_shipments": 50}),
    ],
)
def test_calibrated_risk_schema(engine, kind, row):
    result = engine.assess(kind, row)
    validate(result, RISK_SCHEMA)
    assert result["risk_level"] == "high" and result["recommended_review"]
    assert "probability" not in result
    assert 0 <= engine.metrics[kind]["test_brier_score"] <= 1


def test_missing_and_stale_risk(engine):
    for row in ({"days_of_cover": None}, {"days_of_cover": 3, "inventory_age_days": 10}):
        result = engine.assess("stockout", row)
        assert result["risk_level"] == "unknown" and result["missing_information"]


def test_forecast_intervals_and_walk_forward():
    history = [float(10 + i % 7) for i in range(126)]
    result = forecast(history)
    assert len(result["point"]) == 14
    assert all(
        0 <= lo <= mid <= hi
        for lo, mid, hi in zip(result["lower"], result["point"], result["upper"])
    )
    evaluation = walk_forward(history)
    assert len(evaluation["folds"]) == 3
    assert all(f["train_end_index"] < f["test_start_index"] for f in evaluation["folds"])
    assert evaluation["mean_mae"] >= 0


def test_sparse_history_and_demand_spike():
    sparse = forecast([3, 4])
    assert sparse["method"] == "seasonal_naive"
    assert sparse["lower"] == [None] * 14
    spike = forecast([10] * 100 + [50] * 7)
    assert any("spike" in warning for warning in spike["warnings"])
    assert seasonal_naive([1, 2, 3, 4, 5, 6, 7], 8) == [1, 2, 3, 4, 5, 6, 7, 1]


def test_evidence_traceability_and_tampering(database, engine):
    result = execute(
        database,
        "SELECT * FROM risk_view WHERE product_id = $p AND warehouse_id = $w",
        {"p": "P1", "w": "W3"},
    )
    risk = engine.assess("stockout", result.rows[0])
    rec = recommendation(result, risk)
    assert rec["evidence_id"] == result.evidence_id and rec["simulated_action_only"]
    altered = copy.deepcopy(result)
    altered.rows[0]["on_hand"] = 9999
    with pytest.raises(ValueError, match="changed"):
        recommendation(altered)
    scenario = demand_scenario(result.rows[0])
    assert scenario["scenario_daily_demand"] == pytest.approx(
        result.rows[0]["avg_daily_demand"] * 1.15
    )


def test_forecast_input_traceability(database):
    result = execute(
        database,
        "SELECT * FROM demand_view WHERE product_id = $p AND warehouse_id = $w ORDER BY demand_date",
        {"p": "P1", "w": "W3"},
    )
    forecast_result = forecast_from_result(result)
    assert forecast_result["evidence_id"] == result.evidence_id
    assert recommendation(result, forecast_output=forecast_result)["simulated_action_only"]
