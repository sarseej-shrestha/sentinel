"""Measure consistent history reads at fixed sizes on a disposable minimal fixture."""

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path

from sentinel.state import StateStore, make_pack


def measure():
    results = []
    with tempfile.TemporaryDirectory(prefix="sentinel-history-") as root:
        store = StateStore(Path(root) / "state.sqlite3")
        query = {
            "sql": "SELECT * FROM risk_view",
            "parameters": {},
            "rows": [{"source_record_id": "inventory:1", "on_hand": 30}],
        }
        query["evidence_id"] = hashlib.sha256(
            json.dumps(query, sort_keys=True).encode()
        ).hexdigest()
        pack = make_pack(query, "dataset-hash", "2026-09-30")
        store.save_analysis(
            {
                "analysis_id": "a1",
                "evidence_id": pack["id"],
                "recommendations": [
                    {"id": "r1", "target_row_ids": [pack["row_ids"][0]], "text": "Review coverage"}
                ],
            },
            pack,
        )
        for i in range(300):
            store.propose(f"p{i}", "a1", "r1", "Measurement reviewer", f"Review {i}")
            if i + 1 in (10, 100, 300):
                durations = []
                for _ in range(5):
                    start = time.perf_counter()
                    snapshot = store.history_snapshot()
                    durations.append((time.perf_counter() - start) * 1000)
                    assert len(snapshot["actions"]) == i + 1 and snapshot["projections_verified"]
                results.append(
                    {
                        "proposals": i + 1,
                        "samples": 5,
                        "median_ms": statistics.median(durations),
                        "max_ms": max(durations),
                        "payload_bytes": len(json.dumps(snapshot).encode()),
                    }
                )
    return results


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
