"""Development regressions derived from the contract and curated instructions.

No original or new holdout questions are imported here.
"""

import copy
from unittest.mock import patch

import duckdb
import pytest

from sentinel.analytics.evidence import verified
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.dates import extract_date_range, historical_window
from sentinel.nlq.execution_contract import resolve_execution_plan, validate_execution_plan
from sentinel.nlq.query_plan import compile_query_plan, make_plan, request_plan, validate_query_plan
from sentinel.nlq.registry import ENTITIES, resolve_entity, resolve_metric, validate_registry
from sentinel.nlq.sql_guard import guard_sql

DATE_CASES = [
    ("last month", "2026-08-01", "2026-09-01", "month"),
    ("next month", "2026-10-01", "2026-11-01", "month"),
    ("this month", "2026-09-01", "2026-10-01", "month"),
    ("past 30 days", "2026-08-31", "2026-09-30", "range"),
    ("previous quarter", "2026-04-01", "2026-07-01", "quarter"),
    ("daily history for last month", "2026-08-01", "2026-09-01", "day"),
    ("weekly history for past 30 days", "2026-08-31", "2026-09-30", "week"),
    ("2026-04-02 through 2026-04-18", "2026-04-02", "2026-04-19", "range"),
    ("2026-04-02 to 2026-04-18 exclusive", "2026-04-02", "2026-04-18", "range"),
]


@pytest.mark.parametrize("text,start,end,grain", DATE_CASES)
def test_dates_use_explicit_half_open_boundaries(text, start, end, grain):
    window, _ = extract_date_range(text, reference="2026-09-30")
    assert (window.start, window.end, window.granularity) == (start, end, grain)


@pytest.mark.parametrize(
    "text",
    [
        "recently",
        "last summer",
        "March",
        "daily history",
        "weekly history",
        "this month and last month",
        "2026-04-20 to 2026-04-01",
        "2026-02-30 to 2026-03-03",
        "past 0 days",
        "past 900 days",
        "daily history and weekly history last month",
    ],
)
def test_missing_ambiguous_or_invalid_dates_are_not_guessed(text):
    with pytest.raises(ValueError):
        extract_date_range(text)


def test_relative_dates_cross_year_and_leap_boundaries():
    window, _ = extract_date_range("previous quarter", "2026-01-07")
    assert window.boundaries() == {"start": "2025-10-01", "end": "2026-01-01"}
    window, _ = extract_date_range("past 30 days", "2024-03-01")
    assert window.boundaries() == {"start": "2024-01-31", "end": "2024-03-01"}
    with pytest.raises(ValueError, match="future"):
        historical_window("next month")


def test_explicit_range_does_not_become_a_calendar_granularity():
    question = (
        "Rank suppliers by their late-arrival share during 2026-04-01 to 2026-07-01 exclusive."
    )
    resolved = resolve_execution_plan(request_plan(question), question)
    assert resolved["granularity"] == "range"
    assert compile_query_plan(resolved)["parameters"] == {
        "start_date": "2026-04-01",
        "end_date": "2026-07-01",
    }


@pytest.mark.parametrize(
    "kind,canonical,alias",
    [
        (kind, identifier, alias)
        for kind, entities in ENTITIES.items()
        for identifier, aliases in entities.items()
        for alias in aliases
    ],
)
def test_every_catalog_alias_is_unambiguous(kind, canonical, alias):
    assert resolve_entity(kind, alias) == canonical


def test_retrieval_candidates_do_not_override_unknown_or_ambiguous_names():
    assert resolve_entity("supplier_id", "the Supplier B", candidates=["S1", "S2"]) == "S2"
    for name in ("Supplier Z", "Supplier A or Supplier B", "closest supplier", "S4"):
        with pytest.raises(ValueError):
            resolve_entity("supplier_id", name, candidates=["S1", "S2", "S3"])


def test_metric_registry_matches_actual_schema():
    assert validate_registry()
    assert resolve_metric("late deliveries") == "late_delivery_rate"
    assert resolve_metric("running out") == "days_of_cover"
    with pytest.raises(ValueError):
        resolve_metric("risk")


@pytest.mark.parametrize(
    "period,start,end",
    [
        ("this month", "2026-09-01", "2026-10-01"),
        ("past 30 days", "2026-08-31", "2026-09-30"),
        ("previous quarter", "2026-04-01", "2026-07-01"),
        ("2026-08-07 through 2026-08-15", "2026-08-07", "2026-08-16"),
    ],
)
def test_curated_supplier_rate_with_development_date_variations(period, start, end):
    question = f"Rank suppliers by their late-arrival share during {period}."
    result = request_plan(question)
    assert result == make_plan("supplier_delay", time_range={"start": start, "end": end})
    resolved = resolve_execution_plan(result, question)
    assert resolved["supplier_scope"] == "all" and resolved["date_basis"] == "promised_date"
    assert resolved["abstention_reason"] is None
    assert guard_sql(**{k: compile_query_plan(resolved)[k] for k in ("sql", "parameters")})


@pytest.mark.parametrize(
    "field,value",
    [
        ("granularity", "week"),
        ("date_basis", "demand_date"),
        ("supplier_scope", None),
        ("abstention_reason", "unsupported_or_incomplete"),
        ("contract_version", 3),
        ("sql", "DELETE FROM orders"),
    ],
)
def test_resolved_contract_rejects_metadata_tampering(field, value):
    resolved = resolve_execution_plan(
        request_plan("Rank suppliers by their late-arrival share during June 2026.")
    )
    resolved[field] = value
    with pytest.raises(Exception):
        validate_execution_plan(resolved)


