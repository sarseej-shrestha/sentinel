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
    parser.add_argument("--output", type=Path, default=Path("data/training/query_plan_v1"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = list(examples())
    random.Random(SEED).shuffle(rows)
    for split in ("train", "validation", "test"):
        selected = [row for kind, row in rows if kind == split]
        with (args.output / f"{split}.jsonl").open("w") as stream:
            for row in selected:
                stream.write(json.dumps(row) + "\n")
        print(split, len(selected))


if __name__ == "__main__":
    main()
