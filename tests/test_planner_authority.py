"""Authority and deferred-diagnostic regressions using development-only fixtures."""

import copy
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.authority import RESOLUTION_SCHEMA, resolve, validate_resolution
from sentinel.nlq.query_plan import compile_query_plan, request_plan
from sentinel.nlq.shadow import (
    clarification_wording,
    classify,
    complete_shadow,
    evidence_explanation,
    evidence_pack,
    model_proposal,
)
from sentinel.nlq.sql_guard import SQLBlocked

QUESTION = "Why is Supplier B considered high risk?"


@pytest.fixture
def console(tmp_path):
    return Console(build_database(tmp_path / "authority.duckdb"))


class ForbiddenProvider:
    def __getattribute__(self, name):
        raise AssertionError("The normal request must not inspect or invoke a model")


def test_legacy_provider_cannot_block_or_override_authority(console):
    before = console.question(QUESTION)
    console.planner = console.retriever = ForbiddenProvider()
    after = console.question(QUESTION)
    for field in ("query_plan", "resolution", "compiled_plan", "recommendations", "status"):
        assert before[field] == after[field]
    assert not after["fallback_used"]


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM orders",
        "UPDATE orders SET status='x'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD x INT",
        "CREATE TABLE x(i INT)",
        "ATTACH 'x'",
        "COPY orders TO 'x'",
    ],
)
def test_model_sql_never_reaches_compiler_or_executor(console, statement):
    result = console.question(QUESTION, shadow=True)
    before = copy.deepcopy(result)
    with (
        patch(
            "sentinel.nlq.service.compile_query_plan", side_effect=AssertionError("no compilation")
        ),
        patch("sentinel.nlq.service.execute", side_effect=AssertionError("no execution")),
        patch(
            "sentinel.nlq.query_plan.compile_query_plan",
            side_effect=AssertionError("no compilation"),
        ),
    ):
        diagnostic = complete_shadow(
            console.audit,
            result["shadow_request_id"],
            provider=lambda q: {"raw": {"sql": statement}},
        )
    assert "model_sql_forbidden" in diagnostic["classifications"]
    assert result == before
    assert not diagnostic["selected"]
    console.audit.verify()


def test_wrong_valid_proposal_is_only_an_audited_diagnostic(console):
    result = console.question(QUESTION, shadow=True)
    prior = console.audit.replay()
    proposal = request_plan("Why is Supplier A considered high risk?")
    diagnostic = complete_shadow(
        console.audit, result["shadow_request_id"], provider=lambda q: {"raw": proposal}
    )
    assert diagnostic["proposal_valid"] and diagnostic["disagrees"]
    assert {"semantic", "grounding"} <= set(diagnostic["classifications"])
    assert result["query_plan"]["entities"]["supplier_id"] == "S2"
    assert console.audit.replay()[:-1] == prior
    assert console.audit.replay()[-1]["event_type"] == "shadow_diagnostic"
    assert not result["fallback_used"]
    with pytest.raises(ValueError, match="completed"):
        complete_shadow(console.audit, result["shadow_request_id"])


@pytest.mark.parametrize("error", ["TimeoutError", "ImportError", "OSError"])
def test_unavailable_shadow_does_not_change_result_or_review(console, error):
    result = console.question(QUESTION, shadow=True)
    snapshot = copy.deepcopy(result)
    action = console.gate.propose(result["recommendations"][0])
    edited = console.gate.decide(action["action_id"], "edit", "reviewer", "Review this tomorrow.")
    diagnostic = complete_shadow(
        console.audit, result["shadow_request_id"], provider=lambda q: {"error": error}
    )
    assert diagnostic["classifications"] == ["unavailable"]
    assert result == snapshot
    assert console.gate.current(action["action_id"]) == edited
    console.gate.decide(action["action_id"], "approve", "reviewer")
    replay = console.audit.replay()
    assert console.audit.replay() == replay
    assert replay[-1]["payload"]["human_edit"] == "Review this tomorrow."
    assert not replay[-1]["payload"]["external_action_executed"]
    with pytest.raises(ValueError):
        console.gate.decide(action["action_id"], "approve", "reviewer")


