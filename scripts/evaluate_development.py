"""Development-only diagnostics. Does not load the frozen held-out examples."""

import argparse
import json
from pathlib import Path

from scripts.build_sft_dataset import examples
from scripts.instruction_curation import seed_rows
from scripts.plan_diagnostics import aggregate, diagnose
from sentinel.nlq.query_plan import request_plan


def cases():
    for split, row in examples():
        yield {
            "id": row["instruction_group"],
            "split": split,
            "question": row["question"],
            "expected": json.loads(row["messages"][-1]["content"]),
        }
    for row in seed_rows():
        yield {
            "id": row["id"],
            "split": row["split"],
            "question": row["question"],
            "expected": row["target"],
            **({"previous_output": row["previous_output"]} if "previous_output" in row else {}),
        }


def run(path, baseline=None, model_report=None):
    # Reuse the baseline's frozen development labels after changing the parser.
    source = json.loads(Path(baseline).read_text())["cases"] if baseline else list(cases())
    records = [
        {
            **c,
            "prediction": request_plan(c["question"]),
            "diagnostics": diagnose(request_plan(c["question"]), c["expected"]),
        }
        for c in source
    ]
    report = {
        "scope": "Existing training/validation/development instructions, not heldout; field credit is diagnostic only.",
        "cases": records,
        "summary": aggregate([r["diagnostics"] for r in records]),
        "supported": aggregate([r["diagnostics"] for r in records if not r["expected"]["abstain"]]),
        "recovery_input_taxonomy": aggregate(
            [
                diagnose(c["previous_output"], c["expected"])
                for c in source
                if "previous_output" in c
            ]
        ),
    }
    if model_report:
        development = json.loads(Path("data/sample/query_plan_gold.json").read_text())
        labels = {c["question"]: c["expected"] for c in [*source, *development]}
        measurements = json.loads(Path(model_report).read_text())["records"]
        if any(r["input"] not in labels for r in measurements):
            raise ValueError("Model diagnostics accept only known development questions")
        report["development_model_diagnostics"] = aggregate(
            [diagnose(r["model_output"], labels[r["input"]]) for r in measurements]
        )
    path = Path(path)
    if path.exists():
        raise ValueError("Choose a new report path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--model-report", type=Path)
    args = parser.parse_args()
    run(args.output, args.baseline, args.model_report)
