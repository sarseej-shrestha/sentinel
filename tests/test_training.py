import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_sft_dataset_has_disjoint_splits_and_valid_json(tmp_path):
    from sentinel.nlq.query_plan import validate_query_plan

    subprocess.run(
        [sys.executable, "scripts/build_sft_dataset.py", "--output", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    groups = {}
    reserved = {
        r["question"].casefold().rstrip("?.!")
        for r in json.loads(Path("data/sample/query_plan_gold.json").read_text())
    }
    for split, expected in (("train", 15), ("validation", 13), ("test", 13)):
        rows = [json.loads(line) for line in (tmp_path / f"{split}.jsonl").read_text().splitlines()]
        assert len(rows) == expected
        groups[split] = {r["question"].casefold().rstrip("?.!") for r in rows}
        assert not groups[split] & reserved
        for row in rows:
            assert row["source"] == "synthetic_query_plan_instructions_v2"
            assert row["contract"] == "sql_free_query_plan_v1"
            plan = validate_query_plan(row["messages"][-1]["content"], row["question"])
            assert "sql" not in plan
    assert not groups["train"] & (groups["validation"] | groups["test"])
    assert not groups["validation"] & groups["test"]


def test_instruction_preflight_accepts_all_three_curated_splits():
    from scripts.build_sft_dataset import examples
    from scripts.train_qlora import validate_instruction_splits

    splits = {key: [] for key in ("train", "validation", "test")}
    for split, row in examples():
        splits[split].append(row)
    validate_instruction_splits(splits)


@pytest.mark.parametrize("damage", ["legacy_contract", "sql_target", "duplicate_question"])
def test_instruction_preflight_rejects_legacy_sql_or_leakage(damage):
    from scripts.build_sft_dataset import examples
    from scripts.train_qlora import validate_instruction_splits

    splits = {key: [] for key in ("train", "validation", "test")}
    for split, row in examples():
        splits[split].append(row)
    if damage == "legacy_contract":
        del splits["train"][0]["contract"]
    elif damage == "sql_target":
        splits["train"][0]["messages"][-1]["content"] = '{"sql":"SELECT * FROM orders"}'
    else:
        splits["validation"].append(splits["train"][0])
    from jsonschema import ValidationError

    with pytest.raises((ValueError, ValidationError)):
        validate_instruction_splits(splits)
