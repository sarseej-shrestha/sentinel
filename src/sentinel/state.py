"""Transactional local application state. Hashes detect changes, not authorship.

Analytics never write here. Rollback journaling avoids dependence on WAL behavior.
Legacy DuckDB audit exports remain byte-for-byte event snapshots, never rehashed.
"""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sentinel.analytics.evidence import verify_snapshot
from sentinel.audit.logger import GENESIS_HASH, canonical_json, digest, verify_events


class Conflict(ValueError):
    pass


class IntegrityFailure(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def verify_query(sql, parameters, rows, identity):
    if rows:
        verify_snapshot(sql, parameters, rows, identity)
    elif (
        not sql
        or hashlib.sha256(
            json.dumps(
                {"sql": sql, "parameters": parameters, "rows": rows},
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        != identity
    ):
        raise ValueError("Invalid empty query snapshot")


def make_pack(query, dataset_fingerprint, reference_date, source=None):
    verify_query(query["sql"], query["parameters"], query["rows"], query["evidence_id"])
    body = {
        "snapshot_id": query["evidence_id"],
        "sql": query["sql"],
        "parameters": query["parameters"],
        "rows": query["rows"],
        "row_ids": [fingerprint({"index": i, "row": row}) for i, row in enumerate(query["rows"])],
        "dataset_fingerprint": dataset_fingerprint,
        "reference_date": reference_date,
        "versions": {
            "application": "1.0",
            "schema": "synthetic-v2",
            "query_plan": 2,
            "forecast": "recursive-hist-quantiles-v1",
            "risk": "synthetic-logistic-rules-v1",
        },
        "truncated": query.get("truncated", False),
        "included_count": len(query["rows"]),
        "excluded_count": sum(
            row.get(
                "excluded_shipments",
                row.get("excluded_demand_count", row.get("missing_or_invalid_shipments", 0)),
            )
            or 0
            for row in query["rows"]
        ),
    }
    if source is not None:
        body["source_id"] = fingerprint(source)
    return {"id": fingerprint(body), **body}


class StateStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            # Do not silently switch a pre-existing WAL database under other users.
            if con.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise IntegrityFailure("Application state requires SQLite DELETE journaling")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, event_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=FULL")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                self._events(con)
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def _events(self, con):
        try:
            events = [
                json.loads(row[0]) for row in con.execute("SELECT body FROM events ORDER BY seq")
            ]
            verify_events(events)
            return events
        except (ValueError, KeyError, TypeError) as exc:
            raise IntegrityFailure(
                "Audit verification failed. Preserve this database and restore a verified backup."
            ) from exc

    def _append(self, con, kind, payload, actor="system"):
        previous = con.execute("SELECT body FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": kind,
            "actor": actor,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
            "previous_hash": json.loads(previous[0])["event_hash"] if previous else GENESIS_HASH,
        }
        event["event_hash"] = digest(event)
        con.execute("INSERT INTO events(body) VALUES(?)", [canonical_json(event)])
        return event

    def verify(self):
        with self._connection() as con:
            return verify_events(self._events(con))

    def history(self):
        with self._connection() as con:
            return self._events(con)

    def _projections(self, con, events):
        try:
            latest = {}
            for event in events:
                if event["event_type"].startswith("decision_"):
                    action = event["payload"]["action"]
                    latest[action["id"]] = action
            rows = con.execute("SELECT id,body FROM actions ORDER BY rowid DESC").fetchall()
            actual = {key: json.loads(body) for key, body in rows}
            if actual != latest:
                raise IntegrityFailure(
                    "Decision projections are incomplete or differ from verified history. Preserve the database; no automatic repair was performed."
                )
            for evidence_id in {a["evidence_id"] for a in actual.values()}:
                self._pack(con, evidence_id)
            return [actual[key] for key, _ in rows]
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, IntegrityFailure):
                raise
            raise IntegrityFailure("Decision projection verification failed") from exc

    def history_snapshot(self):
        with self._connection() as con:
            con.execute("BEGIN")
            events = self._events(con)
            actions = self._projections(con, events)
            return {
                "verified": True,
                "chain_verified": True,
                "projections_verified": True,
                **{
                    "event_count": len(events),
                    "head_hash": events[-1]["event_hash"] if events else GENESIS_HASH,
                },
                "events": events,
                "actions": actions,
            }

    def _pack(self, con, evidence_id):
        row = con.execute("SELECT body FROM evidence WHERE id=?", [evidence_id]).fetchone()
        if not row:
            raise IntegrityFailure("Evidence is missing")
        try:
            pack = json.loads(row[0])
            body = {k: v for k, v in pack.items() if k != "id"}
            if fingerprint(body) != evidence_id or pack["id"] != evidence_id:
                raise ValueError("Evidence digest differs")
            verify_query(pack["sql"], pack["parameters"], pack["rows"], pack["snapshot_id"])
            if pack.get("source_id"):
                self._source(con, pack["source_id"])
            return pack
        except (ValueError, KeyError, TypeError) as exc:
            raise IntegrityFailure("Evidence integrity verification failed") from exc

    def evidence(self, evidence_id, offset=0, limit=25):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid evidence page")
        with self._connection() as con:
            self._events(con)
            pack = self._pack(con, evidence_id)
        return {
            **{k: v for k, v in pack.items() if k not in {"rows", "row_ids"}},
            "rows": pack["rows"][offset : offset + limit],
            "row_ids": pack["row_ids"][offset : offset + limit],
            "offset": offset,
            "total": len(pack["rows"]),
            "verified": True,
        }

    def _source(self, con, source_id):
        row = con.execute("SELECT body FROM sources WHERE id=?", [source_id]).fetchone()
        if not row:
            raise IntegrityFailure("Historical source snapshot is unavailable")
        try:
            value = json.loads(row[0])
            if fingerprint(value) != source_id:
                raise ValueError("Changed source snapshot")
            return value
        except (ValueError, TypeError) as exc:
            raise IntegrityFailure("Source snapshot integrity verification failed") from exc

    def save_analysis(self, result, pack=None, source=None):
        with self._transaction() as con:
            if source is not None:
                if not pack or fingerprint(source) != pack.get("source_id"):
                    raise IntegrityFailure("Source snapshot is not bound to evidence")
                con.execute(
                    "INSERT OR IGNORE INTO sources VALUES(?,?)",
                    [fingerprint(source), canonical_json(source)],
                )
            if pack:
                if fingerprint({k: v for k, v in pack.items() if k != "id"}) != pack["id"]:
                    raise IntegrityFailure("Invalid evidence pack")
                con.execute(
                    "INSERT OR IGNORE INTO evidence VALUES(?,?)", [pack["id"], canonical_json(pack)]
                )
                self._pack(con, pack["id"])
            identity = fingerprint(result)
            con.execute(
                "INSERT INTO analyses VALUES(?,?,?)",
                [result["analysis_id"], canonical_json(result), identity],
            )
            self._append(
                con,
                "analysis_saved",
                {
                    "analysis_id": result["analysis_id"],
                    "result_hash": identity,
                    "evidence_id": result.get("evidence_id"),
                },
            )
        return result

    def _analysis(self, con, analysis_id):
        row = con.execute("SELECT body,hash FROM analyses WHERE id=?", [analysis_id]).fetchone()
        if not row:
            raise KeyError("Unknown analysis")
        result = json.loads(row[0])
        events = self._events(con)
        expected = next(
            (
                e["payload"]["result_hash"]
                for e in events
                if e["event_type"] == "analysis_saved"
                and e["payload"]["analysis_id"] == analysis_id
            ),
            None,
        )
        if fingerprint(result) != row[1] or row[1] != expected:
            raise IntegrityFailure("Analysis snapshot verification failed")
        if result.get("evidence_id"):
            self._pack(con, result["evidence_id"])
        return result

    def analysis(self, analysis_id):
        with self._connection() as con:
            return self._analysis(con, analysis_id)

    def _action(self, con, action_id):
        row = con.execute("SELECT body FROM actions WHERE id=?", [action_id]).fetchone()
        if not row:
            raise KeyError("Unknown proposal")
        action = json.loads(row[0])
        snapshots = [
            e["payload"]["action"]
            for e in self._events(con)
            if e["event_type"].startswith("decision_") and e["payload"]["action"]["id"] == action_id
        ]
        if not snapshots or action != snapshots[-1]:
            raise IntegrityFailure("Decision snapshot verification failed")
        self._pack(con, action["evidence_id"])
        return action

    def actions(self):
        with self._connection() as con:
            self._events(con)
            return [
                self._action(con, row[0])
                for row in con.execute("SELECT id FROM actions ORDER BY rowid DESC").fetchall()
            ]

    def _retry(self, con, key, request):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("An idempotency key is required")
        row = con.execute(
            "SELECT request_hash,event_id FROM receipts WHERE key=?", [key]
        ).fetchone()
        if row:
            if row[0] != fingerprint(request):
                raise Conflict("This retry key was already used with different content")
            event = next((e for e in self._events(con) if e["event_id"] == row[1]), None)
            if (
                not event
                or event["payload"].get("request_hash") != row[0]
                or event["payload"].get("key") != key
            ):
                raise IntegrityFailure("Retry record verification failed")
            action = event["payload"]["action"]
            self._pack(con, action["evidence_id"])
            return action
        return None

    @staticmethod
    def _reviewer(reviewer):
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or len(reviewer) > 100
            or reviewer.strip().casefold() in {"system", "model", "automatic"}
        ):
            raise ValueError(
                "Enter a named human reviewer; this is attribution, not authentication"
            )

    @staticmethod
    def _text(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("A draft must contain 1 to 2,000 characters")

    def _save_action(self, con, key, request, action, decision, reviewer):
        con.execute(
            "INSERT INTO actions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            [action["id"], canonical_json(action)],
        )
        event = self._append(
            con,
            "decision_" + decision,
            {"action": action, "key": key, "request_hash": fingerprint(request)},
            reviewer,
        )
        con.execute(
            "INSERT INTO receipts VALUES(?,?,?)", [key, fingerprint(request), event["event_id"]]
        )
        return action

    def propose(self, key, analysis_id, recommendation_id, reviewer, text):
        self._reviewer(reviewer)
        self._text(text)
        request = {
            "operation": "propose",
            "analysis_id": analysis_id,
            "recommendation_id": recommendation_id,
            "reviewer": reviewer,
            "text": text,
        }
        with self._transaction() as con:
            prior = self._retry(con, key, request)
            if prior:
                return prior
            result = self._analysis(con, analysis_id)
            rec = next((r for r in result["recommendations"] if r["id"] == recommendation_id), None)
            if not rec:
                raise ValueError("Select a recommendation from this analysis")
            pack = self._pack(con, result["evidence_id"])
            if (
                pack["truncated"]
                or not rec["target_row_ids"]
                or not set(rec["target_row_ids"]) <= set(pack["row_ids"])
            ):
                raise IntegrityFailure("Proposal requires complete linked evidence")
            action = {
                "id": str(uuid.uuid4()),
                "analysis_id": analysis_id,
                "recommendation_id": recommendation_id,
                "evidence_id": pack["id"],
                "target_row_ids": rec["target_row_ids"],
                "state": "pending",
                "revision": 1,
                "text": text,
                "reviewer": reviewer,
                "simulated_action_only": True,
                "external_action_executed": False,
            }
            self._save_action(con, key, request, action, "proposed", reviewer)
        return action

    def decide(self, key, action_id, decision, reviewer, expected_revision, text=None):
        self._reviewer(reviewer)
        if decision not in {"edit", "approve", "reject"}:
            raise ValueError("Choose edit, approve or reject")
        if decision == "edit":
            self._text(text)
        elif text is not None:
            raise ValueError("Save draft edits separately before a final decision")
        request = {
            "operation": decision,
            "action_id": action_id,
            "reviewer": reviewer,
            "revision": expected_revision,
            "text": text,
        }
        with self._transaction() as con:
            prior = self._retry(con, key, request)
            if prior:
                return prior
            action = self._action(con, action_id)
            if action["state"] != "pending" or action["revision"] != expected_revision:
                raise Conflict(
                    "This proposal changed or is final. Reload its saved state before continuing."
                )
            action = {**action, "revision": action["revision"] + 1, "reviewer": reviewer}
            if decision == "edit":
                action["text"] = text
            else:
                action["state"] = "approved_simulation" if decision == "approve" else "rejected"
            self._save_action(con, key, request, action, decision, reviewer)
        return action

    def import_legacy(self, events):
        verify_events(events)
        identity = fingerprint(events)
        with self._transaction() as con:
            if not con.execute("SELECT 1 FROM legacy WHERE id=?", [identity]).fetchone():
                con.execute("INSERT INTO legacy VALUES(?,?)", [identity, canonical_json(events)])
                self._append(
                    con, "legacy_imported", {"snapshot_id": identity, "event_count": len(events)}
                )
        return identity

    def legacy(self, identity):
        with self._connection() as con:
            self._events(con)
            row = con.execute("SELECT body FROM legacy WHERE id=?", [identity]).fetchone()
            if not row:
                raise KeyError("Unknown legacy snapshot")
            events = json.loads(row[0])
            if fingerprint(events) != identity:
                raise IntegrityFailure("Legacy snapshot changed")
            verify_events(events)
            return events
