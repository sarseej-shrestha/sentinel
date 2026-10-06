import copy
import json
from unittest.mock import patch

import pytest

from scripts.instruction_curation import seed_rows
from scripts.plan_diagnostics import FIELDS, aggregate, diagnose
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.query_plan import compile_query_plan, make_plan, request_plan, validate_query_plan
from sentinel.nlq.semantics import date_range
from sentinel.nlq.sql_guard import guard_sql

DEVELOPMENT = list(seed_rows())


@pytest.mark.parametrize("case", DEVELOPMENT, ids=lambda c: c["id"])
def test_development_plan_regression(case):
    actual = request_plan(case["question"])
    assert actual == case["target"]
    assert validate_query_plan(actual, case["question"]) == actual
    compiled = compile_query_plan(actual)
    if not actual["abstain"]:
        assert guard_sql(compiled["sql"], compiled["parameters"])
    else:
        assert compiled["sql"] is None


@pytest.mark.parametrize(
    "question",
    [
        "Build a daily 14-day demand projection for P4 in Warehouse 2 excluding returns.",
        "Build a daily 14-day demand projection for P4 in Warehouse 2 for 30 days.",
        "Build a daily 14-day demand projection for P4 and P5 in Warehouse 2.",
        "Build a daily 14-day demand projection for P4 in Warehouse 2 or W3.",
        "Build a daily 14-day demand projection for P4 in Warehouse 2 without promotions.",
        "Build a daily 14-day demand projection for P4 in Warehouse 99.",
        "Build a daily 14-day demand projection for Product Q in Warehouse 2.",
        "Find inventory positions with more than 23 days of cover.",
        "Find inventory positions with at least 23 days of cover.",
        "Find inventory positions with less than 0 days of cover.",
        "Find inventory positions with less than 91 days of cover.",
        "Find inventory positions with less than 23 days of cover at Warehouse 1.",
        "Show inventory for the last 23 days.",
        "With unchanged stock, simulate demand 18% lower in Warehouse 2.",
        "With unchanged stock, simulate demand 18% higher in Warehouse 2 and 25% higher in W1.",
        "With unchanged stock, simulate demand -18% higher in Warehouse 2.",
        "With unchanged stock, simulate demand 218% higher in Warehouse 2.",
        "With unchanged stock, simulate demand higher in Warehouse 2.",
        "Simulate demand without an 18% increase in Warehouse 2.",
        "Rank suppliers by their late-arrival share during June 2027.",
        "Rank suppliers by their late-arrival share during June 2026 and May 2026.",
        "Rank suppliers by their late-arrival share during the first 32 days of June 2026.",
        "Back up Supplier B's reliability assessment with shipment evidence at W1.",
        "Forecast demand for P1 at W1 for the next 60 days.",
    ],
)
def test_unrepresentable_modifiers_never_get_dropped(question):
    assert request_plan(question)["abstain"]


@pytest.mark.parametrize(
    "expression,start,end",
    [
        ("December 2025", "2025-12-01", "2026-01-01"),
        ("February 2024", "2024-02-01", "2024-03-01"),
        ("first quarter of 2026", "2026-01-01", "2026-04-01"),
        ("calendar month before March 2026", "2026-02-01", "2026-03-01"),
        ("2026-05-04 through 2026-05-11 inclusive", "2026-05-04", "2026-05-12"),
        ("2026-05-04 to 2026-05-11 exclusive", "2026-05-04", "2026-05-11"),
    ],
)
def test_date_slot_arithmetic(expression, start, end):
    span, rest = date_range(expression.lower())
    assert span == {"start": start, "end": end}
    assert not rest.strip()


