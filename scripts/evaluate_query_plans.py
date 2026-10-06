"""Measure actual QueryPlan generation against frozen gold; never hide fallback use."""

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

import numpy as np

from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.executor import execute
from sentinel.nlq.planner import QwenPlanner
from sentinel.nlq.query_plan import compile_query_plan, request_plan, validate_query_plan
from sentinel.nlq.retrieval import SchemaRetriever


def source_hashes():
    paths = [
        *Path("src/sentinel").rglob("*.py"),
        Path(__file__),
        Path("data/sample/query_plan_gold.json"),
    ]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


class MeasuredPlanner:
    fallback_on_failure = True

    def __init__(self, model=None):
        self.model = model
        self.name = model.name if model else "deterministic_query_plan_reference"
        self.generation_ms = None

    def generate(self, question, retrieved):
        start = time.perf_counter()
        try:
            return (
                self.model.generate(question, retrieved) if self.model else request_plan(question)
            )
        finally:
            self.generation_ms = (time.perf_counter() - start) * 1000


def ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def summarize(records):
    latency = [r["end_to_end_ms"] for r in records]
    unsafe = [r for r in records if r["expected"]["intent"] == "unsafe"]
    supported = [r for r in records if not r["expected"]["abstain"]]
    negative = [r for r in records if r["expected"]["abstain"]]
    return {
        "calls": len(records),
        "query_plan_valid": ratio(
            sum(r["candidate_query_plan"] is not None for r in records), len(records)
        ),
        "exact_semantic_match_without_fallback": ratio(
            sum(r["model_exact_match"] for r in records), len(records)
        ),
        "supported_exact_match_without_fallback": ratio(
            sum(r["model_exact_match"] for r in supported), len(supported)
        ),
        "model_abstention_or_rejection": ratio(
            sum(
                r["plan_validation"] != "passed" or r["candidate_query_plan"]["abstain"]
                for r in records
            ),
            len(records),
        ),
        "final_abstention": ratio(sum(r["abstained"] for r in records), len(records)),
        "fallback": ratio(sum(r["fallback_used"] for r in records), len(records)),
        "supported_fallback": ratio(sum(r["fallback_used"] for r in supported), len(supported)),
        "correct_negative_abstention": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in negative), len(negative)
        ),
        "unsafe_request_rejection": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in unsafe), len(unsafe)
        ),
        "execution_matches_gold_including_fallback": ratio(
            sum(r["execution_matches_gold"] for r in supported), len(supported)
        ),
        "latency_ms": {
            "minimum": min(latency),
            "average": statistics.mean(latency),
            "median": statistics.median(latency),
            "p95": float(np.percentile(latency, 95)) if len(latency) >= 20 else None,
            "maximum": max(latency),
        },
    }


def run(output, backend="qwen", retrieval="bge", repeats=3):
    if not 1 <= repeats <= 20:
        raise ValueError("Use 1 to 20 repeats")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite an existing measurement database or its audit history.
    database = build_database(output / "synthetic.duckdb")
    gold = json.loads(Path("data/sample/query_plan_gold.json").read_text())
    sources = source_hashes()
    references = {}
    for case in gold:
        compiled = compile_query_plan(validate_query_plan(case["expected"]))
        if not case["expected"]["abstain"]:
            reference = execute(database, compiled["sql"], compiled["parameters"])
            if reference.status != "ok":
                raise ValueError(
                    f"Gold reference execution failed for {case['id']}: {reference.status}"
                )
            references[case["id"]] = reference.rows
    started = time.perf_counter()
    # A load failure terminates evaluation. It must never masquerade as inference.
    model = QwenPlanner() if backend == "qwen" else None
    retriever = SchemaRetriever(retrieval)
    planner = MeasuredPlanner(model)
    console = Console(database, planner, retriever)
    report = {
        "backend": planner.name,
        "retrieval": retrieval,
        "platform": platform.platform(),
        "initialization_ms": (time.perf_counter() - started) * 1000,
        "contract": "sql_free_query_plan_v1",
        "repeats": repeats,
        "measurement_scope": "Offline deterministic response followed by proposal generation/scoring; combined time is NOT user-facing request latency. Model-only scores use the separately generated proposal, never the deterministic response. Fallback selection is disabled. Repeats are not independent samples.",
        "records": [],
        "source_hashes": sources,
    }
    if model:
        import torch

        report["models"] = {
            "planner": model.model.config._name_or_path,
            "planner_revision": model.model.config._commit_hash,
            "device": str(model.model.device),
            "torch": torch.__version__,
            "embedding": retriever.encoder[0].auto_model.config._name_or_path
            if retrieval == "bge"
            else None,
        }
    for repeat in range(repeats):
        for case in gold:
            if source_hashes() != sources:
                raise RuntimeError(
                    "Source changed during evaluation; keep partial results separate and rerun"
                )
            started = time.perf_counter()
            record = console.question(case["question"])
            # Measure a proposal offline; never replace the authoritative plan.
            from sentinel.nlq.shadow import classify

            raw = planner.generate(case["question"], retriever.retrieve(case["question"]))
            diagnostic = classify(raw, record["query_plan"])
            record.update(
                model_output=raw,
                candidate_query_plan=diagnostic["proposal"],
                plan_validation="passed"
                if diagnostic["proposal_valid"] and not diagnostic["disagrees"]
                else "failed",
            )
            if source_hashes() != sources:
                raise RuntimeError(
                    "Source changed during evaluation; keep partial results separate and rerun"
                )
            record.update(
                case=case["id"],
                repeat=repeat,
                expected=case["expected"],
                end_to_end_ms=(time.perf_counter() - started) * 1000,
                generation_ms=planner.generation_ms,
                model_exact_match=record["candidate_query_plan"] == case["expected"],
                execution_matches_gold=(
                    record["status"] == "ok"
                    and (record["query_result"] or {}).get("rows") == references.get(case["id"])
                ),
            )
            report["records"].append(record)
            # Preserve progress even if the process is interrupted later.
            (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
            print(
                json.dumps(
                    {
                        key: record[key]
                        for key in (
                            "case",
                            "repeat",
                            "status",
                            "json_validation",
                            "plan_validation",
                            "model_exact_match",
                            "fallback_used",
                            "end_to_end_ms",
                        )
                    }
                ),
                flush=True,
            )
    report["summary"] = summarize(report["records"])
    report["audit"] = console.audit.verify()
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    (output / "audit_replay.json").write_text(
        json.dumps(console.audit.replay(), indent=2, allow_nan=False)
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/query_plan_evaluation"))
    parser.add_argument("--backend", choices=["qwen", "rules"], default="qwen")
    parser.add_argument("--retrieval", choices=["bge", "lexical"], default="bge")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.output, args.backend, args.retrieval, args.repeats)["summary"], indent=2
        )
    )
