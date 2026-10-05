"""Contract-derived development cases, not benchmark examples."""

from unittest.mock import patch

import pytest

from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.intent_planners import clarification, interpret
from sentinel.nlq.query_plan import request_plan
from sentinel.nlq.registry import ENTITIES, extract_entity_slots
from sentinel.nlq.service import ask


@pytest.mark.parametrize(
    "kind,canonical,alias",
    [(k, c, a) for k, entries in ENTITIES.items() for c, names in entries.items() for a in names],
)
def test_slot_extraction_uses_entire_registry(kind, canonical, alias):
    entities, rest = extract_entity_slots(alias.lower())
    assert entities[kind] == canonical
    assert not rest.strip()
    assert sum(v is not None for v in entities.values()) == 1


@pytest.mark.parametrize("alias", ["Supplier Z", "Warehouse 8", "Product 99", "S2 and S3"])
def test_slot_extraction_rejects_unknown_or_conflicting_entities(alias):
    with pytest.raises(ValueError):
        extract_entity_slots(alias.lower())


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Rank suppliers by their late-arrival share during current month.", "supplier_delay"),
        ("Find inventory positions with less than 17 days of cover.", "stockout"),
        ("Daily demand forecasts for Product 4 at Warehouse 2.", "forecast"),
        ("What observations explain the risk rating assigned to Supplier C?", "supplier_risk"),
        ("With unchanged stock, simulate demand 13% higher in Warehouse 2.", "what_if"),
        ("Calculate monthly late delivery rate for Supplier B during June 2026.", "supplier_delay"),
    ],
)
def test_dedicated_development_planners(question, intent):
    assert interpret(question).plan["intent"] == intent
    assert request_plan(question)["intent"] == intent


@pytest.mark.parametrize(
    "question,reason,field",
    [
        ("Forecast daily demand for P4.", "missing_entity", "warehouse_id"),
        (
            "What if demand increases at Warehouse 2?",
            "missing_scenario",
            "scenario.demand_increase",
        ),
        ("Rank suppliers by late delivery rate.", "missing_or_ambiguous_date", "time_range"),
        ("Forecast demand for Product 99 in W2.", "unknown_or_ambiguous_entity", "entities"),
        ("Count of late deliveries by supplier in June 2026.", "unsupported_metric", "metrics"),
        (
            "Calculate monthly late delivery rate for suppliers in previous quarter.",
            "unsupported_granularity",
            "granularity",
        ),
        ("Forecast demand with a 20% increase for P4 in W2.", "ambiguous_intent", "intent"),
    ],
)
def test_missing_slots_are_actionable_abstentions(question, reason, field):
    result = clarification(question)
    assert result["reason"] == reason
    assert field in result["required_fields"]
    assert result["question"]
    assert request_plan(question)["abstain"]


def test_clarification_is_audited_without_execution(tmp_path):
    database = build_database(tmp_path / "clarification.duckdb")
    console = Console(database)
    with patch("sentinel.nlq.service.execute", side_effect=AssertionError("must not execute")):
        result = console.question("What if demand increases at Warehouse 2?")
    assert result["abstained"]
    assert result["clarification_request"]["reason"] == "missing_scenario"
    assert not result.get("recommendations")
    assert console.audit.verify()["event_count"] == 1


@pytest.mark.parametrize(
    "raw,kind",
    [
        ('{"type":"object","properties":{}}', "schema_echo"),
        ("not JSON", "malformed_output"),
        ('{"sql":"DELETE FROM orders"}', "invalid_contract"),
    ],
)
def test_shadow_failures_are_diagnosed_without_authority(raw, kind):
    class Shadow:
        name = "experimental"
        shadow_mode = True
        fallback_on_failure = True

        def generate(self, question, schema):
            return raw

    with patch("sentinel.nlq.service.execute", side_effect=AssertionError("must not execute")):
        result = ask("unused", "What if demand increases at Warehouse 2?", planner=Shadow())
    assert result["shadow_failure"] == kind
    assert result["fallback_used"] and result["abstained"]
    assert result["effective_planner"] == "deterministic_rules"
    assert result["compiled_plan"]["sql"] is None