@pytest.mark.parametrize(
    "field,label,value",
    [
        ("intent", "wrong_intent", "stockout"),
        (
            "entities",
            "wrong_entity_normalization",
            {"supplier_id": None, "product_id": "P4", "warehouse_id": "W99"},
        ),
        (
            "entities",
            "missing_entity_filter",
            {"supplier_id": None, "product_id": "P4", "warehouse_id": None},
        ),
        ("time_range", "wrong_date_range", {"start": "2026-06-01", "end": "2026-07-01"}),
        ("metrics", "wrong_metric", ["profit"]),
        ("group_by", "wrong_grouping", ["product_id"]),
        ("evidence_requirements", "missing_evidence_requirement", ["units"]),
        ("abstain", "incorrect_abstention", True),
    ],
)
def test_development_error_taxonomy(field, label, value):
    expected = next(r["target"] for r in DEVELOPMENT if r["id"] == "curated_forecast_0")
    raw = copy.deepcopy(expected)
    raw[field] = value
    result = diagnose(raw, expected)
    assert label in result["errors"]
    assert not result["fields"][field] and not result["whole_plan_exact"]


def test_missing_scenario_and_wrong_fraction_are_distinct():
    expected = next(r["target"] for r in DEVELOPMENT if r["id"] == "curated_what_if_0")
    for value, error in (
        (None, "missing_what_if_parameter"),
        ({"demand_increase": 18}, "wrong_what_if_parameter"),
    ):
        raw = {**expected, "scenario": value}
        assert error in diagnose(raw, expected)["errors"]


def test_invalid_output_has_no_field_credit_and_no_executable_plan():
    expected = DEVELOPMENT[0]["target"]
    report = diagnose("Here is a plan: {}", expected)
    assert "formatting_failure" in report["errors"]
    assert not any(report["fields"].values())
    assert not report["whole_plan_exact"]


def test_partial_credit_does_not_hide_invalid_contract():
    expected = DEVELOPMENT[0]["target"]
    raw = {**expected, "sql": "DELETE FROM orders"}
    result = diagnose(raw, expected)
    assert all(result["fields"].values())
    assert not result["schema_valid"] and not result["whole_plan_exact"]


def test_negative_proposal_acceptance_is_separate_from_actual_execution():
    result = diagnose(DEVELOPMENT[0]["target"], make_plan())
    assert "unsupported_request_incorrectly_accepted" in result["errors"]


def test_fields_accept_canonical_aliases_but_not_duplicates_or_missing_nulls():
    expected = next(r["target"] for r in DEVELOPMENT if r["id"] == "curated_forecast_0")
    raw = copy.deepcopy(expected)
    raw["entities"]["warehouse_id"] = "Warehouse 2"
    assert diagnose(raw, expected)["whole_plan_exact"]
    raw["metrics"].append("units")
    del raw["time_range"]
    result = diagnose(raw, expected)
    assert not result["fields"]["metrics"] and not result["fields"]["time_range"]
    summary = aggregate([result])
    assert set(summary["field_matches"]) == set(FIELDS)


def test_new_development_coverage_retains_ast_and_audit_protections(tmp_path):
    database = build_database(tmp_path / "development.duckdb")
    console = Console(database)
    question = next(c["question"] for c in DEVELOPMENT if c["id"] == "curated_what_if_0")
    result = console.question(question)
    assert result["status"] == "ok" and result["sql_validation"] == "passed"
    assert result["compiled_plan"]["parameters"] == {"increase": 0.18, "warehouse": "W2"}
    assert console.audit.replay()[-1]["payload"] == result
    assert all(r["warehouse_id"] == "W2" for r in result["query_result"]["rows"])


def test_schema_valid_model_plan_with_wrong_development_filter_cannot_execute(tmp_path):
    from scripts.compare_query_plans import RecordedOutput

    case = next(c for c in DEVELOPMENT if c["id"] == "curated_forecast_0")
    wrong = copy.deepcopy(case["target"])
    wrong["entities"]["warehouse_id"] = "W1"
    console = Console(
        build_database(tmp_path / "blocked.duckdb"), RecordedOutput(json.dumps(wrong), "base")
    )
    with patch("sentinel.nlq.service.execute", side_effect=RuntimeError("probe")) as execute:
        result = console.question(case["question"])
    execute.assert_called_once()
    assert execute.call_args.args[2] == {"product": "P4", "warehouse": "W2"}
    assert result["query_plan"] == case["target"]
    assert result["model_output"] is None
