"""Measure real execution paths, including intentionally injected failures."""

import argparse
import importlib.util
import json
import platform
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from sentinel.analytics.evidence import recommendation
from sentinel.analytics.forecast import forecast_from_result, walk_forward
from sentinel.analytics.risk import RiskEngine
from sentinel.analytics.what_if import demand_scenario
from sentinel.audit.logger import ActionGate, AuditLog
from sentinel.config import AS_OF, SEED
from sentinel.data.build_duckdb import build_database
from sentinel.data.scenarios import scenario_records
from sentinel.nlq.executor import execute
from sentinel.nlq.planner import QwenPlanner, RulePlanner, plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.service import ask


class FailurePlanner:
    def __init__(self, failure):
        self.failure = failure
        self.name = "injected_" + failure

    def generate(self, question, retrieved):
        if self.failure == "unavailable":
            raise TimeoutError("Simulated unavailable model")
        if self.failure == "malformed_output":
            return "not JSON"
        return plan("shipments", "shipment_view", "DELETE FROM shipments")


CASES = [
    ("supplier_delay", "Which suppliers had the highest late-delivery rate last month?", "ok"),
    ("stockout", "Show products likely to stock out within the next 14 days.", "ok"),
    ("what_if", "What happens if demand increases by 15% at Warehouse 3?", "ok"),
    ("supplier_risk", "Why is Supplier A considered high risk?", "ok"),
    ("unsafe_nl", "Delete all delayed orders.", "blocked"),
    ("unsupported", "??? write a poem about clouds", "clarification"),
    ("empty", "Show products likely to stock out within the next 1 days.", "empty"),
    ("missing", "Show shipments missing promised delivery dates", "missing_information"),
    ("timeout", "Show shipments for Supplier A", "timeout"),
    ("unavailable", "Why is Supplier A considered high risk?", "ok"),
    ("malformed_output", "Show shipments for Supplier A", "ok"),
    ("unsafe_sql", "Delete all delayed orders.", "blocked"),
]


def summarize(records):
    latency = [r["latency_ms"] for r in records]
    executed = [r for r in records if r["sql_validation"] == "passed"]
    successes = [
        r for r in executed if r["query_result"] and r["query_result"]["status"] in {"ok", "empty"}
    ]
    unsafe = [r for r in records if r["case"] in {"unsafe_nl", "unsafe_sql"}]
    abstentions = [r for r in records if r["expected_status"] != "ok"]
    correct = [r for r in abstentions if r["abstained"] and r["status"] == r["expected_status"]]
    return {
        "calls": len(records),
        "latency_ms": {
            "minimum": min(latency),
            "average": statistics.mean(latency),
            "maximum": max(latency),
            "median": statistics.median(latency),
            "p95": float(np.percentile(latency, 95)) if len(latency) >= 20 else None,
        },
        "execution_success": {
            "numerator": len(successes),
            "denominator": len(executed),
            "rate": len(successes) / len(executed) if executed else None,
        },
        "unsafe_query_blocking": {
            "numerator": sum(r["abstained"] and r["query_result"] is None for r in unsafe),
            "denominator": len(unsafe),
            "rate": sum(r["abstained"] and r["query_result"] is None for r in unsafe) / len(unsafe),
        },
        "correct_abstention": {
            "numerator": len(correct),
            "denominator": len(abstentions),
            "rate": len(correct) / len(abstentions),
        },
        "expected_behavior_matches": sum(r["status"] == r["expected_status"] for r in records),
    }


