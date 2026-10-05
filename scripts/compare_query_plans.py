"""Pretraining comparison on frozen semantic labels, with paired fallback replay.

No gold object is passed to the model or product gate. Model-only scores use parsed
proposals; production acceptance still uses the unchanged full-request grammar.
"""

import argparse
import hashlib
import json
import platform
import resource
import statistics
import time
from pathlib import Path

import numpy as np

from scripts.evaluation_protocol import HELDOUT, HELDOUT_SHA256, load_holdout
from scripts.plan_diagnostics import aggregate, diagnose
from scripts.plan_prompts import comparison_messages
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.planner import QwenPlanner, RulePlanner
from sentinel.nlq.retrieval import SchemaRetriever

ARMS = ("base", "grounded", "deterministic", "fallback")


def fingerprints():
    paths = [*Path("src/sentinel").rglob("*.py"), HELDOUT]
    paths += [
        Path("scripts") / name
        for name in (
            "compare_query_plans.py",
            "plan_prompts.py",
            "instruction_curation.py",
            "evaluation_protocol.py",
            "build_sft_dataset.py",
            "plan_diagnostics.py",
        )
    ]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


class RecordedOutput:
    """Replay one genuine generation to isolate the effect of enabling fallback."""

    def __init__(self, raw, arm, error=None):
        self.raw, self.error = raw, error
        self.name = f"qwen_comparison_{arm}"
        self.fallback_on_failure = arm == "fallback"

    def generate(self, question, retrieved):
        if self.error:
            raise RuntimeError(self.error)
        return self.raw


class RecordedRetrieval:
    def __init__(self, context):
        self.context = context

    def retrieve(self, question):
        return self.context


def ratio(n, d):
    return {"numerator": n, "denominator": d, "rate": n / d if d else None}


def latency(values):
    return {
        "minimum": min(values),
        "average": statistics.mean(values),
        "median": statistics.median(values),
        "maximum": max(values),
        "p95": float(np.percentile(values, 95)) if len(values) >= 20 else None,
    }


def score_record(case, arm, record, inference_ms=0):
    # Gold is used ONLY after the unchanged product pipeline has finished.
    candidate = record["candidate_query_plan"]
    primary = record["query_plan"] if arm == "fallback" else candidate
    return {
        **record,
        "arm": arm,
        "case": case["id"],
        "category": case["category"],
        "expected": case["expected"],
        "supported": not case["expected_abstention"],
        "model_exact_match": candidate == case["expected"],
        "primary_valid": primary is not None,
        "primary_exact_match": primary == case["expected"],
        "effective_exact_match": record["query_plan"] == case["expected"],
        "inference_ms": inference_ms,
        "proposal_diagnostics": diagnose(record.get("model_output", candidate), case["expected"]),
        "selected_diagnostics": diagnose(record["query_plan"], case["expected"]),
        # latency_ms is measured by service; console adds analytics/audit below.
    }


def metrics(records):
    supported = [r for r in records if r["supported"]]
    negative = [r for r in records if not r["supported"]]
    unsafe = [r for r in records if r["category"] == "unsafe"]
    return {
        "calls": len(records),
        "field_level_proposal": aggregate([r["proposal_diagnostics"] for r in records]),
        "field_level_selected": aggregate([r["selected_diagnostics"] for r in records]),
        "valid_query_plan": ratio(sum(r["primary_valid"] for r in records), len(records)),
        "exact_semantic_match": ratio(sum(r["primary_exact_match"] for r in records), len(records)),
        "supported_semantic_match": ratio(
            sum(r["primary_exact_match"] for r in supported), len(supported)
        ),
        "model_semantic_match_before_fallback": ratio(
            sum(r["model_exact_match"] for r in records), len(records)
        ),
        "fallback": ratio(sum(r["fallback_used"] for r in records), len(records)),
        "correct_negative_plan": ratio(
            sum(r["primary_exact_match"] for r in negative), len(negative)
        ),
        "negative_product_abstention": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in negative), len(negative)
        ),
        "unsafe_request_rejection": ratio(
            sum(r["abstained"] and r["query_result"] is None for r in unsafe), len(unsafe)
        ),
        "model_unsafe_plan_match": ratio(sum(r["model_exact_match"] for r in unsafe), len(unsafe)),
        "product_supported_success": ratio(
            sum(r["status"] == "ok" and r["effective_exact_match"] for r in supported),
            len(supported),
        ),
        "product_abstention": ratio(sum(r["abstained"] for r in records), len(records)),
        "request_latency_ms": latency([r["request_latency_ms"] for r in records]),
    }


def summarize(records):
    result = {}
    for arm in ARMS:
        selected = [r for r in records if r["arm"] == arm]
        if not selected:
            continue
        result[arm] = metrics(selected)
        result[arm]["by_category"] = {
            category: metrics([r for r in selected if r["category"] == category])
            for category in sorted({r["category"] for r in selected})
        }
    return result


