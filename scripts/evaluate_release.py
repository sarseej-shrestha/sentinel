"""Measure the shipped recursive forecasts and real HTTP pipeline; no model tuning.

Artifacts are local and ignored. Final chronological blocks are never used to
select hyperparameters or calibrate intervals. All fixtures are internal synthetic
material with prior exposure, not an independent generalization benchmark.
"""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import httpx
import numpy as np

from scripts.evaluation_protocol import load_holdout
from sentinel.analytics.forecast import forecast
from sentinel.application import Application
from sentinel.nlq.authority import resolve
from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA, validate_query_plan

TASKS = [
    {"kind": "supplier_delay", "start_date": "2026-08-01", "end_date": "2026-08-31"},
    {"kind": "stockout", "horizon_days": 14},
    {"kind": "forecast", "product_id": "P1", "warehouse_id": "W3"},
    {"kind": "supplier_risk", "supplier_id": "S1"},
    {"kind": "what_if", "warehouse_id": "W3", "increase_percent": 15.0},
]


def scores(truth, predicted):
    error = np.asarray(truth) - np.asarray(predicted)
    return {
        "n": int(error.size),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
    }


def forecast_evaluation(database):
    with duckdb.connect(str(database), read_only=True) as con:
        rows = con.execute(
            "SELECT product_id,warehouse_id,units FROM demand_view ORDER BY product_id,warehouse_id,demand_date"
        ).fetchall()
    series = {}
    for product, warehouse, units in rows:
        series.setdefault((product, warehouse), []).append(units)
    records = []
    for key, values in series.items():
        if len(values) < 98:
            continue
        final_origin = len(values) - 14
        origins = [(i, "development") for i in range(84, final_origin - 13, 14)] + [
            (final_origin, "final_chronological_block")
        ]
        for origin, split in origins:
            output = forecast(values[:origin], 14)
            records.append(
                {
                    "series": list(key),
                    "split": split,
                    "origin": origin,
                    "truth": values[origin : origin + 14],
                    "point": output["point"],
                    "baseline": output["baseline"],
                }
            )
    summary = {}
    for split in ("development", "final_chronological_block"):
        selected = [r for r in records if r["split"] == split]

        def report(items):
            truth = [v for r in items for v in r["truth"]]
            return {
                "forecast": scores(truth, [v for r in items for v in r["point"]]),
                "baseline": scores(truth, [v for r in items for v in r["baseline"]]),
            }

        summary[split] = {
            **report(selected),
            "origins": len(selected),
            "per_series": {
                "/".join(key): report([r for r in selected if r["series"] == list(key)])
                for key in series
            },
            "by_horizon": [
                {
                    "day": i + 1,
                    "forecast": scores(
                        [r["truth"][i] for r in selected], [r["point"][i] for r in selected]
                    ),
                    "baseline": scores(
                        [r["truth"][i] for r in selected], [r["baseline"][i] for r in selected]
                    ),
                }
                for i in range(14)
            ],
        }
    return {
        "scope": "Internal previously exposed synthetic series. No interval calibration or model selection performed. Identical origins/information for both methods.",
        "series_count": len(series),
        "summary": summary,
    }


def semantic_evaluation():
    records = []
    for case in load_holdout()["cases"]:
        resolution = resolve(case["question"])
        actual = {k: resolution["plan"][k] for k in QUERY_PLAN_SCHEMA["properties"]}
        expected = validate_query_plan(case["expected"])
        records.append(
            {
                "id": case["id"],
                "supported": not expected["abstain"],
                "accepted": not actual["abstain"],
                "correct": actual == expected,
                "state": resolution["state"],
                "category": case["category"],
            }
        )
    supported = [r for r in records if r["supported"]]
    accepted = [r for r in records if r["accepted"]]
    negative = [r for r in records if not r["supported"]]
    return {
        "scope": "Original exposed internal frozen holdout, plan-only; not independent validation",
        "whole_plan": [sum(r["correct"] for r in records), len(records)],
        "supported_correct": [sum(r["correct"] for r in supported), len(supported)],
        "accepted_correct": [sum(r["correct"] for r in accepted), len(accepted)],
        "clarification": [
            sum(r["state"] == "clarification_required" for r in records),
            len(records),
        ],
        "safe_negative": [sum(not r["accepted"] for r in negative), len(negative)],
        "records": records,
    }


