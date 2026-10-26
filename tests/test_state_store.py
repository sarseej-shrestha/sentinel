"""Atomic local decisions, immutable evidence, retries and recovery."""

import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from sentinel.state import Conflict, IntegrityFailure, StateStore, make_pack


def pack():
    rows = [{"source_record_id": "inventory:1", "on_hand": 30}]
    sql, params = "SELECT * FROM risk_view", {}
    identity = hashlib.sha256(
        json.dumps({"sql": sql, "parameters": params, "rows": rows}, sort_keys=True).encode()
    ).hexdigest()
    return make_pack(
        {"sql": sql, "parameters": params, "rows": rows, "evidence_id": identity},
        "dataset-hash",
        "2026-09-30",
    )


def seeded(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    evidence = pack()
    result = {
        "analysis_id": "a1",
        "evidence_id": evidence["id"],
        "recommendations": [
            {"id": "r1", "target_row_ids": [evidence["row_ids"][0]], "text": "Review coverage"}
        ],
    }
    store.save_analysis(result, evidence)
    return store


def test_restart_pagination_and_integrity(tmp_path):
    store = seeded(tmp_path)
    evidence_id = store.analysis("a1")["evidence_id"]
    reopened = StateStore(store.path)
    assert reopened.evidence(evidence_id, 0, 1)["total"] == 1
    assert reopened.verify()["event_count"] == 1
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE evidence SET body='{}'")
    with pytest.raises(IntegrityFailure):
        reopened.evidence(evidence_id, 0, 1)


def test_idempotency_revision_and_terminal_state(tmp_path):
    store = seeded(tmp_path)
    request = dict(analysis_id="a1", recommendation_id="r1", reviewer="Sam", text="Review coverage")
    a = store.propose("p-key", **request)
    assert store.propose("p-key", **request) == a
    with pytest.raises(Conflict):
        store.propose("p-key", **(request | {"text": "Different"}))
    edit = store.decide("edit-key", a["id"], "edit", "Sam", 1, "Revised review")
    assert edit["revision"] == 2 and edit["state"] == "pending"
    with pytest.raises(Conflict):
        store.decide("stale", a["id"], "approve", "Sam", 1)
    approved = store.decide("approve-key", a["id"], "approve", "Sam", 2)
    assert approved["state"] == "approved_simulation"
    assert store.decide("approve-key", a["id"], "approve", "Sam", 2) == approved
    with pytest.raises(Conflict):
        store.decide("conflict", a["id"], "reject", "Sam", 2)
    with pytest.raises(Conflict):
        store.decide("late-edit", a["id"], "edit", "Sam", 3, "Changed")


def test_concurrent_approval_has_one_winner(tmp_path):
    store = seeded(tmp_path)
    a = store.propose("proposal", "a1", "r1", "Sam", "Review")

    def decide(i):
        try:
            return store.decide(f"key-{i}", a["id"], "approve" if i % 2 else "reject", "Sam", 1)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(decide, range(6)))
    assert sum(r is not None for r in results) == 1
    assert store.verify()["event_count"] == 3


def test_failed_commit_preserves_pending_draft(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    a = store.propose("p", "a1", "r1", "Sam", "Keep this draft")
    original = store._append

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("Injected storage failure")

    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.decide("d", a["id"], "approve", "Sam", 1)
    assert store.actions()[0] == a
    assert store.verify()["event_count"] == 2


def test_corrupt_chain_fails_closed_without_repairing(tmp_path):
    store = seeded(tmp_path)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE events SET body='{}'")
    with pytest.raises(IntegrityFailure):
        store.propose("p", "a1", "r1", "Sam", "Review")
    with pytest.raises(IntegrityFailure):
        StateStore(store.path).verify()


def test_evidence_tampering_blocks_decision(tmp_path):
    store = seeded(tmp_path)
    a = store.propose("p", "a1", "r1", "Sam", "Review")
    with sqlite3.connect(store.path) as con:
        body = json.loads(con.execute("SELECT body FROM evidence").fetchone()[0])
        body["rows"][0]["on_hand"] = 999
        con.execute("UPDATE evidence SET body=?", [json.dumps(body)])
    with pytest.raises(IntegrityFailure):
        store.decide("d", a["id"], "approve", "Sam", 1)


def test_pack_rejects_forged_source():
    p = pack()
    damaged = copy.deepcopy(p)
    damaged["rows"][0]["on_hand"] = 5
    with pytest.raises(ValueError):
        make_pack(
            {
                "sql": p["sql"],
                "parameters": p["parameters"],
                "rows": damaged["rows"],
                "evidence_id": p["snapshot_id"],
            },
            "d",
            "2026-09-30",
        )


def test_history_pins_one_committed_snapshot(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    store.propose("p", "a1", "r1", "Sam", "Review")
    original = store._action

    def read(con, action_id):
        assert con.in_transaction
        return original(con, action_id)

    monkeypatch.setattr(store, "_action", read)
    snapshot = store.history_snapshot()
    assert snapshot["event_count"] == 2
    assert snapshot["events"][-1]["payload"]["action"] == snapshot["actions"][0]


def test_corrupted_copy_recovery_preserves_original_and_pending_draft(tmp_path):
    import shutil

    store = seeded(tmp_path)
    action = store.propose("p", "a1", "r1", "Sam", "Keep pending")
    backup = tmp_path / "verified-backup.sqlite3"
    with sqlite3.connect(store.path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    damaged = tmp_path / "damaged.sqlite3"
    shutil.copy2(backup, damaged)
    with sqlite3.connect(damaged) as con:
        con.execute("UPDATE events SET body='{}' WHERE seq=1")
    damaged_hash = hashlib.sha256(damaged.read_bytes()).hexdigest()
    with pytest.raises(IntegrityFailure):
        StateStore(damaged).verify()
    restored = tmp_path / "restored.sqlite3"
    shutil.copy2(backup, restored)
    assert StateStore(restored).history_snapshot()["actions"] == [action]
    assert hashlib.sha256(damaged.read_bytes()).hexdigest() == damaged_hash
    assert store.actions() == [action]