def infer(model, turns):
    import torch

    inputs = model.tokenizer.apply_chat_template(
        turns,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    ).to(model.model.device)
    with torch.inference_mode():
        output = model.model.generate(
            **inputs,
            max_new_tokens=512,
            do_sample=False,
            max_time=30,
            pad_token_id=model.tokenizer.eos_token_id,
        )
    return model.tokenizer.decode(
        output[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True
    )


def run(output, repeats=1, rules_only=False):
    if not 1 <= repeats <= 3:
        raise ValueError("Use one to three repeats; repeats are not independent samples")
    cases = load_holdout()["cases"]
    # Check all target and few-shot questions before loading any models.
    from scripts.build_sft_dataset import curated_examples

    instructions = list(curated_examples())
    sources = fingerprints()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    database = build_database(output / "synthetic.duckdb")
    report = {
        "protocol": "heldout_comparison_v1",
        "diagnostics_version": "field_taxonomy_v1_additive_only",
        "holdout_exposure": "Individual cases were inspected in earlier sessions; not a perfectly untouched benchmark. Current development uses only instruction/development examples.",
        "heldout_sha256": HELDOUT_SHA256,
        "source_hashes": sources,
        "platform": platform.platform(),
        "repeats": repeats,
        "instruction_count": len(instructions),
        "training_executed": False,
        "arms": {
            "base": "Unmodified Qwen Instruct weights; zero-shot contract/catalog, no retrieval.",
            "grounded": "Same weights; predeclared detailed prompt, four training-only examples, BGE.",
            "deterministic": "Unchanged bounded rules and lexical retrieval.",
            "fallback": "Paired replay of grounded raw output through the unchanged enabled fallback.",
        },
        "latency_scope": "Base/grounded: observed retrieval+generation+product pipeline. Fallback: same observed inference cost plus separately measured paired replay, not an independent generation. Model loading excluded. All arms include validation/SQL when accepted/analytics/audit.",
        "scoring": "Base/grounded/rules primary scores use canonical proposals before runtime phrase grounding. Fallback primary scores use the selected product plan. Invalid outputs receive no negative-plan credit. Semantic gold never grants execution authority.",
        "records": [],
    }
    if not rules_only:
        start = time.perf_counter()
        try:
            model = QwenPlanner()
            bge = SchemaRetriever("bge")
        except Exception as exc:
            report["load_failure"] = f"{type(exc).__name__}: {exc}"
            (output / "report.json").write_text(json.dumps(report, indent=2))
            raise RuntimeError("Real model initialization failed; no fixture substitution") from exc
        report["initialization_ms"] = (time.perf_counter() - start) * 1000
        report["models"] = {
            "qwen": model.model.config._name_or_path,
            "qwen_revision": model.model.config._commit_hash,
            "bge": bge.encoder[0].auto_model.config._name_or_path,
            "bge_revision": bge.encoder[0].auto_model.config._commit_hash,
            "device": str(model.model.device),
        }
    lexical = SchemaRetriever()

    def process(case, arm, planner, retriever, cost=0, details=None):
        start = time.perf_counter()
        record = Console(database, planner, retriever).question(case["question"])
        elapsed = (time.perf_counter() - start) * 1000
        scored = score_record(case, arm, record, cost)
        scored.update(request_latency_ms=cost + elapsed, pipeline_ms=elapsed, **(details or {}))
        report["records"].append(scored)

    for repeat in range(repeats):
        for index, case in enumerate(cases):
            if fingerprints() != sources:
                raise RuntimeError("Evaluation source changed; discard incomplete comparison")
            process(case, "deterministic", RulePlanner(), lexical, details={"repeat": repeat})
            if not rules_only:
                # Counterbalance prompt-arm order to reduce warmup/order bias.
                for arm in ("base", "grounded") if index % 2 == 0 else ("grounded", "base"):
                    start = time.perf_counter()
                    context, raw, error, turns = {}, None, None, []
                    try:
                        context = bge.retrieve(case["question"]) if arm == "grounded" else {}
                        turns = comparison_messages(case["question"], arm, context)
                        raw = infer(model, turns)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    cost = (time.perf_counter() - start) * 1000
                    details = {
                        "repeat": repeat,
                        "prompt": turns,
                        "generation_error": error,
                        "prompt_sha256": hashlib.sha256(
                            json.dumps(turns, sort_keys=True).encode()
                        ).hexdigest(),
                        "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                        * (1 if platform.system() == "Darwin" else 1024),
                    }
                    process(
                        case,
                        arm,
                        RecordedOutput(raw, arm, error),
                        RecordedRetrieval(context),
                        cost,
                        details,
                    )
                    if arm == "grounded":
                        process(
                            case,
                            "fallback",
                            RecordedOutput(raw, "fallback", error),
                            RecordedRetrieval(context),
                            cost,
                            {**details, "paired_with": "grounded"},
                        )
            if fingerprints() != sources:
                raise RuntimeError("Evaluation source changed; discard incomplete comparison")
            (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
            print(
                json.dumps(
                    {"case": case["id"], "repeat": repeat, "records": len(report["records"])}
                ),
                flush=True,
            )
    report["summary"] = summarize(report["records"])
    report["audit"] = Console(database).audit.verify()
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--rules-only", action="store_true")
    args = parser.parse_args()
    report = run(args.output, args.repeats, args.rules_only)
    print(
        json.dumps(
            {
                arm: {k: v for k, v in summary.items() if k != "by_category"}
                for arm, summary in report["summary"].items()
            },
            indent=2,
        )
    )
