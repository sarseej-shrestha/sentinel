"""Question-only prediction, then separately supplied labels. No claim of independence.

Hashes detect changes relative to a separately retained digest; they do not
authenticate authorship. Only trusted source checkouts may be evaluated.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.plan_diagnostics import aggregate, diagnose
from sentinel.config import AS_OF
from sentinel.nlq.execution_contract import validate_execution_plan
from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA

METADATA_FIELDS = ("granularity", "date_basis", "supplier_scope", "abstention_reason")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_pinned(path, sha256):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Benchmark input is too large")
    if not sha256 or digest(path) != sha256:
        raise ValueError("Checksum mismatch")
    return json.loads(path.read_text())


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("Unexpected or missing fields")


def validate_questions(value):
    exact_keys(value, ("protocol", "benchmark_id", "reference_date", "provenance", "cases"))
    if value["protocol"] != "sentinel_blind_questions_v1":
        raise ValueError("Unsupported questions protocol")
    if value["reference_date"] != AS_OF:
        raise ValueError("Reference date differs from the fixed synthetic fixture")
    if not isinstance(value["benchmark_id"], str) or not value["benchmark_id"].strip():
        raise ValueError("Missing benchmark identifier")
    if value["provenance"] not in ("internal", "externally_authored_claimed"):
        raise ValueError("Provenance must be explicit; external authorship is not verified")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 500:
        raise ValueError("Expected 1..500 cases")
    ids, questions = set(), set()
    for case in cases:
        exact_keys(case, ("id", "question"))
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in ids:
            raise ValueError("Invalid or duplicate case ID")
        q = case["question"]
        if not isinstance(q, str) or len(q) > 2000 or q.casefold().strip() in questions:
            raise ValueError("Invalid or duplicate question")
        ids.add(case["id"])
        questions.add(q.casefold().strip())
    return value


def predict(questions_path, sha256, output, source_root):
    questions = validate_questions(load_pinned(questions_path, sha256))
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a fresh output directory")
    output.mkdir(parents=True)
    # The worker never receives labels, categories or expected fields.
    worker_input = output / "questions.json"
    worker_input.write_text(json.dumps([c["question"] for c in questions["cases"]]))
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("evaluate_semantic_repair.py").resolve()),
            "--worker",
            "--source-root",
            str(Path(source_root).resolve()),
            "--questions",
            str(worker_input),
            "--output",
            str(output),
        ],
        check=True,
    )
    worker = json.loads((output / "worker.json").read_text())
    for case, record in zip(questions["cases"], worker["records"], strict=True):
        if record["input"] != case["question"]:
            raise ValueError("Worker question alignment mismatch")
        record["case_id"] = case["id"]
    result = {
        "protocol": "sentinel_blind_predictions_v1",
        "benchmark_id": questions["benchmark_id"],
        "questions_sha256": sha256,
        "questions_manifest": questions,
        "independence_verified": False,
        **worker,
    }
    path = output / "predictions.json"
    path.write_text(json.dumps(result, indent=2, allow_nan=False))
    return {"predictions": str(path), "sha256": digest(path), "audit": result["audit"]}


def score(predictions_path, predictions_sha256, labels_path, labels_sha256):
    predictions = load_pinned(predictions_path, predictions_sha256)
    labels = load_pinned(labels_path, labels_sha256)
    if predictions.get("protocol") != "sentinel_blind_predictions_v1":
        raise ValueError("Unsupported predictions protocol")
    questions = validate_questions(predictions["questions_manifest"])
    exact_keys(labels, ("protocol", "benchmark_id", "questions_sha256", "cases"))
    if (
        labels["protocol"] != "sentinel_blind_labels_v1"
        or labels["benchmark_id"] != predictions["benchmark_id"]
        or labels["benchmark_id"] != questions["benchmark_id"]
        or labels["questions_sha256"] != predictions["questions_sha256"]
    ):
        raise ValueError("Labels do not belong to these frozen questions")
    gold = {}
    for row in labels["cases"]:
        exact_keys(row, ("id", "category", "expected"))
        if row["id"] in gold or not isinstance(row["category"], str):
            raise ValueError("Duplicate labels or invalid category")
        validate_execution_plan(row["expected"])
        gold[row["id"]] = row
    records = predictions["records"]
    ids = [c["id"] for c in questions["cases"]]
    if set(gold) != set(ids) or [r["case_id"] for r in records] != ids:
        raise ValueError("Case IDs must align exactly")
    scores = []
    for question, record in zip(questions["cases"], records, strict=True):
        if question["question"] != record["input"]:
            raise ValueError("Prediction input mismatch")
        expected = gold[record["case_id"]]["expected"]
        raw = record.get("resolved_query_plan")
        try:
            valid = validate_execution_plan(raw)
        except Exception:
            valid = None
        core = {k: expected[k] for k in QUERY_PLAN_SCHEMA["properties"]}
        diagnostic = diagnose(record.get("query_plan"), core)
        scores.append(
            {
                "category": gold[record["case_id"]]["category"],
                "diagnostic": diagnostic,
                "supported": not expected["abstain"],
                "unsafe": expected["intent"] == "unsafe",
                "exact_v2": valid is not None and valid == expected,
                "metadata": {
                    k: valid is not None and valid[k] == expected[k] for k in METADATA_FIELDS
                },
                "valid": valid is not None,
                "safe_abstention": valid is not None
                and valid["abstain"]
                and record["abstained"]
                and record["query_result"] is None,
                "fallback": record.get("fallback_used", False),
                "latency_ms": record["request_latency_ms"],
            }
        )
    import statistics

    def summarize(rows):
        return {
            "count": len(rows),
            "valid_plans": sum(r["valid"] for r in rows),
            "whole_plan_exact_v2": sum(r["exact_v2"] for r in rows),
            "supported_exact_v2": {
                "correct": sum(r["exact_v2"] for r in rows if r["supported"]),
                "total": sum(r["supported"] for r in rows),
            },
            "negative_safe_abstention": {
                "correct": sum(r["safe_abstention"] for r in rows if not r["supported"]),
                "total": sum(not r["supported"] for r in rows),
            },
            "unsafe_rejection": {
                "correct": sum(r["safe_abstention"] for r in rows if r["unsafe"]),
                "total": sum(r["unsafe"] for r in rows),
            },
            "fallback_count": sum(r["fallback"] for r in rows),
            "legacy_diagnostics": aggregate([r["diagnostic"] for r in rows]),
            "metadata_field_matches": {
                k: sum(r["metadata"][k] for r in rows) for k in METADATA_FIELDS
            },
            "latency_ms": {
                "minimum": min(r["latency_ms"] for r in rows),
                "mean": statistics.mean(r["latency_ms"] for r in rows),
                "median": statistics.median(r["latency_ms"] for r in rows),
                "maximum": max(r["latency_ms"] for r in rows),
            },
        }

    return {
        "provenance": questions["provenance"],
        "independence_verified": False,
        "promotion": "blocked_pending_external_review",
        "predictions_sha256": predictions_sha256,
        "labels_sha256": labels_sha256,
        "summary": summarize(scores),
        "by_category": {
            c: summarize([r for r in scores if r["category"] == c])
            for c in sorted({r["category"] for r in scores})
        },
        "audit": predictions["audit"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prediction = commands.add_parser("predict")
    prediction.add_argument("--questions", type=Path, required=True)
    prediction.add_argument("--sha256", required=True)
    prediction.add_argument("--output", type=Path, required=True)
    prediction.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    scoring = commands.add_parser("score")
    scoring.add_argument("--predictions", type=Path, required=True)
    scoring.add_argument("--predictions-sha256", required=True)
    scoring.add_argument("--labels", type=Path, required=True)
    scoring.add_argument("--labels-sha256", required=True)
    args = parser.parse_args()
    result = (
        predict(args.questions, args.sha256, args.output, args.source_root)
        if args.command == "predict"
        else score(args.predictions, args.predictions_sha256, args.labels, args.labels_sha256)
    )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
