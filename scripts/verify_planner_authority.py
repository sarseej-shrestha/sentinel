"""Measure development fixtures with models disabled or deferred. Never train."""

import argparse
import hashlib
import json
import platform
import resource
import statistics
import time
from pathlib import Path


def fingerprints():
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            *Path("src/sentinel").rglob("*.py"),
            Path(__file__),
            Path("scripts/robust_instruction_data.py"),
        ]
    }


def summary(records):
    import numpy as np

    from sentinel.analytics.evidence import verified

    supported = [r for r in records if not r["expected"]["abstain"]]
    negative = [r for r in records if r["expected"]["abstain"]]
    unsafe = [r for r in negative if r["expected"]["intent"] == "unsafe"]
    evidence = [r for r in records if r.get("recommendations")]
    elapsed = [r["request_latency_ms"] for r in records]
    return {
        "cases": len(records),
        "whole_plan_exact": sum(r["query_plan"] == r["expected"] for r in records),
        "supported_exact": [
            sum(r["query_plan"] == r["expected"] for r in supported),
            len(supported),
        ],
        "negative_exact": [sum(r["query_plan"] == r["expected"] for r in negative), len(negative)],
        "safe_abstention": [
            sum(r["abstained"] and r["query_result"] is None for r in negative),
            len(negative),
        ],
        "unsafe_blocked": [
            sum(r["abstained"] and r["query_result"] is None for r in unsafe),
            len(unsafe),
        ],
        "evidence_verified": [
            sum(
                all(
                    rec["evidence_id"] == verified(r["query_result"]).evidence_id
                    and rec["evidence_rows"] == verified(r["query_result"]).rows
                    for rec in r["recommendations"]
                )
                for r in evidence
            ),
            len(evidence),
        ],
        "fallback_count": sum(r["fallback_used"] for r in records),
        "latency_ms": {
            "minimum": min(elapsed),
            "mean": statistics.mean(elapsed),
            "median": statistics.median(elapsed),
            "p95": float(np.percentile(elapsed, 95)),
            "maximum": max(elapsed),
        },
        "parent_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
    }


def run(output, real_shadow=False):
    # Spawned SQL workers must not re-import the full measurement/analytics stack.
    from scripts.robust_instruction_data import new_seeds
    from sentinel.console import Console
    from sentinel.data.build_duckdb import build_database
    from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA
    from sentinel.nlq.shadow import complete_shadow

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprints()
    cases = new_seeds()
    report = {
        "provenance": "internal development fixtures; not independent or unseen",
        "training_run": False,
        "source_hashes": frozen,
        "arms": {},
        "shadow_diagnostics": [],
    }
    for enabled in (False, True):
        name = "deferred_shadow" if enabled else "disabled"
        database = build_database(output / (name + ".duckdb"))
        records = []
        for case in cases:
            started = time.perf_counter()
            record = Console(database).question(case["question"], shadow=enabled)
            elapsed = (time.perf_counter() - started) * 1000
            records.append(
                {
                    **record,
                    "case": case["id"],
                    "category": case["category"],
                    "expected": {k: case["target"][k] for k in QUERY_PLAN_SCHEMA["properties"]},
                    "request_latency_ms": elapsed,
                }
            )
        audit = Console(database).audit
        report["arms"][name] = {
            "summary": summary(records),
            "audit": audit.verify(),
            "records": records,
        }
        if enabled and real_shadow:
            seen = set()
            for record in records:
                intent = record["query_plan"]["intent"]
                if record["abstained"] or intent in seen:
                    continue
                seen.add(intent)
                prefix = audit.replay()
                diagnostic = complete_shadow(audit, record["shadow_request_id"])
                assert audit.replay()[:-1] == prefix
                report["shadow_diagnostics"].append(diagnostic)
                print(
                    json.dumps(
                        {
                            "completed_intent": intent,
                            "classifications": diagnostic["classifications"],
                            "latency_ms": diagnostic["latency_ms"],
                        }
                    ),
                    flush=True,
                )
            report["audit_after_shadow"] = audit.verify()
    for before, after in zip(
        report["arms"]["disabled"]["records"],
        report["arms"]["deferred_shadow"]["records"],
        strict=True,
    ):
        for key in (
            "query_plan",
            "resolution",
            "compiled_plan",
            "status",
            "abstained",
            "recommendations",
        ):
            assert before.get(key) == after.get(key), key
    if fingerprints() != frozen:
        raise RuntimeError("Sources changed during measurement")
    report["authoritative_results_identical"] = True
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(
        json.dumps(
            {
                "arms": {
                    k: {"summary": v["summary"], "audit": v["audit"]}
                    for k, v in report["arms"].items()
                },
                "real_shadow_jobs": len(report["shadow_diagnostics"]),
                "authoritative_results_identical": True,
            },
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--real-shadow",
        action="store_true",
        help="After responses, measure five cached Qwen/BGE diagnostic jobs",
    )
    args = parser.parse_args()
    run(args.output, args.real_shadow)
