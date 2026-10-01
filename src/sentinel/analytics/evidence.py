"""Recommendations are built from verified snapshots and deterministic templates."""

import hashlib
import json

from sentinel.nlq.executor import QueryResult


def verify_snapshot(sql, parameters, rows, evidence_id):
    """Check snapshot integrity at both recommendation and proposal boundaries."""
    if (
        not isinstance(sql, str)
        or not sql.strip()
        or not isinstance(parameters, dict)
        or not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, dict) and row for row in rows)
        or not isinstance(evidence_id, str)
        or not evidence_id
    ):
        raise ValueError("Nonempty verified query evidence is required")
    digest = hashlib.sha256(
        json.dumps(
            {"sql": sql, "parameters": parameters, "rows": rows},
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    if digest != evidence_id:
        raise ValueError("Query evidence snapshot has changed since query execution")


def verified(result):
    if isinstance(result, dict):
        result = QueryResult(**result)
    if result.status != "ok":
        raise ValueError("Nonempty verified query evidence is required")
    verify_snapshot(result.sql, result.parameters, result.rows, result.evidence_id)
    return result


def recommendation(result, risk=None, forecast_output=None):
    result = verified(result)
    missing, assumptions, sources = (
        [],
        ["Synthetic demo only; human review is required."],
        ["review_template_rule_v1"],
    )
    if result.truncated:
        missing.append("Query reached its row cap; the evidence may be incomplete.")
    for row in result.rows:
        if row.get("data_quality_flag", "ok") != "ok":
            missing.append(row["data_quality_flag"])
    if risk:
        if not risk["evidence_rows"] or any(
            row not in result.rows for row in risk["evidence_rows"]
        ):
            raise ValueError("Risk evidence must belong to this verified query snapshot")
        missing.extend(risk["missing_information"])
        assumptions.extend(risk.get("assumptions", []))
        sources.append(risk["source"])
    if forecast_output:
        if forecast_output.get("evidence_id") != result.evidence_id:
            raise ValueError("Forecast must identify the verified input snapshot")
        sources.append(forecast_output["method"])
        assumptions.extend(forecast_output["warnings"])
    text = "Review the attached supply-chain evidence."
    if missing:
        text = "Resolve missing or invalid data before considering an operational change."
    elif risk and risk["risk_level"] == "high":
        text = {
            "stockout": "Review inventory coverage and simulate a replenishment proposal.",
            "late_delivery": "Review the shipment delay drivers and simulate a contingency note.",
            "supplier_reliability": "Review supplier delivery evidence and simulate a follow-up note.",
        }[risk["risk_type"]]
    return {
        "recommendation": text,
        "evidence_id": result.evidence_id,
        "evidence_rows": result.rows,
        "verified_sql": result.sql,
        "parameters": result.parameters,
        "sources": sources,
        "assumptions": list(dict.fromkeys(assumptions)),
        "missing_information": list(dict.fromkeys(missing)),
        "suggested_human_review": True,
        "simulated_action_only": True,
    }
