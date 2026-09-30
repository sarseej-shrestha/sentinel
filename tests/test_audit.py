import duckdb
import pytest
from sentinel.audit.logger import AuditLog, ActionGate
from sentinel.analytics.evidence import recommendation
from sentinel.nlq.executor import execute
from sentinel.data.build_duckdb import build_database


def test_audit_human_gate_and_replay(tmp_path):
    path = build_database(tmp_path / "audit.duckdb")
    audit = AuditLog(path)
    query = execute(path, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
    gate = ActionGate(audit)
    proposed = gate.propose(recommendation(query))
    with pytest.raises(ValueError, match="human"):
        gate.decide(proposed["action_id"], "approve", "system")
    edited = gate.decide(proposed["action_id"], "edit", "demo_reviewer", "Review Supplier A next week.")
    assert edited["state"] == "pending" and edited["revision"] == 2
    approved = gate.decide(proposed["action_id"], "approve", "demo_reviewer")
    assert approved["state"] == "approved_simulation"
    assert not approved["external_action_executed"]
    with pytest.raises(ValueError, match="pending"):
        gate.decide(proposed["action_id"], "approve", "demo_reviewer")
    assert len(audit.replay()) == 3
    second = gate.propose(recommendation(query))
    assert gate.decide(second["action_id"], "reject", "demo_reviewer")["state"] == "rejected"
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 540
        con.execute("UPDATE audit_events SET event_hash='tampered' WHERE event_type='action_approve'")
    with pytest.raises(ValueError, match="verification"):
        audit.replay()
