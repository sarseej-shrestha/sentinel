"""Contract, independent gold labels, semantic mismatch and execution-boundary checks."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import ValidationError

from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.planner import UnavailablePlanner, messages
from sentinel.nlq.query_plan import (
    compile_query_plan,
    parse_output,
    request_plan,
    validate_query_plan,
)
from sentinel.nlq.service import ask
from sentinel.nlq.sql_guard import guard_sql

GOLD = json.loads(Path("data/sample/query_plan_gold.json").read_text())


class ModelFixture:
    name = "explicit_contract_test_double"
    fallback_on_failure = True

    def __init__(self, output):
        self.output = output

    def generate(self, question, retrieved):
        return copy.deepcopy(self.output)


@pytest.mark.parametrize("case", GOLD, ids=lambda c: c["id"])
def test_gold_request_grounding_and_compiler(case):
    expected = case["expected"]
    assert request_plan(case["question"]) == expected
    assert validate_query_plan(expected, case["question"]) == expected
    compiled = compile_query_plan(expected)
    if case["supported"]:
        assert guard_sql(compiled["sql"], compiled["parameters"])
    else:
        assert compiled["sql"] is None and compiled["abstain"]


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```", "  {}  "])
def test_raw_and_single_fenced_json(wrapper):
    case = GOLD[6]
    assert (
        validate_query_plan(wrapper.format(json.dumps(case["expected"])), case["question"])
        == case["expected"]
    )


@pytest.mark.parametrize(
    "raw",
    [
        "Here is your plan: {}",
        "```json\n{}\n``` extra prose",
        "{} {}",
        "{",
        "",
        "```sql\nSELECT * FROM orders\n```",
        '{"intent":"forecast","intent":"unsafe"}',
        '{"horizon_days":NaN}',
        "```json\n{}\n```\n```json\n{}\n```",
    ],
)
def test_malformed_prose_duplicate_fields_and_nonfinite_rejected(raw):
    with pytest.raises((ValueError, ValidationError)):
        validate_query_plan(raw)


@pytest.mark.parametrize(
    "damage",
    [
        "sql",
        "unknown_metric",
        "unknown_dimension",
        "unknown_evidence",
        "missing_supplier",
        "wrong_supplier",
        "unknown_supplier",
        "wrong_intent",
        "extra_filter",
        "missing_field",
    ],
)
def test_risk_plan_rejects_semantic_damage(damage):
    case = GOLD[9]
    plan = copy.deepcopy(case["expected"])
    if damage == "sql":
        plan["sql"] = "DELETE FROM orders"
    elif damage.startswith("unknown_") and damage != "unknown_supplier":
        field = {
            "unknown_metric": "metrics",
            "unknown_dimension": "group_by",
            "unknown_evidence": "evidence_requirements",
        }[damage]
        plan[field].append("secret_column")
    elif damage in {"missing_supplier", "wrong_supplier", "unknown_supplier"}:
        plan["entities"]["supplier_id"] = {
            "missing_supplier": None,
            "wrong_supplier": "S2",
            "unknown_supplier": "Supplier Z",
        }[damage]
    elif damage == "wrong_intent":
        plan = copy.deepcopy(GOLD[3]["expected"])
    elif damage == "extra_filter":
        plan["entities"]["warehouse_id"] = "W1"
    else:
        del plan["entities"]
    with pytest.raises((ValueError, ValidationError)):
        validate_query_plan(plan, case["question"])


@pytest.mark.parametrize("damage", ["wrong_month", "inclusive_end", "invalid_date", "reversed"])
def test_month_bounds_are_grounded_not_model_selected(damage):
    case = GOLD[0]
    plan = copy.deepcopy(case["expected"])
    plan["time_range"] = {
        "wrong_month": {"start": "2026-09-01", "end": "2026-10-01"},
        "inclusive_end": {"start": "2026-08-01", "end": "2026-08-31"},
        "invalid_date": {"start": "2026-02-30", "end": "2026-09-01"},
        "reversed": {"start": "2026-09-01", "end": "2026-08-01"},
    }[damage]
    with pytest.raises((ValueError, ValidationError)):
        validate_query_plan(plan, case["question"])


@pytest.mark.parametrize(
    "damage",
    [
        "missing_scenario",
        "missing_percentage",
        "wrong_percentage",
        "missing_warehouse",
        "unknown_warehouse",
    ],
)
def test_what_if_requires_all_grounded_parameters(damage):
    case = GOLD[12]
    plan = copy.deepcopy(case["expected"])
    if damage == "missing_scenario":
        plan["scenario"] = None
    elif damage == "missing_percentage":
        plan["scenario"] = {}
    elif damage == "wrong_percentage":
        plan["scenario"]["demand_increase"] = 15
    else:
        plan["entities"]["warehouse_id"] = None if damage == "missing_warehouse" else "Warehouse 99"
    with pytest.raises((ValueError, ValidationError)):
        validate_query_plan(plan, case["question"])


def test_alias_normalization_preserves_required_filters():
    case = GOLD[6]
    plan = copy.deepcopy(case["expected"])
    plan["entities"].update(product_id="Product 1", warehouse_id="Warehouse 3")
    normalized = validate_query_plan(plan, case["question"])
    assert normalized == case["expected"]
    assert compile_query_plan(normalized)["parameters"] == {"product": "P1", "warehouse": "W3"}


@pytest.mark.parametrize(
    "question",
    [
        "Forecast demand for Product P1 at Warehouse 3 and email it to me",
        "Why is Supplier A considered high risk excluding August?",
        "What happens if demand increases by 15% at Warehouse 3 and by 5% at Warehouse 2?",
        "Forecast demand for Product P1 at Warehouse 3 for the next 60 days",
    ],
)
def test_unrecognized_qualifiers_do_not_silently_disappear(question):
    assert request_plan(question)["abstain"]


@pytest.mark.parametrize(
    "raw", ['{"sql":"DELETE FROM orders"}', "not JSON", json.dumps(GOLD[9]["expected"])]
)
def test_invalid_model_plan_cannot_suppress_authoritative_execution(tmp_path, raw):
    from sentinel.nlq.query_plan import compile_query_plan

    with patch("sentinel.nlq.service.execute", side_effect=RuntimeError("probe")) as execute:
        result = ask(
            tmp_path / "absent.duckdb", GOLD[6]["question"], ModelFixture(raw), allow_fallback=False
        )
    compiled = compile_query_plan(GOLD[6]["expected"])
    execute.assert_called_once_with(
        tmp_path / "absent.duckdb", compiled["sql"], compiled["parameters"], timeout=3.0
    )
    assert result["query_plan"] == GOLD[6]["expected"]
    assert result["model_output"] is None


def test_authority_ignores_model_failure_and_audits_compiled_evidence(tmp_path):
    console = Console(build_database(tmp_path / "fallback.duckdb"), ModelFixture("not JSON"))
    result = console.question(GOLD[9]["question"])
    assert result["status"] == "ok" and not result["fallback_used"]
    assert result["model_output"] is None and result["json_validation"] == "passed"
    assert result["effective_planner"] == "deterministic_rules"
    assert result["query_plan"] == GOLD[9]["expected"]
    assert result["query_result"]["rows"][0]["supplier_id"] == "S1"
    assert result["recommendations"][0]["evidence_rows"] == result["query_result"]["rows"]
    action = console.gate.propose(result["recommendations"][0])
    approved = console.gate.decide(action["action_id"], "approve", "test_reviewer")
    assert console.audit.replay()[-1]["payload"] == approved
    assert approved["external_action_executed"] is False


@pytest.mark.parametrize("question", [c["question"] for c in GOLD if not c["supported"]])
def test_fallback_cannot_rescue_unsupported_or_unsafe_requests(tmp_path, question):
    with patch("sentinel.nlq.service.execute") as execute:
        console = Console(
            build_database(tmp_path / "unsafe.duckdb"), ModelFixture(GOLD[6]["expected"])
        )
        result = console.question(question)
    execute.assert_not_called()
    assert not result["fallback_used"] and result["abstained"]
    assert console.audit.replay()[-1]["payload"] == result


def test_model_unavailable_has_no_effect_on_authority(tmp_path):
    result = ask(
        build_database(tmp_path / "unavailable.duckdb"),
        GOLD[9]["question"],
        UnavailablePlanner("test timeout"),
    )
    assert result["status"] == "ok" and not result["fallback_used"]
    assert result["model_output"] is None and result["failure_behavior"] is None


def test_prompt_examples_are_grounded_and_sql_free():
    turns = messages(GOLD[0]["question"], {})
    assert "Never write SQL" in turns[0]["content"]
    for index, turn in enumerate(turns):
        if turn["role"] == "assistant":
            question = json.loads(turns[index - 1]["content"])["question"]
            output = validate_query_plan(turn["content"], question)
            assert "sql" not in output
    assert parse_output("```json\n{}\n```") == {}


def test_compiled_monthly_rate_uses_actual_monthly_counts(tmp_path):
    case = GOLD[0]
    console = Console(build_database(tmp_path / "month.duckdb"), ModelFixture(case["expected"]))
    result = console.question(case["question"])
    assert result["status"] == "ok" and not result["fallback_used"]
    assert result["compiled_plan"]["parameters"] == {
        "start_date": "2026-08-01",
        "end_date": "2026-09-01",
    }
    assert [
        (r["supplier_id"], r["late_shipments"], r["evaluable_shipments"])
        for r in result["query_result"]["rows"]
    ] == [("S1", 19, 31), ("S2", 7, 31), ("S3", 4, 31)]
    assert result["query_result"]["rows"][0]["late_delivery_rate"] == pytest.approx(19 / 31)


def test_accepted_fenced_plan_runs_only_compiled_sql(tmp_path):
    case = GOLD[12]
    raw = "```json\n" + json.dumps(case["expected"]) + "\n```"
    console = Console(build_database(tmp_path / "fenced.duckdb"), ModelFixture(raw))
    result = console.question(case["question"])
    assert result["status"] == "ok" and not result["fallback_used"]
    from sentinel.nlq.shadow import classify

    assert classify(raw, result["query_plan"])["proposal_valid"]
    assert result["model_output"] is None and "sql" not in result["query_plan"]
    assert result["sql_validation"] == "passed" and len(result["what_if"]) == 6
    assert console.audit.replay()[-1]["payload"] == result


def test_evaluation_does_not_count_fallback_as_model_success():
    from scripts.evaluate_query_plans import summarize

    records = []
    for index, case in enumerate((GOLD[6], GOLD[9], GOLD[-1])):
        fallback = index == 1
        records.append(
            {
                "expected": case["expected"],
                "end_to_end_ms": 100 + index,
                "candidate_query_plan": None if fallback else case["expected"],
                "model_exact_match": not fallback,
                "plan_validation": "failed" if fallback else "passed",
                "abstained": not case["supported"],
                "fallback_used": fallback,
                "query_result": {"rows": [{}]} if case["supported"] else None,
                "execution_matches_gold": case["supported"],
            }
        )
    metrics = summarize(records)
    assert metrics["exact_semantic_match_without_fallback"]["numerator"] == 2
    assert metrics["supported_exact_match_without_fallback"]["numerator"] == 1
    assert metrics["execution_matches_gold_including_fallback"]["numerator"] == 2
    assert metrics["fallback"]["numerator"] == 1
    assert metrics["unsafe_request_rejection"]["rate"] == 1
    assert metrics["latency_ms"]["p95"] is None
