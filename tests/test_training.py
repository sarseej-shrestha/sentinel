import json
import subprocess
import sys


def test_sft_dataset_has_disjoint_splits_and_valid_json(tmp_path):
    from sentinel.nlq.planner import validate_plan
    from sentinel.nlq.sql_guard import guard_sql

    subprocess.run(
        [sys.executable, "scripts/build_sft_dataset.py", "--output", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    groups = {}
    for split, expected in (("train", 360), ("validation", 90), ("test", 90)):
        rows = [json.loads(line) for line in (tmp_path / f"{split}.jsonl").read_text().splitlines()]
        assert len(rows) == expected
        groups[split] = {r["entity_group"] for r in rows}
        for row in rows:
            assert row["source"] == "independent_synthetic_templates_v1"
            plan = validate_plan(row["messages"][-1]["content"])
            if plan["sql"]:
                guard_sql(plan["sql"], plan["parameters"])
    assert not groups["train"] & (groups["validation"] | groups["test"])
    assert not groups["validation"] & groups["test"]
