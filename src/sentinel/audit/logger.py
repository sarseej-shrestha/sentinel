"""Local tamper detection, not authentication; replay never re-executes actions."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from sentinel.analytics.evidence import verify_snapshot

GENESIS_HASH = "0" * 64
EVENT_FIELDS = {"event_id", "event_type", "actor", "timestamp", "payload", "previous_hash"}


def canonical_json(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(event):
    return hashlib.sha256(canonical_json(event).encode("utf-8")).hexdigest()


def verify_events(events):
    """Verify an ordered replay snapshot without executing it.

    A rewritten chain or removed suffix needs an independently retained head hash
    to detect. This verifier alone does not authenticate the author or completeness.
    """
    previous, seen = GENESIS_HASH, set()
    try:
        for stored in events:
            event = dict(stored)
            event_hash = event.pop("event_hash")
            if (
                set(event) != EVENT_FIELDS
                or event["event_id"] in seen
                or event["previous_hash"] != previous
                or digest(event) != event_hash
            ):
                raise ValueError("Invalid event or hash link")
            seen.add(event["event_id"])
            previous = event_hash
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Audit chain verification failed") from exc
    return {"event_count": len(seen), "head_hash": previous}


class AuditLog:
    def __init__(self, database):
        self.database = str(Path(database).resolve())

    def replay(self):
        with duckdb.connect(self.database, read_only=True) as con:
            rows = con.execute(
                "SELECT event_id, event_type, actor, payload, previous_hash, event_hash "
                "FROM audit_events ORDER BY rowid"
            ).fetchall()
        events = []
        try:
            for event_id, event_type, actor, payload, previous_hash, event_hash in rows:
                event = json.loads(payload)
                # The indexed columns must describe the same event as the hashed JSON.
                if (
                    not isinstance(event, dict)
                    or set(event) != EVENT_FIELDS
                    or any(
                        event.get(key) != value
                        for key, value in {
                            "event_id": event_id,
                            "event_type": event_type,
                            "actor": actor,
                            "previous_hash": previous_hash,
                        }.items()
                    )
                ):
                    raise ValueError("Event metadata differs from its snapshot")
                events.append(event | {"event_hash": event_hash})
        except (TypeError, ValueError) as exc:
            raise ValueError("Audit chain verification failed") from exc
        verify_events(events)
        return events

    def verify(self):
        """Return the verified local chain length and head hash, or raise ValueError."""
        return verify_events(self.replay())

    def append(self, event_type, payload, actor="system"):
        if not actor or not actor.strip():
            raise ValueError("An actor is required")
        events = self.replay()
        previous = events[-1]["event_hash"] if events else GENESIS_HASH
        now = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "actor": actor,
            "timestamp": now,
            "payload": payload,
            "previous_hash": previous,
        }
        event_hash = digest(event)
        with duckdb.connect(self.database) as con:
            con.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    event_id,
                    event_type,
                    actor,
                    canonical_json(event),
                    previous,
                    event_hash,
                    "sentinel_synthetic_runtime",
                    event_id,
                    "syn:audit:" + event_id,
                    "ok",
                    0,
                    now,
                ],
            )
        return event | {"event_hash": event_hash}


class ActionGate:
    """Single-operator local demo. Approval records a simulation; it has no dispatcher."""

    def __init__(self, audit):
        self.audit = audit

    def propose(self, recommendation):
        if (
            not isinstance(recommendation, dict)
            or recommendation.get("simulated_action_only") is not True
        ):
            raise ValueError("A simulated, evidence-backed recommendation is required")
        verify_snapshot(
            recommendation.get("verified_sql"),
            recommendation.get("parameters"),
            recommendation.get("evidence_rows"),
            recommendation.get("evidence_id"),
        )
        action = {
            "action_id": str(uuid.uuid4()),
            "state": "pending",
            "revision": 1,
            "recommendation": recommendation,
            "simulated_action_only": True,
        }
        self.audit.append("action_proposed", action)
        return action

    def current(self, action_id):
        matches = [
            event["payload"]
            for event in self.audit.replay()
            if event["event_type"].startswith("action_")
            and event["payload"].get("action_id") == action_id
        ]
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
            if (
                not isinstance(edited_text, str)
                or not edited_text.strip()
                or len(edited_text) > 2000
            ):
                raise ValueError("An edit must contain 1 to 2,000 characters of nonblank text")
            action["human_edit"] = edited_text
            action["revision"] += 1
            # An edit preserves the original evidence and needs a new explicit approval.
        else:
            action["state"] = "approved_simulation" if decision == "approve" else "rejected"
        action["external_action_executed"] = False
        self.audit.append("action_" + decision, action, actor)
        return action
