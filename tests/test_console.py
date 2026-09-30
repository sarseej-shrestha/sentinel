import json
import subprocess
import sys

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
