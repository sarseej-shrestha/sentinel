"""One inspectable request record, including blocked and unavailable paths."""

import time
from dataclasses import asdict

from sentinel.nlq.executor import execute
from sentinel.nlq.planner import RulePlanner, validate_plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


def ask(database, question, planner=None, retriever=None, query_timeout=3.0):
    start = time.perf_counter()
    record = {
        "input": question,
        "retrieved_schema": None,
        "model_output": None,
        "planner_backend": getattr(planner, "name", "deterministic_rules"),
        "json_validation": "not_run",
        "sql_validation": "not_run",
        "query_result": None,
        "status": "abstained",
        "clarified": False,
        "abstained": False,
        "failure_behavior": None,
    }
    try:
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            record.update(
                status="clarification",
                clarified=True,
                abstained=True,
                failure_behavior="Enter a supported supply-chain question, at most 2,000 characters.",
            )
            return record
        record["retrieved_schema"] = (retriever or SchemaRetriever()).retrieve(question)
        record["model_output"] = (planner or RulePlanner()).generate(
            question, record["retrieved_schema"]
        )
        try:
            plan = validate_plan(record["model_output"])
            record["json_validation"] = "passed"
        except Exception as exc:
            record.update(
                json_validation="failed",
                abstained=True,
                failure_behavior=f"Invalid planner JSON: {type(exc).__name__}",
            )
            return record
        if plan["abstain"] or plan["needs_clarification"]:
            record.update(
                status="blocked" if plan["intent"] == "unsafe" else "clarification",
                clarified=plan["needs_clarification"],
                abstained=True,
                failure_behavior="No SQL executed. Read-only supply-chain questions only; specify a supported metric and entity.",
            )
            return record
        try:
            guard_sql(plan["sql"], plan["parameters"])
            record["sql_validation"] = "passed"
        except SQLBlocked as exc:
            record.update(
                sql_validation="blocked",
                status="blocked",
                abstained=True,
                failure_behavior=str(exc),
            )
            return record
        result = execute(database, plan["sql"], plan["parameters"], timeout=query_timeout)
        record["query_result"] = asdict(result)
        record["status"] = result.status
        if result.status != "ok":
            record.update(abstained=True, failure_behavior=result.explanation)
        elif any(
            row.get("data_quality_flag", "ok") != "ok" or row.get("missing_field_count", 0) > 0
            for row in result.rows
        ):
            record.update(
                status="missing_information",
                abstained=True,
                failure_behavior="Incomplete or invalid evidence is shown; resolve data quality before a recommendation.",
            )
    except Exception as exc:
        record.update(
            status="unavailable",
            abstained=True,
            failure_behavior=f"Planner or retrieval unavailable: {type(exc).__name__}",
        )
    finally:
        record["latency_ms"] = (time.perf_counter() - start) * 1000
    return record
