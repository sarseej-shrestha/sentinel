"""Deferred, diagnostic-only proposals. This module never compiles or executes SQL."""

import hashlib
import json
import multiprocessing
import platform
import resource
import time

from sentinel.nlq.query_plan import parse_output, validate_query_plan


def classify(raw, authoritative, authoritative_resolution=None):
    """Partial field diagnostics do not authorize a proposal."""
    result = {
        "proposal": None,
        "proposal_valid": False,
        "format": "raw_json",
        "classifications": [],
        "field_disagreements": [],
        "disagrees": True,
    }
    serialized = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    result["output_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
    if len(serialized) > 32768:
        result["classifications"] = ["formatting"]
        return result
    if isinstance(raw, str) and raw.strip().startswith("```"):
        result["format"] = "fenced_json"
    try:
        parsed = parse_output(raw)
    except (ValueError, TypeError):
        result["classifications"] = ["formatting"]
        return result
    if not isinstance(parsed, dict):
        result["classifications"] = ["schema"]
        return result
    if "state" in parsed or "plan" in parsed:
        from sentinel.nlq.authority import validate_resolution
        from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA

        try:
            resolution = validate_resolution(parsed)
        except Exception:
            result["classifications"] = ["schema"]
            return result
        result["resolution"] = resolution
        parsed = {k: resolution["plan"][k] for k in QUERY_PLAN_SCHEMA["properties"]}
    if {"type", "properties"} <= parsed.keys():
        result["classifications"] = ["schema", "schema_echo"]
        return result
    if any(key.casefold() in {"sql", "query", "statement"} for key in parsed):
        result["classifications"] = ["schema", "model_sql_forbidden"]
        return result
    result["field_disagreements"] = [k for k in authoritative if parsed.get(k) != authoritative[k]]
    try:
        proposal = validate_query_plan(parsed)
    except Exception:
        result["classifications"].append("schema")
    else:
        result.update(proposal=proposal, proposal_valid=True)
        if parsed.get("entities") != proposal["entities"]:
            result["classifications"].append("grounding")
        result["disagrees"] = proposal != authoritative
    fields = result["field_disagreements"]
    if "entities" in fields or "time_range" in fields:
        result["classifications"].append("grounding")
    if "abstain" in fields:
        result["classifications"].append("abstention")
    if fields:
        result["classifications"].append("semantic")
    result["classifications"] = sorted(set(result["classifications"]))
    if "resolution" in result and authoritative_resolution is not None:
        for field in ("state", "reason_code", "fields", "allowed_choices", "plan"):
            if result["resolution"][field] != authoritative_resolution[field]:
                result["field_disagreements"].append("resolution." + field)
                result["disagrees"] = True
                result["classifications"].append("abstention" if field == "state" else "semantic")
        result["classifications"] = sorted(set(result["classifications"]))
    return result


def _model_worker(pipe, question, retrieval):
    try:
        from sentinel.nlq.planner import QwenPlanner
        from sentinel.nlq.retrieval import SchemaRetriever

        start = time.perf_counter()
        planner = QwenPlanner()
        context = SchemaRetriever(retrieval).retrieve(question)
        raw = planner.generate_resolution(question, context)
        pipe.send(
            {
                "raw": raw,
                "retrieved_schema": context,
                "model_ms": (time.perf_counter() - start) * 1000,
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * (1 if platform.system() == "Darwin" else 1024),
            }
        )
    except Exception as exc:
        pipe.send({"error": type(exc).__name__})
    finally:
        pipe.close()


def model_proposal(question, retrieval="bge", timeout=60):
    if not 0 < timeout <= 120 or retrieval not in {"lexical", "bge"}:
        raise ValueError("Invalid shadow timeout or retrieval backend")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_model_worker, args=(send, question, retrieval))
    try:
        process.start()
        send.close()
        if not receive.poll(timeout):
            return {"error": "TimeoutError"}
        return receive.recv()
    except (EOFError, OSError):
        return {"error": "ModelUnavailable"}
    finally:
        if process.pid is not None:
            # Let a completed worker release tokenizer and retrieval resources.
            # Deadline failures still have bounded terminate/kill cleanup.
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
        receive.close()
        send.close()


