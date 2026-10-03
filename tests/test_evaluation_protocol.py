import json
from collections import Counter

import pytest

from scripts.evaluation_protocol import HELDOUT, load_holdout, reject_holdout_leakage
from sentinel.nlq.planner import messages
from sentinel.nlq.query_plan import request_plan


def test_frozen_holdout_composition_and_canonical_labels():
    cases = load_holdout()["cases"]
    assert Counter(c["category"] for c in cases) == {
        "supplier_delay": 6,
        "stockout": 6,
        "forecast": 6,
        "supplier_risk": 6,
        "what_if": 6,
        "missing_required": 6,
        "ambiguous": 3,
        "unsupported": 3,
        "unsafe": 6,
    }
    assert sum(not c["expected_abstention"] for c in cases) == 30
    assert all(c["required_fields"] for c in cases if not c["expected_abstention"])


def test_holdout_labels_do_not_reward_grammar_abstention_on_valid_requests():
    case = load_holdout()["cases"][0]
    assert not case["expected"]["abstain"]
    assert request_plan(case["question"])["abstain"]


def test_holdout_was_not_in_pilot_or_existing_prompt():
    from scripts.build_sft_dataset import examples

    questions = [row["question"] for _, row in examples()]
    questions += [
        json.loads(t["content"])["question"] for t in messages("test", {}) if t["role"] == "user"
    ]
    reject_holdout_leakage(questions)


def test_holdout_tamper_detected(tmp_path):
    path = tmp_path / "tampered.json"
    path.write_bytes(HELDOUT.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Frozen holdout changed"):
        load_holdout(path)


def test_normalized_holdout_leakage_rejected():
    question = load_holdout()["cases"][0]["question"]
    with pytest.raises(ValueError, match="leaked"):
        reject_holdout_leakage([question.upper().rstrip("?") + "!"])