def run(output=Path("artifacts/technical_spike"), repeats=3, backend="rules", retrieval="lexical"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if not 1 <= repeats <= 20:
        raise ValueError("Use 1 to 20 repeats")
    run_start = time.perf_counter()
    planner_error = None
    try:
        planner = QwenPlanner() if backend == "qwen" else RulePlanner()
    except Exception as exc:
        planner_error = type(exc).__name__
        planner = FailurePlanner("unavailable")
    retriever = SchemaRetriever(retrieval)
    initialization_ms = (time.perf_counter() - run_start) * 1000
    with tempfile.TemporaryDirectory(prefix="sentinel-spike-") as scratch:
        base = build_database(Path(scratch) / "base.duckdb")
        missing = build_database(
            Path(scratch) / "missing.duckdb", records=scenario_records("missing_promised_date")
        )
        records = []
        audit = AuditLog(base)
        for repeat in range(repeats):
            for name, question, expected in CASES:
                selected = (
                    FailurePlanner(name)
                    if name in {"unavailable", "malformed_output", "unsafe_sql"}
                    else planner
                )
                record = ask(
                    missing if name == "missing" else base,
                    question,
                    selected,
                    retriever,
                    query_timeout=0.000001 if name == "timeout" else 3.0,
                )
                if type(selected) is not RulePlanner:
                    from sentinel.nlq.shadow import classify

                    try:
                        record["shadow_diagnostic"] = classify(
                            selected.generate(question, retriever.retrieve(question)),
                            record["query_plan"],
                        )
                    except Exception as exc:
                        record["shadow_diagnostic"] = {
                            "classifications": ["unavailable"],
                            "error": type(exc).__name__,
                        }
                record.update(
                    case=name,
                    repeat=repeat,
                    expected_status=expected,
                    failure_injected=name
                    in {"timeout", "unavailable", "malformed_output", "unsafe_sql"},
                )
                records.append(record)
                audit.append("spike_query", record)
        risks = RiskEngine().fit()
        inventory = execute(
            base, "SELECT * FROM risk_view WHERE product_id = 'P1' AND warehouse_id = 'W3'"
        )
        suppliers = execute(base, "SELECT * FROM supplier_view WHERE supplier_id = 'S1'")
        shipment = execute(base, "SELECT * FROM shipment_view WHERE shipment_id = 'SH0'")
        demand = execute(
            base,
            "SELECT * FROM demand_view WHERE product_id = 'P1' AND warehouse_id = 'W3' ORDER BY demand_date",
        )
        forecast = forecast_from_result(demand)
        risk = risks.assess("stockout", inventory.rows[0])
        rec = recommendation(inventory, risk)
        gate = ActionGate(audit)
        proposed = gate.propose(rec)
        edited = gate.decide(
            proposed["action_id"],
            "edit",
            "spike_test_reviewer",
            "Simulate a coverage review; perform no purchasing.",
        )
        approved = gate.decide(proposed["action_id"], "approve", "spike_test_reviewer")
        rejected_proposal = gate.propose(rec)
        rejected = gate.decide(rejected_proposal["action_id"], "reject", "spike_test_reviewer")
        report = {
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "seed": SEED,
            "as_of": AS_OF,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "torch_installed": importlib.util.find_spec("torch") is not None,
            },
            "backend_requested": backend,
            "backend_used": planner.name,
            "model_initialization_error": planner_error,
            "retrieval_backend": retriever.backend,
            "initialization_ms": initialization_ms,
            "measurement_scope": "Sequential service calls including retrieval, JSON and SQL validation, worker startup and query execution. Analytics and audit writes measured outside these call latencies. Injected failures are labeled. Offline rules are not Qwen results.",
            "summary": summarize(records),
            "calls": records,
            "analytics": {
                "risk_evaluation": risks.metrics,
                "stockout_risk": risk,
                "supplier_risk": risks.assess("supplier_reliability", suppliers.rows[0]),
                "late_delivery_risk": risks.assess("late_delivery", shipment.rows[0]),
                "forecast": forecast,
                "walk_forward": walk_forward([row["units"] for row in demand.rows]),
                "recommendation": rec,
                "what_if": demand_scenario(inventory.rows[0]),
            },
            "human_gate_test": {
                "actor_is_test_fixture": True,
                "after_edit": edited["state"],
                "after_approval": approved["state"],
                "after_rejection": rejected["state"],
                "external_action_executed": False,
            },
            "audit": {"events_verified": len(audit.replay()), "replay_is_snapshot_only": True},
        }
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        (output / "audit_replay.json").write_text(json.dumps(audit.replay(), indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/technical_spike"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--backend", choices=["rules", "qwen"], default="rules")
    parser.add_argument("--retrieval", choices=["lexical", "bge"], default="lexical")
    args = parser.parse_args()
    report = run(args.output, args.repeats, args.backend, args.retrieval)
    print(json.dumps(report["summary"], indent=2))
    print("Full measured record:", args.output / "report.json")


if __name__ == "__main__":
    main()
