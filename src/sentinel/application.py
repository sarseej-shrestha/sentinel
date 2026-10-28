"""Product orchestration over the existing deterministic analysis boundary."""

import copy
import hashlib
import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import duckdb

from sentinel.analytics.forecast import current_forecast_context
from sentinel.config import AS_OF
from sentinel.console import Console
from sentinel.investigation import capture, target_id
from sentinel.nlq.service import ask, execute_plan
from sentinel.state import StateStore, make_pack
from sentinel.tasks import Task


class Application:
    def __init__(self, database, state):
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise ValueError("Build the versioned synthetic database before launching")
        self.dataset_fingerprint = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.store = StateStore(state)
        with duckdb.connect(str(self.database), read_only=True) as con:
            con.execute("SELECT excluded_demand_count, demand_source_ids FROM risk_view LIMIT 0")
            con.execute("SELECT shipment_source_ids FROM supplier_view LIMIT 0")
        self.console = Console(self.database)

    def analyze(self, task: Task):
        task = Task.model_validate(task.model_dump())
        return self.analyze_question(task.question(), task)

    def analyze_question(self, question, task=None, *, query_timeout=3.0):
        started = time.perf_counter()
        if hashlib.sha256(self.database.read_bytes()).hexdigest() != self.dataset_fingerprint:
            raise ValueError(
                "The dataset changed. Restart with a verified dataset; saved evidence remains available."
            )
        record = (
            execute_plan(self.database, task.plan(), question, query_timeout)
            if task is not None
            else ask(self.database, question, query_timeout=query_timeout)
        )
        pack = None
        query = record.get("query_result")
        source = None
        if query and query.get("evidence_id"):
            source = capture(self.database, record["query_plan"])
            if hashlib.sha256(self.database.read_bytes()).hexdigest() != self.dataset_fingerprint:
                raise ValueError("Dataset changed while capturing evidence; no analysis was saved")
            pack = make_pack(query, self.dataset_fingerprint, AS_OF, source)
            if source and record["query_plan"]["intent"] != "forecast":
                valid = {t["id"] for t in source["targets"] if t["assessed"] and t["matching"]}
                rows = [
                    r
                    for r in query["rows"]
                    if target_id(record["query_plan"]["intent"], r) in valid
                    and r.get("data_quality_flag", "ok") == "ok"
                ]
                record = copy.deepcopy(record)
                query = record["query_result"]
                query["rows"] = rows
                query["evidence_id"] = hashlib.sha256(
                    json.dumps(
                        {"sql": query["sql"], "parameters": query["parameters"], "rows": rows},
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                query["status"] = "ok" if rows else "empty"
                record["status"] = (
                    "ok"
                    if rows
                    else "missing_information"
                    if source["quality"]["entities"]["unevaluable"]
                    else "empty"
                )
                record["failure_behavior"] = (
                    None
                    if rows
                    else "No evaluable matches. Inspect population quality and missing evidence."
                    if record["status"] == "missing_information"
                    else "No matching observations."
                )
        forecast_context = None
        if record.get("query_plan", {}).get("intent") == "forecast" and query:
            history = (
                [r["record"] for r in source["records"] if r["kind"] == "demand"]
                if source
                else query["rows"]
            )
            forecast_context = current_forecast_context(history, AS_OF)
            if forecast_context["freshness_status"] != "current":
                record["status"] = "missing_information"
                record["failure_behavior"] = (
                    "Current forecast unavailable: "
                    + forecast_context["freshness_status"].replace("_", " ")
                    + ". Supply at least 56 consecutive valid daily observations through "
                    + AS_OF
                    + ". No dates were filled or shifted."
                )
        error = record["failure_behavior"]
        if record["status"] == "ok":
            try:
                self.console._analyze(record, compact=True)
            except (ValueError, KeyError, TypeError):
                error = "The evidence is incomplete or invalid for this analysis. Review data quality before continuing."
                record["status"] = "missing_information"
                record.pop("recommendations", None)
        state = (
            "success"
            if record["status"] == "ok"
            else "empty"
            if record["status"] == "empty"
            else "uncertain"
            if record["status"] in {"clarification", "missing_information", "blocked"}
            else "error"
        )
        plan = record.get("resolved_query_plan") or {}
        result = {
            "analysis_id": str(uuid.uuid4()),
            "state": state,
            "task": task.model_dump() if task else None,
            "interpretation": question,
            "plan": plan,
            "reference_date": AS_OF,
            "dataset_fingerprint": self.dataset_fingerprint,
            "evidence_id": pack["id"] if pack else None,
            "row_count": len(query["rows"]) if query else 0,
            "message": error
            or "Analysis complete. Review the evidence before recording a simulated decision.",
            "warnings": ["Synthetic data. Decisions are simulations; no external actions occur."],
            "rows": [],
            "recommendations": [],
            "forecast": None,
            "forecast_context": forecast_context,
            "quality": source["quality"] if source else None,
            "targets": source["targets"] if source else [],
            "elapsed_ms": 0.0,
        }
        if source:
            q = source["quality"]
            if q["entities"]["unevaluable"] or any(
                v["excluded"] or v["unassignable"] for v in q["observations"].values()
            ):
                result["warnings"].append(
                    "This population includes unevaluable entities or excluded/unassignable records. Valid results are preserved; inspect population quality."
                )
        if pack:
            if pack["excluded_count"]:
                result["warnings"].append(
                    f"{pack['excluded_count']} observations were excluded from aggregate metrics. Inspect evidence before review."
                )
            if pack["truncated"]:
                result["warnings"].append(
                    "The query reached its row cap. This is incomplete evidence; proposals are disabled."
                )
            # A result is a compact presentation; complete source rows live in the evidence pack.
            hidden = {
                "source_dataset",
                "record_timestamp",
                "synthetic_entity_id",
                "demand_source_ids",
                "shipment_source_ids",
            }
            result["rows"] = [
                {k: v for k, v in row.items() if k not in hidden} for row in query["rows"]
            ]
        if state == "success" and pack:
            risks = record.get("risks", [])
            for i, risk in enumerate(risks):
                result["rows"][i]["review_level"] = risk["risk_level"]
                result["rows"][i]["review_reasons"] = "; ".join(
                    risk["drivers"] + risk["missing_information"]
                )
            if risks:
                result["warnings"].append(
                    "Review levels combine transparent rules with a synthetic classifier. They are not validated operational probabilities."
                )
            for i, rec in enumerate(record.get("recommendations", [])):
                if not pack["truncated"]:
                    indices = rec["target_indices"]
                    target = query["rows"][indices[0]]
                    label = (
                        " / ".join(
                            str(target[k])
                            for k in ("supplier_id", "product_id", "warehouse_id", "shipment_id")
                            if k in target
                        )
                        if len(indices) == 1
                        else f"All {len(indices)} result rows"
                    )
                    result["recommendations"].append(
                        {
                            "id": f"r{i + 1}",
                            "label": label,
                            "text": rec["recommendation"],
                            "target_row_ids": [
                                pack["row_ids"][pack["rows"].index(query["rows"][j])]
                                for j in indices
                            ],
                        }
                    )
            if "forecast" in record:
                forecast = record["forecast"]
                last = date.fromisoformat(forecast["last_observed_date"])
                result["forecast"] = {
                    "method": forecast["method"],
                    "history_days": forecast["history_days"],
                    "points": [
                        {
                            "date": (last + timedelta(days=i + 1)).isoformat(),
                            "forecast": point,
                            "baseline": forecast["baseline"][i],
                        }
                        for i, point in enumerate(forecast["point"])
                    ],
                }
                result["warnings"].append(
                    "Point estimates only. Interval calibration is insufficient; uncertainty bands are deliberately omitted."
                )
                result["warnings"].extend(
                    w
                    for w in forecast["warnings"]
                    if "interval" not in w.lower() and "confidence" not in w.lower()
                )
            if "what_if" in record:
                result["warnings"].append(
                    "Arithmetic sensitivity only: inventory, replenishment, prices and capacity stay constant. Not a causal forecast."
                )
        result["elapsed_ms"] = (time.perf_counter() - started) * 1000
        # Persist before returning success. Measurements around the HTTP request also include this commit and serialization.
        self.store.save_analysis(result, pack, source)
        json.dumps(result, allow_nan=False)
        return result