def complete_shadow(audit, request_id, provider=None, retrieval="bge", timeout=60):
    """Run explicitly AFTER the answer; never mutate its record or any action.

    A supplied provider is a trusted test/offline diagnostic callable, not a model
    output hook on the request path. Normal loading is isolated and time-bounded.
    """
    events = audit.replay()
    pending = next(
        (
            e
            for e in events
            if e["event_id"] == request_id and e["event_type"] == "shadow_requested"
        ),
        None,
    )
    if pending is None:
        raise ValueError("Unknown shadow request")
    if any(
        e["event_type"] == "shadow_diagnostic" and e["payload"]["request_id"] == request_id
        for e in events
    ):
        raise ValueError("Shadow request already completed")
    query_id = pending["payload"]["query_event_id"]
    query = next(e for e in events if e["event_id"] == query_id and e["event_type"] == "query")
    authoritative = validate_query_plan(query["payload"]["query_plan"])
    question = query["payload"]["input"]
    started = time.perf_counter()
    try:
        measured = provider(question) if provider else model_proposal(question, retrieval, timeout)
        if measured.get("error"):
            diagnostic = {
                "classifications": ["unavailable"],
                "proposal_valid": False,
                "disagrees": None,
                "reason_code": "model_timeout"
                if measured["error"] == "TimeoutError"
                else "model_unavailable",
            }
        else:
            diagnostic = classify(measured["raw"], authoritative, query["payload"]["resolution"])
            diagnostic.update(
                {
                    k: measured[k]
                    for k in ("model_ms", "peak_rss_bytes", "retrieved_schema")
                    if k in measured
                }
            )
    except Exception:
        diagnostic = {
            "classifications": ["unavailable"],
            "proposal_valid": False,
            "disagrees": None,
            "reason_code": "model_unavailable",
        }
    diagnostic.update(
        request_id=request_id,
        query_event_id=query_id,
        query_event_hash=query["event_hash"],
        latency_ms=(time.perf_counter() - started) * 1000,
        selected=False,
        authority="deterministic_rules",
    )
    audit.append("shadow_diagnostic", diagnostic)
    return diagnostic


def clarification_wording(resolution, candidate=None):
    """Closed-set wording validation cannot fill in missing values."""
    from sentinel.nlq.authority import validate_resolution

    validate_resolution(resolution)
    if resolution["state"] != "clarification_required":
        raise ValueError("Clarification is not required")
    original = resolution["message"]
    allowed = [original, "Please clarify before analysis. " + original]
    return {
        "text": candidate if isinstance(candidate, str) and candidate in allowed else original,
        "used_template": candidate not in allowed,
        "fields": resolution["fields"],
        "allowed_choices": resolution["allowed_choices"],
    }


def evidence_pack(record):
    """Only verified source rows and authoritative semantic fields, never SQL."""
    from sentinel.analytics.evidence import verified
    from sentinel.nlq.authority import validate_resolution
    from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA
    from sentinel.nlq.sql_guard import guard_sql

    validate_resolution(record["resolution"])
    if record["abstained"] or record["resolution"]["state"] != "supported":
        raise ValueError("No supported evidence to explain")
    result = verified(record["query_result"])
    core = {k: record["resolution"]["plan"][k] for k in QUERY_PLAN_SCHEMA["properties"]}
    checked = guard_sql(record["compiled_plan"]["sql"], record["compiled_plan"]["parameters"])
    if (
        record["query_plan"] != core
        or checked.sql != result.sql
        or checked.parameters != result.parameters
    ):
        raise ValueError("Evidence does not match the authoritative result")
    return {
        "evidence_id": result.evidence_id,
        "rows": result.rows,
        "authoritative_plan": record["resolution"]["plan"],
    }


def evidence_explanation(record, candidate=None):
    """Free-form claims cannot be verified reliably; accept only exact safe templates."""
    pack = evidence_pack(record)
    text = f"The verified evidence contains {len(pack['rows'])} rows for {pack['authoritative_plan']['intent']}. Human review is required; no external action was performed."
    allowed = [text, "Based only on the verified snapshot: " + text]
    return {
        "text": candidate if isinstance(candidate, str) and candidate in allowed else text,
        "evidence_id": pack["evidence_id"],
        "used_template": candidate not in allowed,
    }