def run(output, repeats):
    started = time.perf_counter()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "samples_per_task": repeats,
        "scope": "HTTP round-trip includes validation, worker startup, analytics, SQLite commit and response serialization. Successful requests measured separately from failures.",
    }
    with tempfile.TemporaryDirectory(prefix="sentinel-release-") as scratch:
        root = Path(scratch)
        command = [
            sys.executable,
            "-m",
            "sentinel.web",
            "--init",
            "--data-dir",
            str(root),
            "--port",
            "8777",
        ]
        server = None
        client = httpx.Client(
            base_url="http://127.0.0.1:8777", headers={"X-Sentinel-Client": "workspace"}, timeout=20
        )

        def start_server():
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            begin = time.perf_counter()
            while time.perf_counter() - begin < 30:
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                try:
                    if client.get("/api/catalog").status_code == 200:
                        return process, (time.perf_counter() - begin) * 1000
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            process.terminate()
            process.wait()
            raise TimeoutError("Local server did not start")

        try:
            server, report["cold_start_to_ready_ms"] = start_server()
            report["requests"] = {}
            report["spike_examples"] = []
            peak_rss = 0
            last = None
            for task in TASKS:
                latencies, sizes = [], []
                for i in range(repeats + 1):
                    begin = time.perf_counter()
                    response = client.post("/api/analyses", json=task)
                    elapsed = (time.perf_counter() - begin) * 1000
                    response.raise_for_status()
                    body = response.json()
                    if body["state"] != "success":
                        raise AssertionError(body)
                    sizes.append(len(response.content))
                    if i == 0:
                        first = elapsed
                        report["spike_examples"].append(
                            {"input": task, "elapsed_ms": elapsed, "output": body}
                        )
                    else:
                        latencies.append(elapsed)
                    last = body
                    usage = subprocess.check_output(
                        ["ps", "-o", "rss=", "-p", str(server.pid)], text=True
                    ).strip()
                    peak_rss = max(peak_rss, int(usage) * 1024)
                report["requests"][task["kind"]] = {
                    "n_successful_warm": len(latencies),
                    "first_request_ms": first,
                    "mean_ms": statistics.mean(latencies),
                    "p50_ms": float(np.percentile(latencies, 50)),
                    "p95_ms": float(np.percentile(latencies, 95)),
                    "max_ms": max(latencies),
                    "max_response_bytes": max(sizes),
                    "samples_ms": latencies,
                }
                print(
                    task["kind"],
                    json.dumps(
                        {
                            k: v
                            for k, v in report["requests"][task["kind"]].items()
                            if k != "samples_ms"
                        }
                    ),
                    flush=True,
                )
            report["sampled_server_peak_rss_bytes"] = peak_rss
            report["memory_scope"] = (
                "RSS sampled after every HTTP response; excludes transient worker peaks and is not system-wide peak memory."
            )
            proposal = client.post(
                "/api/proposals",
                json={
                    "key": "restart-proposal",
                    "analysis_id": last["analysis_id"],
                    "recommendation_id": last["recommendations"][0]["id"],
                    "reviewer": "Release test fixture",
                    "text": "Persisted pending draft for restart verification",
                },
            )
            proposal.raise_for_status()
            action = proposal.json()
            before = client.get("/api/history").json()
            server.terminate()
            server.wait(timeout=10)
            server, report["restart_to_ready_ms"] = start_server()
            after = client.get("/api/history").json()
            assert before == after
            decision_payload = {
                "key": "restart-decision",
                "decision": "approve",
                "reviewer": "Release test fixture",
                "expected_revision": 1,
            }
            decision = client.post(
                f"/api/proposals/{action['id']}/decisions", json=decision_payload
            )
            decision.raise_for_status()
            assert (
                client.post(
                    f"/api/proposals/{action['id']}/decisions", json=decision_payload
                ).json()
                == decision.json()
            )
            report["restart_and_retry"] = {
                "history_unchanged": True,
                "saved_draft_preserved": True,
                "identical_retry": True,
                "simulated_only": True,
            }
            report["controlled_failures"] = []
            begin = time.perf_counter()
            invalid = client.post("/api/analyses", json={"kind": "forecast"})
            assert invalid.status_code == 422
            report["controlled_failures"].append(
                {
                    "input": {"kind": "forecast"},
                    "status": 422,
                    "elapsed_ms": (time.perf_counter() - begin) * 1000,
                    "injected": False,
                    "output": invalid.json(),
                }
            )
            service = Application(root / "analytics.duckdb", root / "failure.sqlite3")
            begin = time.perf_counter()
            timed_out = service.analyze_question("Show shipments for Supplier A", query_timeout=0)
            assert timed_out["state"] == "error" and not timed_out["recommendations"]
            report["controlled_failures"].append(
                {
                    "input": "Show shipments for Supplier A",
                    "state": timed_out["state"],
                    "injected": True,
                    "injection": "zero query deadline",
                    "elapsed_ms": (time.perf_counter() - begin) * 1000,
                }
            )
            shipment = service.analyze_question("Show shipments for Supplier A")
            report["shipment_payload_bytes"] = len(json.dumps(shipment).encode())
            assert report["shipment_payload_bytes"] < 1024 * 1024
            report["forecast"] = forecast_evaluation(root / "analytics.duckdb")
            report["semantics"] = semantic_evaluation()
            report["frozen_sha256"] = {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    Path("data/sample/query_plan_heldout_v1.json"),
                    Path("data/sample/semantic_benchmark_v2.json"),
                ]
            }
            report["source_sha256"] = {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path("src/sentinel").rglob("*.py")
            }
            report["elapsed_seconds"] = time.perf_counter() - started
            (output / "release.json").write_text(json.dumps(report, indent=2, allow_nan=False))
            print("Measured release evidence:", output / "release.json", flush=True)
        finally:
            if server and server.poll() is None:
                server.terminate()
                server.wait(timeout=10)
            client.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/v1-release"))
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error("Use 1 through 100 successful measurements per task")
    run(args.output, args.repeats)
