"""Hash-linked append-only application events; replay never re-executes actions."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import duckdb


def digest(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class AuditLog:
    def __init__(self, database):
        self.database = str(Path(database).resolve())

    def replay(self):
        with duckdb.connect(self.database, read_only=True) as con:
            rows = con.execute("SELECT payload, previous_hash, event_hash FROM audit_events ORDER BY rowid").fetchall()
        events, previous = [], "0"*64
        for payload, previous_hash, event_hash in rows:
            event = json.loads(payload)
            if previous_hash != previous or event["previous_hash"] != previous or digest(event) != event_hash:
                raise ValueError("Audit chain verification failed")
            events.append(event | {"event_hash": event_hash})
            previous = event_hash
        return events

    def append(self, event_type, payload, actor="system"):
        if not actor or not actor.strip():
            raise ValueError("An actor is required")
        events = self.replay()
        previous = events[-1]["event_hash"] if events else "0"*64
        now = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid4())
        event = {"event_id": event_id, "event_type": event_type, "actor": actor, "timestamp": now, "payload": payload, "previous_hash": previous}
        event_hash = digest(event)
        with duckdb.connect(self.database) as con:
            con.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [event_id, event_type, actor, json.dumps(event, sort_keys=True), previous, event_hash,
                         "sentinel_synthetic_runtime", event_id, "syn:audit:"+event_id, "ok", 0, now])
        return event | {"event_hash": event_hash}


class ActionGate:
    """Single-operator local demo. Approval records a simulation; it has no dispatcher."""
    def __init__(self, audit):
        self.audit = audit

    def propose(self, recommendation):
        if not recommendation.get("simulated_action_only") or not recommendation.get("evidence_id"):
            raise ValueError("A simulated, evidence-backed recommendation is required")
        action = {"action_id": str(uuid.uuid4()), "state": "pending", "revision": 1,
                  "recommendation": recommendation, "simulated_action_only": True}
        self.audit.append("action_proposed", action)
        return action

    def current(self, action_id):
        matches = [event["payload"] for event in self.audit.replay() if event["event_type"].startswith("action_") and event["payload"].get("action_id") == action_id]
        if not matches:
            raise ValueError("Unknown action")
        return matches[-1]

    def decide(self, action_id, decision, actor, edited_text=None):
        if not actor or actor.strip().lower() in {"system", "model", "automatic"}:
            raise ValueError("A named human reviewer is required")
        if decision not in {"approve", "reject", "edit"}:
            raise ValueError("Choose approve, reject or edit")
        action = self.current(action_id)
        if action["state"] != "pending":
            raise ValueError("Only a pending action can receive a decision")
        if decision == "edit":
            if not edited_text or len(edited_text) > 2000:
                raise ValueError("An edit must contain 1 to 2,000 characters")
            action["human_edit"] = edited_text
            action["revision"] += 1
            # An edit preserves the original evidence and needs a new explicit approval.
        else:
            action["state"] = "approved_simulation" if decision == "approve" else "rejected"
        action["external_action_executed"] = False
        self.audit.append("action_"+decision, action, actor)
        return action
