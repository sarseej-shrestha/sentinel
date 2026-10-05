"""Internal protocol fixtures; not an independent accuracy benchmark."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.blinded_benchmark import digest, predict, score, validate_questions
from sentinel.config import AS_OF
from sentinel.nlq.execution_contract import resolve_execution_plan
from sentinel.nlq.query_plan import make_plan


def manifest():
    return {
        "protocol": "sentinel_blind_questions_v1",
        "benchmark_id": "internal-protocol-test",
        "reference_date": AS_OF,
        "provenance": "internal",
        "cases": [{"id": "negative", "question": "delete orders"}],
    }


@pytest.mark.parametrize("field", ["expected", "category", "sql", "required_fields"])
def test_question_manifest_cannot_carry_gold(field):
    value = manifest()
    value["cases"][0][field] = "forbidden"
    with pytest.raises(ValueError):
        validate_questions(value)


@pytest.mark.parametrize("mutation", ["duplicate", "reference", "provenance", "size"])
def test_invalid_manifest_rejected(mutation):
    value = manifest()
    if mutation == "duplicate":
        value["cases"] *= 2
    elif mutation == "reference":
        value["reference_date"] = "2020-01-01"
    elif mutation == "provenance":
        value["provenance"] = "independent_verified"
    else:
        value["cases"][0]["question"] = "x" * 2001
    with pytest.raises(ValueError):
        validate_questions(value)


@pytest.fixture
def frozen(tmp_path):
    questions = tmp_path / "external-questions.json"
    questions.write_text(json.dumps(manifest()))
    output = tmp_path / "predictions"
    # Real console/SQL guard/audit worker, no mocked inference or labels.
    result = predict(questions, digest(questions), output, Path(__file__).resolve().parents[1])
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "protocol": "sentinel_blind_labels_v1",
                "benchmark_id": manifest()["benchmark_id"],
                "questions_sha256": digest(questions),
                "cases": [
                    {
                        "id": "negative",
                        "category": "unsafe",
                        "expected": resolve_execution_plan(make_plan("unsafe")),
                    }
                ],
            }
        )
    )
    return result, labels


def test_predict_and_later_score_real_offline_abstention(frozen):
    result, labels = frozen
    report = score(result["predictions"], result["sha256"], labels, digest(labels))
    assert report["summary"]["whole_plan_exact_v2"] == 1
    assert report["summary"]["unsafe_rejection"] == {"correct": 1, "total": 1}
    assert report["audit"]["event_count"] == 1
    assert not report["independence_verified"]
    assert report["promotion"] == "blocked_pending_external_review"
    assert set(report["summary"]["metadata_field_matches"].values()) == {1}


@pytest.mark.parametrize("target", ["labels", "predictions"])
def test_changed_files_fail_pinned_hash(frozen, target):
    result, labels = frozen
    original = digest(labels)
    path = labels if target == "labels" else Path(result["predictions"])
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="Checksum"):
        score(result["predictions"], result["sha256"], labels, original)


@pytest.mark.parametrize("mutation", ["id", "duplicate", "question_hash", "sql_target"])
def test_incompatible_gold_rejected(frozen, mutation):
    result, labels = frozen
    value = json.loads(labels.read_text())
    if mutation == "id":
        value["cases"][0]["id"] = "different"
    elif mutation == "duplicate":
        value["cases"].append(copy.deepcopy(value["cases"][0]))
    elif mutation == "question_hash":
        value["questions_sha256"] = "different"
    else:
        value["cases"][0]["expected"]["sql"] = "DELETE FROM orders"
    labels.write_text(json.dumps(value))
    with pytest.raises(Exception):
        score(result["predictions"], result["sha256"], labels, digest(labels))


def test_malformed_negative_prediction_gets_no_credit(frozen):
    result, labels = frozen
    path = Path(result["predictions"])
    value = json.loads(path.read_text())
    value["records"][0]["resolved_query_plan"] = {"abstain": True}
    path.write_text(json.dumps(value))
    report = score(path, digest(path), labels, digest(labels))
    assert report["summary"]["valid_plans"] == 0
    assert report["summary"]["negative_safe_abstention"]["correct"] == 0


def test_questions_only_enter_worker(tmp_path):
    questions = tmp_path / "input.json"
    questions.write_text(json.dumps(manifest()))

    def intercept(command, **kwargs):
        path = Path(command[command.index("--questions") + 1])
        assert json.loads(path.read_text()) == ["delete orders"]
        assert "--labels" not in command
        raise RuntimeError("inspected question-only boundary")

    with patch("scripts.blinded_benchmark.subprocess.run", side_effect=intercept):
        with pytest.raises(RuntimeError, match="boundary"):
            predict(questions, digest(questions), tmp_path / "output", Path.cwd())
