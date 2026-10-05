"""Isolated deterministic before/after comparison without exposing gold to the service.

The source-root option runs a trusted Git checkout in a fresh process. Questions
alone enter that process; expected plans stay in the scoring parent. Reports,
synthetic databases and measurements belong in ignored artifacts, not Git.
"""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path


def development_cases():
    from scripts.instruction_curation import seed_rows
    from sentinel.nlq.query_plan import make_plan

    seed = next(r for r in seed_rows() if r["id"] == "curated_supplier_delay_0")
    result = []
    for index, (period, start, end) in enumerate(
        [
            ("last month", "2026-08-01", "2026-09-01"),
            ("this month", "2026-09-01", "2026-10-01"),
            ("past 30 days", "2026-08-31", "2026-09-30"),
            ("previous quarter", "2026-04-01", "2026-07-01"),
            ("2026-08-07 through 2026-08-15", "2026-08-07", "2026-08-16"),
        ]
    ):
        result.append(
            {
                "id": f"development_date_{index}",
                "category": "supplier_delay",
                "question": seed["question"].replace("June 2026", period),
                "expected": make_plan("supplier_delay", time_range={"start": start, "end": end}),
            }
        )
    result.append(
        {
            "id": "development_supplier_filter",
            "category": "supplier_delay",
            "question": "Calculate the late delivery rate for Supplier B during this month.",
            "expected": make_plan(
                "supplier_delay",
                entities={"supplier_id": "S2", "warehouse_id": None, "product_id": None},
                time_range={"start": "2026-09-01", "end": "2026-10-01"},
            ),
        }
    )
    for index, question in enumerate(
        [
            "Rank suppliers by late delivery rate",
            "Rank suppliers by late delivery rate next month",
            "Rank suppliers by late delivery rate with weekly history last month",
            "Rank suppliers by late delivery rate last month or this month",
        ]
    ):
        result.append(
            {
                "id": f"development_negative_{index}",
                "category": "missing_or_ambiguous",
                "question": question,
                "expected": make_plan(),
            }
        )
    return result


def load_benchmark(path, checksum):
    raw = Path(path).read_bytes()
    if not checksum or hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("Benchmark checksum mismatch")
    data = json.loads(raw)
    if data.get("protocol") != "semantic_repair_unseen_v1":
        raise ValueError("Use only the new semantic-repair benchmark, not the original holdout")
    return data["cases"]


def worker(source_root, questions_path, output):
    sys.path.insert(0, str(Path(source_root).resolve() / "src"))
    from sentinel.console import Console
    from sentinel.data.build_duckdb import build_database

    database = build_database(Path(output) / "synthetic.duckdb")
    records = []
    sources = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(source_root) / "src/sentinel").rglob("*.py")
    }
    for question in json.loads(Path(questions_path).read_text()):
        start = time.perf_counter()
        record = Console(database).question(question)
        records.append({**record, "request_latency_ms": (time.perf_counter() - start) * 1000})
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in sources.items()):
        raise RuntimeError("Evaluation source changed during execution")
    return {"records": records, "audit": Console(database).audit.verify(), "source_hashes": sources}


def summarize(records):
    import numpy as np

    from scripts.plan_diagnostics import aggregate

    supported = [r for r in records if not r["expected"]["abstain"]]
    negative = [r for r in records if r["expected"]["abstain"]]
    unsafe = [r for r in negative if r["expected"]["intent"] == "unsafe"]
    latencies = [r["request_latency_ms"] for r in records]

    def ratio(n, d):
        return {"correct": n, "total": d, "rate": n / d if d else None}

    return {
        "whole_plan_exact": ratio(sum(r["exact"] for r in records), len(records)),
        "supported_exact": ratio(sum(r["exact"] for r in supported), len(supported)),
        "correct_negative_plan": ratio(sum(r["exact"] for r in negative), len(negative)),
        "negative_safe_abstention": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in negative), len(negative)
        ),
        "unsafe_rejection": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in unsafe), len(unsafe)
        ),
        "supported_answer": ratio(
            sum(r["exact"] and r["status"] == "ok" for r in supported), len(supported)
        ),
        "evidence_linkage": ratio(
            sum(r["evidence_verified"] for r in records if r.get("recommendations")),
            sum(bool(r.get("recommendations")) for r in records),
        ),
        "diagnostics": aggregate([r["diagnostics"] for r in records]),
        "latency_ms": {
            "minimum": min(latencies),
            "mean": statistics.mean(latencies),
            "median": statistics.median(latencies),
            "p95": float(np.percentile(latencies, 95)) if len(latencies) >= 20 else None,
            "maximum": max(latencies),
        },
    }


def evaluate(cases, output, source_root, checksum=None):
    from scripts.plan_diagnostics import diagnose
    from sentinel.analytics.evidence import verified
    from sentinel.nlq.query_plan import validate_query_plan

    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new output directory")
    for case in cases:
        if validate_query_plan(case["expected"]) != case["expected"]:
            raise ValueError("Expected plans must be canonical")
    output.mkdir(parents=True)
    questions = output / "questions.json"
    questions.write_text(json.dumps([c["question"] for c in cases]))
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--source-root",
            str(Path(source_root).resolve()),
            "--questions",
            str(questions),
            "--output",
            str(output),
        ],
        check=True,
    )
    result = json.loads((output / "worker.json").read_text())
    for case, record in zip(cases, result["records"], strict=True):
        record.update(
            case=case["id"],
            category=case["category"],
            expected=case["expected"],
            exact=record["query_plan"] == case["expected"],
            diagnostics=diagnose(record["query_plan"], case["expected"]),
            evidence_verified=False,
        )
        if record.get("recommendations"):
            snapshot = verified(record["query_result"])
            record["evidence_verified"] = all(
                r["evidence_id"] == snapshot.evidence_id and r["evidence_rows"] == snapshot.rows
                for r in record["recommendations"]
            )
    result.update(
        benchmark_sha256=checksum,
        summary=summarize(result["records"]),
        by_category={
            category: summarize([r for r in result["records"] if r["category"] == category])
            for category in sorted({c["category"] for c in cases})
        },
    )
    (output / "report.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"summary": result["summary"], "audit": result["audit"]}, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--benchmark", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--questions", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        report = worker(args.source_root, args.questions, args.output)
        (args.output / "worker.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    else:
        if args.development == bool(args.benchmark):
            parser.error("Choose exactly one of --development and --benchmark")
        cases = (
            development_cases() if args.development else load_benchmark(args.benchmark, args.sha256)
        )
        evaluate(cases, args.output, args.source_root, args.sha256)
