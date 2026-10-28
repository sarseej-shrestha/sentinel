import json
import sqlite3

import pytest
from test_state_store import seeded

from sentinel.state import IntegrityFailure


@pytest.mark.parametrize("damage", ["missing", "extra", "content", "revision", "terminal"])
def test_projection_must_exactly_match_verified_history(tmp_path, damage):
    store = seeded(tmp_path)
    action = store.propose("p", "a1", "r1", "Reviewer", "Review")
    action = store.decide("d", action["id"], "approve", "Reviewer", 1)
    with sqlite3.connect(store.path) as con:
        if damage == "missing":
            con.execute("DELETE FROM actions")
        elif damage == "extra":
            con.execute("INSERT INTO actions VALUES(?,?)", ["unexpected", json.dumps(action)])
        else:
            action[{"content": "text", "revision": "revision", "terminal": "state"}[damage]] = {
                "content": "changed",
                "revision": 99,
                "terminal": "pending",
            }[damage]
            con.execute("UPDATE actions SET body=?", [json.dumps(action)])
    with pytest.raises(IntegrityFailure):
        store.history_snapshot()


def test_history_verifies_chain_once(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    for i in range(10):
        store.propose(str(i), "a1", "r1", "Reviewer", "Review")
    calls = []
    original = store._events

    def verify(con):
        calls.append(1)
        return original(con)

    monkeypatch.setattr(store, "_events", verify)
    result = store.history_snapshot()
    assert len(calls) == 1
    assert result["chain_verified"] and result["projections_verified"]