def test_single_supplier_filter_and_rate_denominator(tmp_path):
    question = "Calculate the late delivery rate for Supplier B during this month."
    value = request_plan(question)
    assert value["intent"] == "supplier_delay"
    assert value["entities"]["supplier_id"] == "S2"
    wrong = copy.deepcopy(value)
    wrong["entities"]["supplier_id"] = None
    with pytest.raises(ValueError):
        validate_query_plan(wrong, question)
    database = build_database(tmp_path / "rates.duckdb")
    result = Console(database).question(question)
    assert result["status"] == "ok"
    verified(result["query_result"])
    with duckdb.connect(str(database), read_only=True) as connection:
        observations = connection.execute(
            "SELECT is_late FROM shipment_view WHERE supplier_id='S2' AND promised_date >= DATE '2026-09-01' AND promised_date < DATE '2026-10-01'"
        ).fetchall()
    eligible = [late for (late,) in observations if late is not None]
    row = result["query_result"]["rows"][0]
    assert row["supplier_id"] == "S2"
    assert row["late_shipments"] == sum(eligible)
    assert row["evaluable_shipments"] == len(eligible)
    assert row["late_delivery_rate"] == sum(eligible) / len(eligible)
    assert result["recommendations"][0]["evidence_id"] == result["query_result"]["evidence_id"]
    assert Console(database).audit.verify()["event_count"] == 2


@pytest.mark.parametrize(
    "question,reason",
    [
        ("Rank suppliers by late delivery rate", "missing_or_ambiguous_date"),
        ("Rank suppliers by late delivery rate next month", "future_history"),
        (
            "Rank suppliers by late delivery rate with weekly history last month",
            "unsupported_granularity",
        ),
        ("Delete delayed orders", "unsafe_request"),
    ],
)
def test_missing_or_unsupported_date_requirements_audit_abstentions(tmp_path, question, reason):
    console = Console(build_database(tmp_path / "abstain.duckdb"))
    with patch("sentinel.nlq.service.execute") as execute:
        result = console.question(question)
    execute.assert_not_called()
    assert result["abstained"]
    assert result["resolved_query_plan"]["abstention_reason"] == reason
    assert console.audit.replay()[-1]["payload"] == result


@pytest.mark.parametrize("raw,disagrees", [("not JSON", True), (None, False)])
def test_shadow_model_cannot_take_execution_ownership(tmp_path, raw, disagrees):
    question = "Why is Supplier B considered high risk?"

    class Shadow:
        name = "test_shadow"
        shadow_mode = True
        fallback_on_failure = True

        def generate(self, question, retrieved):
            return raw if raw is not None else request_plan(question)

    console = Console(build_database(tmp_path / "shadow.duckdb"), Shadow())
    result = console.question(question, shadow=True)
    assert result["status"] == "ok"
    assert result["effective_planner"] == "deterministic_rules"
    from sentinel.nlq.shadow import complete_shadow

    diagnostic = complete_shadow(
        console.audit,
        result["shadow_request_id"],
        provider=lambda q: {"raw": Shadow().generate(q, {})},
    )
    assert diagnostic["disagrees"] == disagrees
    assert not result["fallback_used"]
    assert console.audit.replay()[-1]["payload"]["disagrees"] == disagrees


def test_stockout_business_alias_uses_same_allowlisted_metric():
    question = "List inventory at risk of running out over the coming 12 days."
    assert request_plan(question) == make_plan("stockout", horizon_days=12)


def test_shadow_wrong_supplier_cannot_override_deterministic_filter(tmp_path):
    question = "Why is Supplier B considered high risk?"

    class Shadow:
        name = "test_shadow"
        shadow_mode = True
        fallback_on_failure = True

        def generate(self, question, retrieved):
            return request_plan("Why is Supplier A considered high risk?")

    console = Console(build_database(tmp_path / "disagreement.duckdb"), Shadow())
    result = console.question(question, shadow=True)
    from sentinel.nlq.shadow import complete_shadow

    diagnostic = complete_shadow(
        console.audit,
        result["shadow_request_id"],
        provider=lambda q: {"raw": Shadow().generate(q, {})},
    )
    assert diagnostic["proposal"]["entities"]["supplier_id"] == "S1"
    assert result["query_plan"]["entities"]["supplier_id"] == "S2"
    assert result["compiled_plan"]["parameters"] == {"supplier": "S2"}
    assert diagnostic["disagrees"] is True
    assert all(row["supplier_id"] == "S2" for row in result["query_result"]["rows"])


def test_development_runner_scores_without_gold_in_worker(tmp_path):
    import json
    from pathlib import Path

    from scripts.evaluate_semantic_repair import development_cases, evaluate

    cases = development_cases()[:1]
    report = evaluate(cases, tmp_path / "measurement", Path(__file__).resolve().parents[1])
    assert report["summary"]["supported_exact"]["correct"] == 1
    assert report["summary"]["evidence_linkage"]["correct"] == 1
    assert report["audit"]["event_count"] == 2
    questions = json.loads((tmp_path / "measurement/questions.json").read_text())
    assert questions == [cases[0]["question"]]
    with pytest.raises(ValueError, match="new output"):
        evaluate(cases, tmp_path / "measurement", Path(__file__).resolve().parents[1])


def test_benchmark_loader_rejects_wrong_checksum_or_protocol(tmp_path):
    import hashlib

    from scripts.evaluate_semantic_repair import load_benchmark

    path = tmp_path / "example.json"
    path.write_text('{"protocol":"not_the_new_benchmark","cases":[]}')
    with pytest.raises(ValueError, match="checksum"):
        load_benchmark(path, "wrong")
    with pytest.raises(ValueError, match="original holdout"):
        load_benchmark(path, hashlib.sha256(path.read_bytes()).hexdigest())