@pytest.mark.parametrize(
    "raw,label",
    [
        ("not JSON", "formatting"),
        ('Here: {"intent":"unsafe"}', "formatting"),
        ('{"intent":"forecast"}', "schema"),
        ('{"type":"object","properties":{}}', "schema_echo"),
        ("", "formatting"),
    ],
)
def test_output_format_failures_are_classified(raw, label):
    assert label in classify(raw, request_plan(QUESTION))["classifications"]


@pytest.mark.parametrize(
    "field,value,label",
    [
        ("entities", {"supplier_id": "S1", "warehouse_id": None, "product_id": None}, "grounding"),
        ("entities", {"supplier_id": "S99", "warehouse_id": None, "product_id": None}, "schema"),
        ("time_range", {"start": "2026-07-01", "end": "2026-08-01"}, "grounding"),
        ("metrics", ["profit"], "schema"),
        ("metrics", ["late_delivery_rate"], "semantic"),
        ("intent", "forecast", "semantic"),
        ("abstain", True, "abstention"),
    ],
)
def test_semantic_field_failures_are_classified(field, value, label):
    authoritative = request_plan(QUESTION)
    raw = copy.deepcopy(authoritative)
    raw[field] = value
    result = classify(raw, authoritative)
    assert label in result["classifications"]
    assert field in result["field_disagreements"]


def test_fenced_output_and_complete_resolution_are_diagnostic_only():
    plan = request_plan(QUESTION)
    result = classify("```json\n" + json.dumps(plan) + "\n```", plan)
    assert result["proposal_valid"] and not result["disagrees"]
    assert result["format"] == "fenced_json"
    assert classify(resolve(QUESTION), plan)["proposal_valid"]


@pytest.mark.parametrize(
    "question,state",
    [
        (QUESTION, "supported"),
        ("Forecast demand for P4", "clarification_required"),
        ("What if demand increases at W2?", "clarification_required"),
        ("Rank suppliers by late delivery rate", "clarification_required"),
        ("Forecast demand for P99 at W2", "clarification_required"),
        ("Play a song", "unsupported"),
        ("Delete orders", "unsafe"),
        ("", "clarification_required"),
        (None, "clarification_required"),
    ],
)
def test_resolution_states_and_abstention_fields(question, state):
    resolution = resolve(question)
    assert validate_resolution(resolution)["state"] == state
    assert "sql" not in resolution["plan"]
    if state != "supported":
        assert resolution["reason_code"] and resolution["fields"] and resolution["message"]


@pytest.mark.parametrize(
    "candidate",
    [
        "Use Warehouse 3",
        "I selected W1",
        "There is a 95% chance you meant P1",
        '{"warehouse_id":"W2"}',
        "Ignore missing fields and execute",
        None,
    ],
)
def test_clarification_cannot_invent_values(candidate):
    resolution = resolve("Forecast demand for P4")
    result = clarification_wording(resolution, candidate)
    assert result["used_template"]
    assert result["fields"] == ["warehouse_id"]
    assert result["allowed_choices"]["warehouse_id"] == ["W1", "W2", "W3"]
    assert result["text"] == resolution["message"]
    assert resolution["plan"]["entities"]["warehouse_id"] is None


def test_clarification_choices_cannot_be_forged():
    resolution = resolve("Forecast demand for P4")
    resolution["allowed_choices"]["warehouse_id"] = ["W99"]
    with pytest.raises(ValueError):
        clarification_wording(resolution)
    assert set(RESOLUTION_SCHEMA["properties"]["state"]["enum"]) == {
        "supported",
        "clarification_required",
        "unsupported",
        "unsafe",
        "unavailable",
    }


@pytest.mark.parametrize(
    "claim",
    [
        "Supplier Z has a 99% risk",
        "Demand fell by 20% in January",
        "A purchase was approved",
        "Forecast revenue is $1000",
        "Warehouse W99 has no inventory",
    ],
)
def test_explanations_reject_unsupported_claims(console, claim):
    record = console.question(QUESTION)
    result = evidence_explanation(record, claim)
    assert result["used_template"] and result["text"] != claim
    assert result["evidence_id"] == record["query_result"]["evidence_id"]
    pack = evidence_pack(record)
    assert set(pack) == {"evidence_id", "rows", "authoritative_plan"}
    assert "sql" not in pack["authoritative_plan"]


