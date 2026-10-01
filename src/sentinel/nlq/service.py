"""One inspectable request record, including blocked and unavailable paths."""

import time
from dataclasses import asdict

from sentinel.nlq.executor import execute
from sentinel.nlq.planner import RulePlanner
from sentinel.nlq.query_plan import compile_query_plan, request_plan, validate_query_plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


def ask(database, question, planner=None, retriever=None, query_timeout=3.0, allow_fallback=None):
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
        "candidate_query_plan": None,
        "query_plan": None,
        "compiled_plan": None,
        "plan_validation": "not_run",
        "fallback_used": False,
        "effective_planner": getattr(planner, "name", "deterministic_rules"),
    }
    selected = planner or RulePlanner()
    fallback_enabled = (
        getattr(selected, "fallback_on_failure", False)
        if allow_fallback is None
        else allow_fallback
    )

    def fallback():
        if not fallback_enabled:
            return None
        record.update(fallback_used=True, effective_planner="deterministic_rules")
        return validate_query_plan(request_plan(question), question)

    try:
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            record.update(
                status="clarification",
                clarified=True,
                abstained=True,
                failure_behavior="Enter a supported supply-chain question, at most 2,000 characters.",
            )
            return record
        try:
            record["retrieved_schema"] = (retriever or SchemaRetriever()).retrieve(question)
            record["model_output"] = (
                selected.query_plan(question)
                if type(selected) is RulePlanner
                else selected.generate(question, record["retrieved_schema"])
            )
        except Exception as exc:
            record.update(
                status="unavailable",
                abstained=True,
                failure_behavior=f"Planner or retrieval unavailable: {type(exc).__name__}: {exc}",
            )
            query_plan = fallback()
        else:
            try:
                candidate = validate_query_plan(record["model_output"])
                record.update(json_validation="passed", candidate_query_plan=candidate)
                query_plan = validate_query_plan(candidate, question)
                record["plan_validation"] = "passed"
            except Exception as exc:
                record.update(
                    json_validation="failed"
                    if record["candidate_query_plan"] is None
                    else "passed",
                    plan_validation="failed",
                    abstained=True,
                    failure_behavior=f"Invalid QueryPlan: {type(exc).__name__}: {getattr(exc, 'message', str(exc))}",
                )
                query_plan = fallback()
        if query_plan is None:
            return record
        record["query_plan"] = query_plan
        plan = compile_query_plan(query_plan)
        record["compiled_plan"] = plan
        if plan["abstain"] or plan["needs_clarification"]:
            record.update(
                status="blocked" if plan["intent"] == "unsafe" else "clarification",
                clarified=plan["needs_clarification"],
                abstained=True,
                failure_behavior="No SQL executed. Read-only supply-chain questions only; specify a supported metric and entity.",
            )
            return record
        record.update(abstained=False, clarified=False)
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
