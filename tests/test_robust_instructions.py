"""Internal instruction validation, never an unseen accuracy claim."""

import json
from collections import Counter

import pytest

from scripts.robust_instruction_data import (
    grouped_splits,
    instruction_rows,
    near_duplicate,
    new_seeds,
    render,
)
from sentinel.nlq.execution_contract import validate_execution_plan
from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA, request_plan


@pytest.mark.parametrize("row", new_seeds(), ids=lambda r: r["id"])
def test_every_new_development_label_has_a_regression(row):
    target = validate_execution_plan(row["target"])
    expected_core = {k: target[k] for k in QUERY_PLAN_SCHEMA["properties"]}
    assert request_plan(row["question"]) == expected_core
    # Schema validation is not semantic proof: retain the independent core label.
    assert target["abstention_reason"] is not None if target["abstain"] else target["metrics"]


def test_versioned_set_has_reviewed_targets_and_no_shared_demonstrations():
    rows = instruction_rows()
    assert len(rows) == 177
    assert Counter(r["split"] for r in rows) == {"train": 131, "validation": 32, "development": 14}
    assert len({r["id"] for r in rows}) == len(rows)
    for row in rows:
        rendered = render(row)
        assert [m["role"] for m in rendered["messages"]] == ["system", "user", "assistant"]
        assert json.loads(rendered["messages"][1]["content"])["question"] == row["question"]
        target = validate_execution_plan(rendered["messages"][-1]["content"])
        assert target == row["target"]
        assert "sql" not in target
    assert sum(r["previous_output"] is not None for r in rows) == 14


def test_near_duplicate_families_do_not_cross_splits():
    rows = instruction_rows()
    for i, left in enumerate(rows):
        for right in rows[:i]:
            if near_duplicate(left["question"], right["question"]):
                assert left["family"] == right["family"]
                assert left["split"] == right["split"]


def test_group_assignment_is_reproducible_and_alias_aware():
    rows = [
        {"id": "a", "question": "Forecast demand for Product 1 at Warehouse 2"},
        {"id": "b", "question": "Forecast demand for P6 at W3"},
        {"id": "c", "question": "Explain supplier risk"},
    ]
    assert near_duplicate(rows[0]["question"], rows[1]["question"])
    assert grouped_splits(rows) == grouped_splits(list(reversed(rows)))


def test_recovery_target_never_copies_sql_or_missing_parameters():
    recovery = [r for r in new_seeds() if r["category"] == "recovery"]
    assert recovery[0]["target"]["intent"] == "forecast"
    assert "sql" not in recovery[0]["target"]
    assert recovery[1]["target"]["abstain"]
    assert recovery[1]["target"]["scenario"] is None
