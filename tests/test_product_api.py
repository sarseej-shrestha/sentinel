import hashlib
import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient
from test_tasks import TASKS

from sentinel.api import create_app
from sentinel.audit.logger import AuditLog
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("product") / "analytics.duckdb")


@pytest.fixture
def client(database, tmp_path):
    app = create_app(database, tmp_path / "state.sqlite3")
    return TestClient(app, headers={"X-Sentinel-Client": "workspace"})


@pytest.mark.parametrize("task", TASKS)
def test_real_workflow_evidence_decision_and_replay(client, task):
    response = client.post("/api/analyses", json=task)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "success", result
    assert len(response.content) < 1024 * 1024
    evidence = client.get("/api/evidence/" + result["evidence_id"]).json()
    assert evidence["verified"] and evidence["dataset_fingerprint"]
    rec = result["recommendations"][-1]
    action = client.post(
        "/api/proposals",
        json={
            "key": "p",
            "analysis_id": result["analysis_id"],
            "recommendation_id": rec["id"],
            "reviewer": "Test reviewer",
            "text": rec["text"],
        },
    ).json()
    response = client.post(
        f"/api/proposals/{action['id']}/decisions",
        json={
            "key": "a",
            "decision": "approve",
            "reviewer": "Test reviewer",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200 and response.json()["state"] == "approved_simulation"
    assert client.get("/api/analyses/" + result["analysis_id"]).json() == result
    history = client.get("/api/history").json()
    assert history["verified"] and len(history["actions"]) == 1
    assert not any(m in sys.modules for m in ("torch", "transformers", "sentence_transformers"))


def test_large_shipment_payload_is_normalized(client):
    service = client.app.state.service
    result = service.analyze_question("Show shipments for Supplier A")
    assert result["row_count"] == 180 and len(result["recommendations"]) == 180
    assert len(json.dumps(result).encode()) < 1024 * 1024
    with sqlite3.connect(service.store.path) as con:
        assert con.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    page = service.store.evidence(result["evidence_id"], 175, 25)
    assert len(page["rows"]) == 5 and page["total"] == 180


def test_local_boundary_and_strict_contract(client):
    assert (
        client.post(
            "/api/analyses", json=TASKS[1], headers={"origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/analyses", json=TASKS[1] | {"sql": "DELETE FROM orders"}).status_code
        == 422
    )
    assert client.post("/api/analyses", json={"kind": "forecast"}).status_code == 422
    assert (
        client.post("/api/analyses", json={"kind": "stockout", "horizon_days": 1}).json()["state"]
        == "empty"
    )


def test_legacy_import_preserves_original_database_and_hashes(tmp_path):
    db = build_database(tmp_path / "legacy.duckdb")
    Console(db).question("Why is Supplier A considered high risk?")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    events = AuditLog(db).replay()
    app = create_app(db, tmp_path / "new.sqlite3")
    store = app.state.service.store
    identity = store.import_legacy(events)
    assert store.legacy(identity) == events
    assert store.import_legacy(events) == identity
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
