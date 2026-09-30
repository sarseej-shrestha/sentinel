"""Produce independently synthetic planner examples with disjoint entity splits."""
import argparse
import json
import random
from pathlib import Path
from sentinel.config import SEED
from sentinel.nlq.planner import plan, messages, validate_plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.sql_guard import guard_sql


def examples():
    retriever = SchemaRetriever()
    for split, entities in (("train", range(1, 61)), ("validation", range(61, 76)), ("test", range(76, 91))):
        for entity in entities:
            cases = [
                (f"Show shipment evidence for supplier S{entity}.", plan("shipments", "shipment_view", "SELECT * FROM shipment_view WHERE supplier_id = $supplier", {"supplier": f"S{entity}"}, ["source_record_id", "promised_date"])),
                (f"Review supplier S{entity} reliability.", plan("supplier_risk", "supplier_view", "SELECT * FROM supplier_view WHERE supplier_id = $supplier", {"supplier": f"S{entity}"}, ["source_record_id", "evaluable_shipments", "late_delivery_rate"])),
                (f"Find inventory coverage below {entity} days.", plan("stockout", "risk_view", "SELECT * FROM risk_view WHERE days_of_cover < $horizon", {"horizon": entity}, ["source_record_id", "days_of_cover"])),
                (f"Simulate demand up {entity}% at warehouse W{entity}.", plan("what_if", "risk_view", "SELECT *, avg_daily_demand * (1 + $increase) AS scenario_daily_demand FROM risk_view WHERE warehouse_id = $warehouse", {"increase": entity/100, "warehouse": f"W{entity}"}, ["source_record_id", "avg_daily_demand"])),
                (f"Delete orders for supplier S{entity}.", plan("unsafe", abstain=True)),
                (f"Tell me the best choice for entity {entity}.", plan(needs_clarification=True, abstain=True)),
            ]
            for question, target in cases:
                validate_plan(target)
                if target["sql"]:
                    guard_sql(target["sql"], target["parameters"])
                yield split, {"source": "independent_synthetic_templates_v1", "entity_group": entity,
                              "messages": messages(question, retriever.retrieve(question)) + [{"role": "assistant", "content": json.dumps(target)}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/training"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = list(examples())
    random.Random(SEED).shuffle(rows)
    for split in ("train", "validation", "test"):
        selected = [row for kind, row in rows if kind == split]
        with (args.output / f"{split}.jsonl").open("w") as stream:
            for row in selected:
                stream.write(json.dumps(row)+"\n")
        print(split, len(selected))


if __name__ == "__main__":
    main()
