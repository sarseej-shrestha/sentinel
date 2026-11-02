import json

import duckdb
import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database, synthetic_records


@pytest.fixture
def workspace(tmp_path):
    db = build_database(tmp_path / "a.duckdb")
    app = create_app(db, tmp_path / "s.sqlite3")
    return db, app, TestClient(app, headers={"X-Sentinel-Client": "workspace"})


def analyze(client):
    r = client.post(
        "/api/analyses",
        json={"kind": "supplier_delay", "start_date": "2026-08-01", "end_date": "2026-08-31"},
    )
    assert r.status_code == 200
    return r.json()


def test_target_records_reconcile_and_paginate(workspace):
    db, app, client = workspace
    result = analyze(client)
    rec = next(r for r in result["recommendations"] if r["target_id"] == "supplier:S1")
    path = f"/api/evidence/{result['evidence_id']}/targets/supplier:S1"
    records = []
    for offset in (0, 10, 20, 30):
        response = client.get(path, params={"kind": "shipment", "offset": offset, "limit": 10})
        assert response.status_code == 200
        records += response.json()["records"]
    with duckdb.connect(str(db), read_only=True) as con:
        actual = con.execute(
            "SELECT promised_date,delivered_date FROM shipments WHERE supplier_id='S1'"
        ).fetchall()
    eligible = [(p, d) for p, d in actual if "2026-08-01" <= str(p) <= "2026-08-31"]
    assert len(records) == len(eligible) == 31
    assert (
        sum(r["contribution"] == "late" for r in records) == sum(d > p for p, d in eligible) == 19
    )
    assert len({r["id"] for r in records}) == 31
    assert "19" in rec["text"] and "31" in rec["text"] and "2026-08-01" in rec["text"]
    assert client.get(path.replace("supplier:S1", "supplier:UNKNOWN")).status_code == 404
    assert client.get(path, params={"kind": "audit_events"}).status_code == 422
    assert client.get(path, params={"limit": 101}).status_code == 422
    assert len(json.dumps(result).encode()) < 1024 * 1024


def test_history_sources_do_not_follow_changed_database(workspace):
    db, app, client = workspace
    result = analyze(client)
    path = f"/api/evidence/{result['evidence_id']}/targets/supplier:S1?kind=shipment"
    before = client.get(path).json()
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE shipments SET delivered_date=promised_date WHERE supplier_id='S1'")
    assert client.get(path).json() == before
    assert client.get("/api/analyses/" + result["analysis_id"]).json() == result


def test_supplier_follow_up_replays_with_its_own_saved_evidence(workspace):
    _, _, client = workspace
    result = analyze(client)
    recommendation = next(r for r in result["recommendations"] if r["target_id"] == "supplier:S1")
    note = "Inspect the 19 late deliveries and document the supplier follow-up."
    response = client.post(
        "/api/proposals",
        json={
            "key": "supplier-s1-follow-up",
            "analysis_id": result["analysis_id"],
            "recommendation_id": recommendation["id"],
            "reviewer": "Test reviewer",
            "text": note,
        },
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["evidence_id"] == result["evidence_id"]
    assert saved["target_row_ids"] == recommendation["target_row_ids"]
    history = client.get("/api/history").json()
    assert history["verified"] and history["actions"] == [saved]
    replay = client.get("/api/analyses/" + saved["analysis_id"]).json()
    assert replay == result
    page = client.get(
        f"/api/evidence/{saved['evidence_id']}/targets/supplier:S1",
        params={"kind": "shipment", "limit": 100},
    ).json()
    assert page["total"] == 31
    assert sum(r["contribution"] == "late" for r in page["records"]) == 19
    assert saved["text"] == note


def test_excluded_delivery_is_visible_but_not_in_the_late_denominator(tmp_path):
    rows = synthetic_records()
    shipment = next(
        r for r in rows["shipments"] if r[2] == "S1" and r[5].isoformat().startswith("2026-08-")
    )
    rows["shipments"] = [r[:6] + (None,) + r[7:] if r == shipment else r for r in rows["shipments"]]
    db = build_database(tmp_path / "a.duckdb", records=rows)
    client = TestClient(
        create_app(db, tmp_path / "s.sqlite3"),
        headers={"X-Sentinel-Client": "workspace"},
    )
    result = analyze(client)
    target = next(t for t in result["targets"] if t["id"] == "supplier:S1")
    page = client.get(
        f"/api/evidence/{result['evidence_id']}/targets/supplier:S1",
        params={"kind": "shipment", "limit": 100},
    ).json()
    records = page["records"]
    assert page["total"] == 31
    assert target["evaluable"] == 30 and target["excluded"] == 1
    assert sum(r["disposition"] == "included" for r in records) == 30
    assert sum(r["contribution"] == "late" for r in records) == target["late"]
    excluded = next(r for r in records if r["disposition"] == "excluded")
    assert excluded["record"]["shipment_id"] == shipment[0]
    assert excluded["record"]["delivered_date"] is None
    assert excluded["reasons"] and excluded["contribution"] is None


def test_source_tampering_fails_closed(workspace):
    import sqlite3

    db, app, client = workspace
    result = analyze(client)
    with sqlite3.connect(app.state.service.store.path) as con:
        con.execute("UPDATE sources SET body='{}'")
    assert (
        client.get(f"/api/evidence/{result['evidence_id']}/targets/supplier:S1").status_code == 503
    )


def test_known_target_outside_single_supplier_scope_is_rejected(workspace):
    _, _, client = workspace
    result = client.post(
        "/api/analyses",
        json={
            "kind": "supplier_delay",
            "supplier_id": "S1",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        },
    ).json()
    assert (
        client.get(f"/api/evidence/{result['evidence_id']}/targets/supplier:S2").status_code == 404
    )


def test_legacy_source_absence_is_explicit(tmp_path):
    from test_state_store import seeded

    store = seeded(tmp_path)
    result = store.source_records(store.analysis("a1")["evidence_id"], "supplier:S1")
    assert not result["available"] and result["records"] == []
    assert "not captured" in result["message"]