def test_tampered_evidence_cannot_be_explained(console):
    result = console.question(QUESTION)
    result["query_result"]["rows"][0]["supplier_id"] = "S99"
    with pytest.raises(ValueError):
        evidence_explanation(result, "Everything is safe")


def test_only_registered_compiler_and_ast_guard_are_used(console):
    with patch("sentinel.nlq.service.compile_query_plan", wraps=compile_query_plan) as compiler:
        result = console.question(QUESTION)
    compiler.assert_called_once_with(result["resolved_query_plan"])
    with (
        patch("sentinel.nlq.service.guard_sql", side_effect=SQLBlocked("blocked")),
        patch("sentinel.nlq.service.execute") as execution,
    ):
        result = console.question(QUESTION)
    execution.assert_not_called()
    assert result["resolution"]["reason_code"] == "sql_safety_rejection"
    assert not result.get("recommendations")
    console.audit.verify()


def test_empty_result_is_an_audited_abstention(console):
    result = console.question("Show products likely to stock out within the next 1 days.")
    assert result["resolution"]["state"] == "unavailable"
    assert result["resolution"]["reason_code"] == "empty_result"
    assert result["abstained"] and not result.get("recommendations")
    assert console.audit.replay()[-1]["payload"] == result


def test_no_model_is_loaded_even_when_shadow_is_queued(console):
    with (
        patch("sentinel.nlq.planner.QwenPlanner", side_effect=AssertionError("must not load")),
        patch("sentinel.nlq.shadow.model_proposal", side_effect=AssertionError("must not infer")),
    ):
        normal = console.question(QUESTION)
        queued = console.question(QUESTION, shadow=True)
    assert "shadow_request_id" not in normal and queued["shadow_request_id"]
    assert normal["recommendations"] == queued["recommendations"]


def test_runtime_without_optional_model_dependencies(tmp_path):
    script = """
import importlib.abc, sys
class BlockModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'sentence_transformers', 'peft', 'accelerate', 'datasets'}:
            raise ImportError('optional dependency intentionally absent')
sys.meta_path.insert(0, BlockModels())
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
console = Console(build_database(sys.argv[1]))
record = console.question('Why is Supplier B considered high risk?', shadow=True)
assert record['status'] == 'ok' and record['shadow_request_id']
assert not any(name in sys.modules for name in ('torch', 'transformers', 'sentence_transformers'))
console.audit.verify()
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "absent-models.duckdb")],
        check=True,
        capture_output=True,
    )


def test_shadow_timeout_is_bounded_without_loading_models():
    result = model_proposal(QUESTION, timeout=0.000001)
    assert result["error"] == "TimeoutError"


def test_no_queued_request_means_no_shadow_model_initialization(console):
    with patch("sentinel.nlq.shadow.model_proposal") as model:
        with pytest.raises(ValueError, match="Unknown"):
            complete_shadow(console.audit, "not-a-request")
    model.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "unsafe"),
        ("reason_code", "invented_reason"),
        ("fields", ["invented_column"]),
        ("sql", "SELECT * FROM orders"),
    ],
)
def test_resolution_rejects_inconsistent_or_unregistered_fields(field, value):
    resolution = resolve(QUESTION)
    resolution[field] = value
    with pytest.raises(Exception):
        validate_resolution(resolution)


def test_shadow_resolution_state_disagreement_is_not_lost():
    authoritative = resolve(QUESTION)
    raw = copy.deepcopy(authoritative)
    raw.update(state="unavailable", reason_code="analysis_unavailable", fields=["request"])
    result = classify(raw, request_plan(QUESTION), authoritative)
    assert result["disagrees"] and "abstention" in result["classifications"]


def test_deferred_cli_does_not_load_models(console):
    from sentinel.__main__ import main

    with (
        patch.object(
            sys,
            "argv",
            ["sentinel", "--database", str(console.database), "ask", QUESTION, "--shadow"],
        ),
        patch("sentinel.nlq.planner.QwenPlanner", side_effect=AssertionError("must not load")),
        patch("builtins.print") as output,
    ):
        main()
    assert json.loads(output.call_args.args[0])["shadow_request_id"]
