import copy
import json

import duckdb
import pytest

from sentinel.analytics.evidence import recommendation
from sentinel.audit.logger import (
    GENESIS_HASH,
    ActionGate,
    AuditLog,
    canonical_json,
    digest,
    verify_events,
)
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.executor import execute


def test_audit_human_gate_and_replay(tmp_path):
    path = build_database(tmp_path / "audit.duckdb")
    audit = AuditLog(path)
    query = execute(path, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
    gate = ActionGate(audit)
    proposed = gate.propose(recommendation(query))
    with pytest.raises(ValueError, match="human"):
        gate.decide(proposed["action_id"], "approve", "system")
    edited = gate.decide(
        proposed["action_id"], "edit", "demo_reviewer", "Review Supplier A next week."
    )
    assert edited["state"] == "pending" and edited["revision"] == 2
    approved = gate.decide(proposed["action_id"], "approve", "demo_reviewer")
    assert approved["state"] == "approved_simulation"
    assert approved["human_edit"] == edited["human_edit"] == "Review Supplier A next week."
    assert approved["recommendation"] == proposed["recommendation"]
    assert not approved["external_action_executed"]
    with pytest.raises(ValueError, match="pending"):
        gate.decide(proposed["action_id"], "approve", "demo_reviewer")
    assert len(audit.replay()) == 3
    second = gate.propose(recommendation(query))
    assert gate.decide(second["action_id"], "reject", "demo_reviewer")["state"] == "rejected"
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 540
        con.execute(
            "UPDATE audit_events SET event_hash='tampered' WHERE event_type='action_approve'"
        )
    with pytest.raises(ValueError, match="verification"):
        audit.replay()


@pytest.mark.parametrize(
    "text", [None, "", " \t\n", ["review"], {"text": "review"}, 42, "x" * 2001]
)
def test_invalid_edit_leaves_action_and_audit_unchanged(tmp_path, text):
    path = build_database(tmp_path / "edit.duckdb")
    audit = AuditLog(path)
    gate = ActionGate(audit)
    query = execute(path, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
    action = gate.propose(recommendation(query))
    before = audit.replay()
    with pytest.raises(ValueError, match="edit"):
        gate.decide(action["action_id"], "edit", "test_reviewer", text)
    assert audit.replay() == before
    assert gate.current(action["action_id"]) == action


def test_canonical_audit_storage_and_legacy_replay(tmp_path):
    audit = AuditLog(build_database(tmp_path / "canonical.duckdb"))
    assert audit.verify() == {"event_count": 0, "head_hash": GENESIS_HASH}
    payload = {"z": [3, 2, 1], "a": {"supplier": "Synthetic café", "quantity": 10}}
    assert canonical_json(payload) == canonical_json({"a": payload["a"], "z": payload["z"]})
    assert digest(payload) == digest({"a": payload["a"], "z": payload["z"]})
    event = audit.append("analysis", payload)
    hashed = {key: value for key, value in event.items() if key != "event_hash"}
    with duckdb.connect(audit.database) as con:
        assert con.execute("SELECT payload FROM audit_events").fetchone()[0] == canonical_json(
            hashed
        )
        # Existing databases used noncompact JSON; canonical hashes remain compatible.
        con.execute("UPDATE audit_events SET payload = ?", [json.dumps(hashed, indent=2)])
    assert audit.replay() == [event]
    assert audit.verify() == {"event_count": 1, "head_hash": event["event_hash"]}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_payload_cannot_create_a_partial_audit_event(tmp_path, value):
    audit = AuditLog(build_database(tmp_path / "nonfinite.duckdb"))
    with pytest.raises(ValueError):
        audit.append("analysis", {"quantity": value})
    assert audit.replay() == []


@pytest.mark.parametrize(
    "damage", ["payload", "reordered", "deleted_middle", "duplicate", "missing_hash"]
)
def test_snapshot_verifier_detects_modified_or_reordered_events(tmp_path, damage):
    audit = AuditLog(build_database(tmp_path / "snapshot.duckdb"))
    original = [audit.append("analysis", {"sequence": number}) for number in range(3)]
    snapshot = copy.deepcopy(original)
    if damage == "payload":
        snapshot[1]["payload"]["sequence"] = 99
    elif damage == "reordered":
        snapshot[0], snapshot[1] = snapshot[1], snapshot[0]
    elif damage == "deleted_middle":
        del snapshot[1]
    elif damage == "duplicate":
        snapshot.insert(1, copy.deepcopy(snapshot[0]))
    else:
        del snapshot[1]["event_hash"]
    with pytest.raises(ValueError, match="verification"):
        verify_events(snapshot)
    assert audit.replay() == original
    assert verify_events(original) == audit.verify()


@pytest.mark.parametrize(
    "column", ["event_id", "event_type", "actor", "previous_hash", "event_hash", "payload"]
)
def test_persisted_tampering_blocks_replay_and_append(tmp_path, column):
    audit = AuditLog(build_database(tmp_path / "tampered.duckdb"))
    audit.append("analysis", {"quantity": 10})
    # Column names are a fixed test allowlist; only the value is parameterized SQL.
    with duckdb.connect(audit.database) as con:
        con.execute(f"UPDATE audit_events SET {column} = ?", ["tampered"])
    for operation in [audit.replay, audit.verify, lambda: audit.append("analysis", {})]:
        with pytest.raises(ValueError, match="verification"):
            operation()
    with duckdb.connect(audit.database, read_only=True) as con:
        assert con.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1


def test_database_reordering_is_detected(tmp_path):
    audit = AuditLog(build_database(tmp_path / "reordered.duckdb"))
    for number in range(3):
        audit.append("analysis", {"sequence": number})
    with duckdb.connect(audit.database) as con:
        con.execute(
            "CREATE TEMP TABLE reversed_events AS SELECT * FROM audit_events ORDER BY rowid DESC"
        )
        con.execute("DELETE FROM audit_events")
        con.execute("INSERT INTO audit_events SELECT * FROM reversed_events")
    with pytest.raises(ValueError, match="verification"):
        audit.replay()


@pytest.mark.parametrize("damage", ["changed_edit", "missing_field", "extra_hash", "array"])
def test_valid_json_tampering_is_not_replayed(tmp_path, damage):
    audit = AuditLog(build_database(tmp_path / "json-tamper.duckdb"))
    event = audit.append("action_edit", {"human_edit": "Review only."}, "test_reviewer")
    stored = {key: value for key, value in event.items() if key != "event_hash"}
    if damage == "changed_edit":
        stored["payload"]["human_edit"] = "Altered decision."
    elif damage == "missing_field":
        del stored["timestamp"]
    elif damage == "extra_hash":
        stored["event_hash"] = "unverified"
    else:
        stored = []
    with duckdb.connect(audit.database) as con:
        con.execute("UPDATE audit_events SET payload = ?", [json.dumps(stored)])
    with pytest.raises(ValueError, match="verification"):
        audit.replay()
