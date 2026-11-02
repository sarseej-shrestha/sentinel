"""A stopped-workspace pair restores decisions and captured source evidence."""

import shutil

from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database


def test_stopped_workspace_pair_restores_history_and_sources(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    database = build_database(original / "analytics.duckdb")
    state = original / "state.sqlite3"
    client = TestClient(create_app(database, state), headers={"X-Sentinel-Client": "workspace"})
    analysis_response = client.post(
        "/api/analyses",
        json={"kind": "supplier_delay", "start_date": "2026-08-01", "end_date": "2026-08-31"},
    )
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    recommendation = next(r for r in analysis["recommendations"] if r["target_id"] == "supplier:S1")
    proposal = client.post(
        "/api/proposals",
        json={
            "key": "backup-proposal",
            "analysis_id": analysis["analysis_id"],
            "recommendation_id": recommendation["id"],
            "reviewer": "Restore test",
            "text": "Review the recorded Supplier A deliveries.",
        },
    ).json()
    decision = client.post(
        f"/api/proposals/{proposal['id']}/decisions",
        json={
            "key": "backup-approval",
            "decision": "approve",
            "reviewer": "Restore test",
            "expected_revision": 1,
        },
    )
    assert decision.status_code == 200
    history = client.get("/api/history").json()
    source_path = f"/api/evidence/{analysis['evidence_id']}/targets/supplier:S1"
    source = client.get(source_path, params={"kind": "shipment", "limit": 100}).json()

    # No service process or transaction is open when copying this idle pair.
    restored = tmp_path / "restored"
    restored.mkdir()
    shutil.copy2(database, restored / "analytics.duckdb")
    shutil.copy2(state, restored / "state.sqlite3")
    replay = TestClient(
        create_app(restored / "analytics.duckdb", restored / "state.sqlite3"),
        headers={"X-Sentinel-Client": "workspace"},
    )
    assert replay.get("/api/history").json() == history
    assert replay.get("/api/analyses/" + analysis["analysis_id"]).json() == analysis
    assert replay.get(source_path, params={"kind": "shipment", "limit": 100}).json() == source
    assert history["verified"] and history["projections_verified"]
    assert history["actions"][0]["state"] == "approved_simulation"
