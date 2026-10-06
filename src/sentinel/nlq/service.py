"""Deterministic-only request execution. Model diagnostics never enter this path."""

import time
from dataclasses import asdict

from sentinel.nlq.authority import execution_abstention, resolve
from sentinel.nlq.execution_contract import resolve_execution_plan
from sentinel.nlq.executor import execute
from sentinel.nlq.planner import plan as abstention_plan
from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA, compile_query_plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


def ask(database, question, planner=None, retriever=None, query_timeout=3.0, allow_fallback=None):
    """Legacy planner/retriever/fallback arguments are ignored, never called.

    Use the explicit deferred shadow interface for optional model/retrieval work.
    """
    start = time.perf_counter()
    record = {
        "input": question,
        "retrieved_schema": None,
        "model_output": None,
        "planner_backend": "deterministic_rules",
        "json_validation": "not_run",
        "sql_validation": "not_run",
        "query_result": None,
        "status": "abstained",
        "clarified": False,
        "abstained": True,
        "failure_behavior": None,
        "candidate_query_plan": None,
        "query_plan": None,
        "compiled_plan": None,
        "plan_validation": "not_run",
        "fallback_used": False,
        "effective_planner": "deterministic_rules",
        "resolved_query_plan": None,
        "shadow_disagreement": None,
        "shadow_failure": None,
        "clarification_request": None,
        "resolution": None,
    }
    try:
        resolution = resolve(question)
        record["resolution"] = resolution
        full = resolution["plan"]
        core = {key: full[key] for key in QUERY_PLAN_SCHEMA["properties"]}
        record.update(
            query_plan=core,
            candidate_query_plan=core,
            resolved_query_plan=full,
            json_validation="passed",
            plan_validation="passed",
        )
        if isinstance(question, str) and question.strip() and len(question) <= 2000:
            record["retrieved_schema"] = SchemaRetriever().retrieve(question)
        if resolution["state"] != "supported":
            needs = resolution["state"] == "clarification_required"
            record.update(
                status="blocked" if resolution["state"] == "unsafe" else "clarification",
                clarified=needs,
                failure_behavior=resolution["message"],
                compiled_plan=abstention_plan(
                    core["intent"], abstain=True, needs_clarification=needs
                ),
                clarification_request={
                    "reason": resolution["reason_code"],
                    "required_fields": resolution["fields"],
                    "allowed_choices": resolution["allowed_choices"],
                    "question": resolution["message"],
                }
                if needs
                else None,
            )
            return record
        compiled = compile_query_plan(full)
        record["compiled_plan"] = compiled
        guard_sql(compiled["sql"], compiled["parameters"])
        record["sql_validation"] = "passed"
        result = execute(database, compiled["sql"], compiled["parameters"], timeout=query_timeout)
        record.update(
            query_result=asdict(result), status=result.status, abstained=result.status != "ok"
        )
        if result.status != "ok":
            record["failure_behavior"] = result.explanation
            execution_abstention(
                record, "empty_result" if result.status == "empty" else "execution_" + result.status
            )
        elif any(
            row.get("data_quality_flag", "ok") != "ok" or row.get("missing_field_count", 0) > 0
            for row in result.rows
        ):
            record.update(
                status="missing_information",
                abstained=True,
                failure_behavior="Incomplete or invalid evidence; resolve data quality before a recommendation.",
            )
            execution_abstention(record, "invalid_evidence")
    except Exception as exc:
        blocked = isinstance(exc, SQLBlocked)
        record.update(
            status="blocked" if blocked else "unavailable",
            abstained=True,
            sql_validation="blocked" if blocked else record["sql_validation"],
            failure_behavior="SQL rejected by safety validation."
            if blocked
            else "Deterministic analysis unavailable. No recommendation was created.",
        )
        if record["resolution"] is None:
            record["resolution"] = {
                "state": "unavailable",
                "plan": resolve_execution_plan(),
                "reason_code": "resolution_unavailable",
                "fields": ["request"],
                "allowed_choices": {},
                "message": record["failure_behavior"],
            }
            record["resolved_query_plan"] = record["resolution"]["plan"]
        execution_abstention(
            record,
            "sql_safety_rejection" if blocked else "analysis_unavailable",
            "sql" if blocked else "request",
        )
    finally:
        record["latency_ms"] = (time.perf_counter() - start) * 1000
    return record
