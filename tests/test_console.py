import json
import subprocess
import sys

import pytest

from sentinel.console import Console
from sentinel.data.build_duckdb import build_database


def test_console_forecast_and_audit(tmp_path):
    path = build_database(tmp_path / "console.duckdb")
    console = Console(path)
    response = console.question("Forecast demand for Product P1 at Warehouse 3")
    assert response["status"] == "ok" and len(response["forecast"]["point"]) == 14
    assert response["recommendations"][0]["evidence_id"] == response["forecast"]["evidence_id"]
    blocked = console.question("Delete all delayed orders.")
    assert blocked["status"] == "blocked"
    assert [event["event_type"] for event in console.audit.replay()] == [
        "query",
        "analysis",
        "query",
    ]


def test_cli_proposal_decision_and_replay(tmp_path):
    path = build_database(tmp_path / "cli.duckdb")

    def run(*args):
        output = subprocess.run(
            [sys.executable, "-m", "sentinel", "--database", str(path), *args],
            check=True,
            text=True,
            capture_output=True,
        )
        return json.loads(output.stdout)

    result = run("ask", "Why is Supplier A considered high risk?", "--propose")
    action = result["pending_action"]
    assert action["state"] == "pending"
    decision = run("decide", action["action_id"], "reject", "--reviewer", "test_reviewer")
    assert decision["state"] == "rejected" and not decision["external_action_executed"]
    assert len(run("replay")) == 4


@pytest.mark.parametrize(
    ("question", "status"),
    [
        ("Show products likely to stock out within the next 1 days.", "empty"),
        ("", "clarification"),
        (None, "clarification"),
        ("What is the lunar cheese index?", "clarification"),
        ("Delete all delayed orders.", "blocked"),
    ],
)
def test_abstention_is_audited_without_partial_recommendation(tmp_path, question, status):
    console = Console(build_database(tmp_path / "abstention.duckdb"))
    result = console.question(question)
    assert result["status"] == status and result["abstained"]
    assert not result.get("recommendations")
    events = console.audit.replay()
    assert len(events) == 1 and events[0]["event_type"] == "query"
    assert events[0]["payload"] == result
    assert console.audit.replay() == events
    if status == "empty":
        assert result["sql_validation"] == "passed"
        assert result["query_result"]["rows"] == []
    else:
        assert result["query_result"] is None
