import copy
import json
from collections import Counter
from unittest.mock import patch

import pytest

from scripts.build_sft_dataset import curated_examples
from scripts.compare_query_plans import RecordedOutput, RecordedRetrieval, score_record, summarize
from scripts.evaluation_protocol import load_holdout, normalized_question, reject_holdout_leakage
from scripts.instruction_curation import seed_rows
from scripts.plan_prompts import comparison_messages
from scripts.train_qlora import validate_instruction_splits
from sentinel.console import Console
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.query_plan import compile_query_plan, make_plan, validate_query_plan
from sentinel.nlq.sql_guard import guard_sql


@pytest.fixture(scope="module")
def instructions():
    return list(curated_examples())


def test_expanded_instruction_sizes_and_unique_questions(instructions):
    assert len(instructions) == 141
    assert Counter(split for split, _ in instructions) == {
        "train": 93,
        "validation": 24,
        "test": 24,
    }
    assert len({normalized_question(r["question"]) for _, r in instructions}) == 141
    assert Counter(r["category"] for r in seed_rows()) == {
        "supplier_delay": 10,
        "stockout": 10,
        "forecast": 10,
        "supplier_risk": 10,
        "what_if": 10,
        "missing_required": 10,
        "ambiguous": 8,
        "unsupported": 8,
        "unsafe": 12,
        "recovery": 12,
    }


def test_instruction_targets_and_context_exclude_holdout(instructions):
    all_questions = []
    for _, row in instructions:
        target = validate_query_plan(row["messages"][-1]["content"])
        assert "sql" not in target
        compiled = compile_query_plan(target)
        if not target["abstain"]:
            assert guard_sql(compiled["sql"], compiled["parameters"])
        all_questions.extend(
            json.loads(t["content"])["question"] for t in row["messages"] if t["role"] == "user"
        )
    reject_holdout_leakage(all_questions)
    splits = {
        split: [r for s, r in instructions if s == split]
        for split in ("train", "validation", "test")
    }
    validate_instruction_splits(splits)


def test_curated_preflight_rejects_changed_but_schema_valid_target(instructions):
    splits = {
        split: copy.deepcopy([r for s, r in instructions if s == split])
        for split in ("train", "validation", "test")
    }
    row = next(r for r in splits["train"] if r["source"] == "hand_curated_query_plan_v3")
    row["messages"][-1]["content"] = json.dumps(make_plan())
    with pytest.raises(ValueError, match="reviewed source label"):
        validate_instruction_splits(splits)


def test_curated_preflight_rejects_holdout_in_fewshot_context(instructions):
    splits = {
        split: copy.deepcopy([r for s, r in instructions if s == split])
        for split in ("train", "validation", "test")
    }
    row = splits["train"][0]
    row["messages"][1]["content"] = json.dumps({"question": load_holdout()["cases"][0]["question"]})
    with pytest.raises(ValueError, match="leaked"):
        validate_instruction_splits(splits)


def test_base_and_grounded_prompts_are_separate_and_have_no_gold_access():
    base = comparison_messages("test question", "base", {"secret_retrieval": "marker"})
    grounded = comparison_messages("test question", "grounded", {"test_retrieval": "marker"})
    assert len(base) == 2 and len(grounded) == 10
    assert "secret_retrieval" not in json.dumps(base)
    assert "test_retrieval" in json.dumps(grounded)
    train_questions = {r["question"] for r in seed_rows() if r["split"] == "train"}
    shots = [json.loads(t["content"])["question"] for t in grounded[1:-1] if t["role"] == "user"]
    assert set(shots) <= train_questions
    reject_holdout_leakage(shots)
    for turn in grounded:
        if turn["role"] == "assistant":
            assert validate_query_plan(turn["content"])


@pytest.mark.parametrize(
    "row", [r for r in seed_rows() if r["category"] == "recovery"], ids=lambda r: r["id"]
)
def test_recovery_targets_are_complete_sql_free_plans(row):
    target = validate_query_plan(row["target"])
    assert "sql" not in target and "previous_output" in row
    if "unspecified" in row["question"] or "unnamed" in row["question"]:
        assert target["abstain"]
    turns = comparison_messages(row["question"], "grounded", {}, row["previous_output"])
    assert json.loads(turns[-1]["content"])["previous_output"] == row["previous_output"]


def fake_record(candidate, selected=None, fallback=False):
    return {
        "candidate_query_plan": candidate,
        "query_plan": selected,
        "fallback_used": fallback,
        "abstained": True,
        "query_result": None,
        "status": "clarification",
        "request_latency_ms": 10,
    }


def test_semantic_gold_does_not_equate_valid_json_with_correct_plan():
    case = load_holdout()["cases"][0]
    record = score_record(case, "base", fake_record(make_plan()))
    assert record["primary_valid"] and not record["primary_exact_match"]


def test_invalid_output_receives_no_correct_negative_plan_credit():
    case = load_holdout()["cases"][-1]
    record = score_record(case, "base", fake_record(None))
    summary = summarize([record])["base"]
    assert summary["correct_negative_plan"]["numerator"] == 0
    assert summary["unsafe_request_rejection"]["numerator"] == 1
    assert summary["supported_semantic_match"]["rate"] is None


def test_fallback_cannot_inflate_model_only_score():
    case = load_holdout()["cases"][6]
    raw = fake_record(None, case["expected"], True)
    base = score_record(case, "grounded", raw)
    fallback = score_record(case, "fallback", raw)
    result = summarize([base, fallback])
    assert result["grounded"]["exact_semantic_match"]["numerator"] == 0
    assert result["fallback"]["exact_semantic_match"]["numerator"] == 1
    assert result["fallback"]["model_semantic_match_before_fallback"]["numerator"] == 0


def test_correct_novel_plan_cannot_bypass_runtime_grounding(tmp_path):
    # Use a development-derived unsupported modifier, not a frozen holdout question.
    row = next(r for r in seed_rows() if r["id"] == "curated_forecast_0")
    case = {"question": row["question"] + " Excluding promotional days.", "expected": row["target"]}
    database = build_database(tmp_path / "test.duckdb")
    with patch("sentinel.nlq.service.execute") as execute:
        console = Console(
            database,
            RecordedOutput(json.dumps(case["expected"]), "grounded"),
            RecordedRetrieval({}),
        )
        record = console.question(case["question"])
    execute.assert_not_called()
    from sentinel.nlq.shadow import classify

    assert classify(case["expected"], record["query_plan"])["disagrees"]
    assert record["candidate_query_plan"]["abstain"] and record["abstained"]
    assert console.audit.replay()[-1]["payload"] == record


def test_generation_error_is_audited_without_substitute_model_output(tmp_path):
    database = build_database(tmp_path / "error.duckdb")
    console = Console(
        database, RecordedOutput(None, "base", "explicit timeout injection"), RecordedRetrieval({})
    )
    record = console.question("Explain the risk for S1.")
    assert record["model_output"] is None and record["status"] == "ok"
    assert not record["abstained"] and not record["fallback_used"]
    assert console.audit.replay()[-1]["payload"] == record
