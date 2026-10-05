"""Curated synthetic NL-to-QueryPlan instructions, never database rows or SQL targets."""

import argparse
import json
import random
from pathlib import Path

from sentinel.config import SEED
from sentinel.nlq.planner import messages
from sentinel.nlq.query_plan import request_plan, validate_query_plan
from sentinel.nlq.retrieval import SchemaRetriever


def examples():
    retriever = SchemaRetriever()
    gold = json.loads(Path("data/sample/query_plan_gold.json").read_text())
    reserved = {row["question"].casefold().rstrip("?.!") for row in gold}
    for split, supplier, warehouse, product, horizons, percentages in (
        ("train", "S1", "W1", "P2", (3, 6, 9), (12, 25)),
        ("validation", "S2", "W2", "P4", (4, 8), (30, 45)),
        ("test", "S3", "W3", "P6", (5, 11), (50, 75)),
    ):
        cases = [
            f"Why is {supplier} considered high risk?",
            f"Show shipments for {supplier}",
            f"Forecast demand for Product {product} at {warehouse}",
            *[f"Find products with fewer than {n} days of stock remaining." for n in horizons],
            *[f"Simulate demand up {n}% in {warehouse}." for n in percentages],
            f"Predict demand at {warehouse}",  # Missing product.
            f"What happens if demand increases at {warehouse}",  # Missing fraction.
            f"Why is Supplier {dict(train='D', validation='E', test='F')[split]} considered high risk?",
            f"Forecast demand for Product {product} at Warehouse 99",
            f"Delete orders for {supplier}",
            f"Recommend a movie about {warehouse}",
        ]
        if split == "train":
            cases.append("Rank suppliers by last month's late delivery rate")
        for index, question in enumerate(cases):
            assert question.casefold().rstrip("?.!") not in reserved, "Gold evaluation leakage"
            target = validate_query_plan(request_plan(question), question)
            yield (
                split,
                {
                    "source": "synthetic_query_plan_instructions_v2",
                    "contract": "sql_free_query_plan_v1",
                    "instruction_group": f"{split}:{index}",
                    "question": question,
                    "messages": messages(question, retriever.retrieve(question))
                    + [{"role": "assistant", "content": json.dumps(target)}],
                },
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["pilot-v1", "curated-v2"], default="pilot-v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.output = args.output or Path(
        "data/training/query_plan_v2"
        if args.version == "curated-v2"
        else "data/training/query_plan_v1"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    rows = list(curated_examples() if args.version == "curated-v2" else examples())
    random.Random(SEED).shuffle(rows)
    for split in ("train", "validation", "test"):
        selected = [row for kind, row in rows if kind == split]
        with (args.output / f"{split}.jsonl").open("w") as stream:
            for row in selected:
                stream.write(json.dumps(row) + "\n")
        print(split, len(selected))


def curated_examples():
    """Retain the pilot; add hand-labeled semantics without broadening runtime authority."""
    from scripts.evaluation_protocol import normalized_question, reject_holdout_leakage
    from scripts.instruction_curation import seed_rows
    from scripts.plan_prompts import comparison_messages

    rows = list(examples())
    retriever = SchemaRetriever()
    for split, row in rows:
        row["category"] = json.loads(row["messages"][-1]["content"])["intent"]
        row["instruction_set"] = "curated-v2"
    for row in seed_rows():
        turns = comparison_messages(
            row["question"],
            "grounded",
            retriever.retrieve(row["question"]),
            row.get("previous_output"),
        )
        rows.append(
            (
                row["split"],
                {
                    "source": "hand_curated_query_plan_v3",
                    "contract": "sql_free_query_plan_v1",
                    "instruction_set": "curated-v2",
                    "instruction_group": row["id"],
                    "category": row["category"],
                    "question": row["question"],
                    "messages": turns
                    + [{"role": "assistant", "content": json.dumps(row["target"])}],
                },
            )
        )
    questions = [row["question"] for _, row in rows]
    if len(set(map(normalized_question, questions))) != len(questions):
        raise ValueError("Duplicate curated target question")
    reject_holdout_leakage(questions)
    # Check few-shot user messages too, not just final training targets.
    reject_holdout_leakage(
        [
            json.loads(turn["content"])["question"]
            for _, row in rows
            for turn in row["messages"]
            if turn["role"] == "user"
        ]
    )
    for _, row in rows:
        validate_query_plan(row["messages"][-1]["content"])
    yield from rows


if __name__ == "__main__":
    main()
