"""Regression checks at the SQL, evidence, model-output and human-review boundaries."""

import copy

import duckdb
import pytest

from sentinel.analytics.evidence import recommendation
from sentinel.audit.logger import ActionGate, AuditLog
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.executor import _worker, execute
from sentinel.nlq.planner import plan
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


@pytest.fixture
def database(tmp_path):
    return build_database(tmp_path / "contracts.duckdb")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders VALUES ('injected')",
        "UPDATE orders SET status = 'injected'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN injected INTEGER",
        "CREATE TABLE injected (id INTEGER)",
        "ATTACH 'injected.duckdb' AS injected",
        "COPY orders TO 'injected.csv'",
        "TRUNCATE orders",
        "LOAD httpfs",
        "SET enable_external_access = true",
        "CALL checkpoint()",
    ],
)
def test_all_write_statement_families_rejected(sql):
    with pytest.raises(SQLBlocked):
        guard_sql(sql)


def test_worker_is_read_only_even_without_ast_guard(database):
    class Capture:
        def send(self, value):
            self.value = value

        def close(self):
            pass

    capture = Capture()
    # This intentionally bypasses the AST to exercise the database's second defense.
    _worker(capture, str(database), "DELETE FROM orders", {}, 200)
    assert capture.value[0] == "error"
    assert "read-only" in capture.value[1].lower()
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 540


@pytest.mark.parametrize("damage", ["absent_rows", "empty_rows", "changed_rows", "changed_hash"])
def test_gate_requires_intact_evidence(database, damage):
    result = execute(database, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
    rec = recommendation(result)
    if damage == "absent_rows":
        del rec["evidence_rows"]
    elif damage == "empty_rows":
        rec["evidence_rows"] = []
    elif damage == "changed_rows":
        rec["evidence_rows"][0]["supplier_name"] = "Unverified supplier"
    else:
        rec["evidence_id"] = "unverified"
    audit = AuditLog(database)
    with pytest.raises(ValueError, match="evidence"):
        ActionGate(audit).propose(rec)
    assert audit.replay() == []


def test_whitespace_is_not_a_human_reviewer(database):
    audit = AuditLog(database)
    gate = ActionGate(audit)
    result = execute(database, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
    action = gate.propose(recommendation(result))
    with pytest.raises(ValueError):
        gate.decide(action["action_id"], "approve", "   ")
    assert gate.current(action["action_id"])["state"] == "pending"


class FixedPlanner:
    name = "contract_fixture"

    def __init__(self, output):
        self.output = output

    def generate(self, question, retrieved):
        return copy.deepcopy(self.output)


@pytest.mark.parametrize("intent", ["what_if", "forecast", "stockout"])
def test_incomplete_or_ill_typed_model_output_abstains(database, intent):
    if intent == "what_if":
        output = plan("what_if", "risk_view", "SELECT * FROM risk_view")
    elif intent == "forecast":
        output = plan(
            "forecast",
            "demand_view",
            "SELECT units FROM demand_view LIMIT 2",
            evidence_fields=["demand_date", "units"],
        )
    else:
        output = plan(
            "stockout",
            "risk_view",
            "SELECT 'unknown' AS days_of_cover, 0 AS inventory_age_days FROM risk_view LIMIT 1",
            evidence_fields=["days_of_cover", "inventory_age_days"],
        )
    console = Console(database, planner=FixedPlanner(output))
    record = console.question("Analyze the fixture")
    assert record["abstained"] and record["failure_behavior"]
    assert not record.get("recommendations")
    assert console.audit.replay()[-1]["payload"]["abstained"]


def test_replay_is_repeatable_and_does_not_apply_decisions_again(database):
    console = Console(database)
    record = console.question("Why is Supplier A considered high risk?")
    rec = record["recommendations"][0]
    assert rec["evidence_rows"] == record["query_result"]["rows"]
    action = console.gate.propose(rec)
    console.gate.decide(action["action_id"], "approve", "test_reviewer")
    first = console.audit.replay()
    assert console.audit.replay() == first
    assert first[-1]["payload"]["external_action_executed"] is False
    with pytest.raises(ValueError, match="pending"):
        console.gate.decide(action["action_id"], "approve", "test_reviewer")
    assert console.audit.replay() == first


def test_risk_queue_and_what_if_retain_actual_query_evidence(database):
    console = Console(database)
    queue = console.question("Show products likely to stock out within the next 14 days.")
    assert queue["status"] == "ok" and queue["risks"]
    assert len(queue["risks"]) == len(queue["query_result"]["rows"])
    for risk, rec in zip(queue["risks"], queue["recommendations"]):
        assert all(row in queue["query_result"]["rows"] for row in risk["evidence_rows"])
        assert rec["evidence_id"] == queue["query_result"]["evidence_id"]
        assert "confidence" not in risk and "probability" not in risk
    scenario = console.question("What happens if demand increases by 15% at Warehouse 3?")
    assert scenario["status"] == "ok" and len(scenario["what_if"]) == 6
    for row in scenario["what_if"]:
        evidence = row["evidence_rows"][0]
        assert evidence in scenario["query_result"]["rows"]
        assert row["scenario_daily_demand"] == pytest.approx(evidence["avg_daily_demand"] * 1.15)
        assert row["simulated_action_only"] is True
