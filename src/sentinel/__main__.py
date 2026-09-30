"""Minimal executable console; no visual-design deliverables."""

import argparse
import json

from sentinel.config import DATABASE
from sentinel.console import Console


def main():
    parser = argparse.ArgumentParser(description="Sentinel synthetic supply-chain console")
    parser.add_argument("--database", default=str(DATABASE))
    commands = parser.add_subparsers(dest="command", required=True)
    question = commands.add_parser("ask")
    question.add_argument("question")
    question.add_argument(
        "--propose",
        action="store_true",
        help="Record the first recommendation as a pending simulated action",
    )
    question.add_argument("--backend", choices=["rules", "qwen"], default="rules")
    question.add_argument("--retrieval", choices=["lexical", "bge"], default="lexical")
    question.add_argument("--adapter", help="Local PEFT adapter directory, for the Qwen backend")
    decision = commands.add_parser("decide")
    decision.add_argument("action_id")
    decision.add_argument("decision", choices=["approve", "reject", "edit"])
    decision.add_argument("--reviewer", required=True)
    decision.add_argument("--text")
    commands.add_parser("replay")
    args = parser.parse_args()
    console = Console(args.database)
    if args.command == "ask":
        from sentinel.nlq.planner import QwenPlanner
        from sentinel.nlq.retrieval import SchemaRetriever

        try:
            if args.backend == "qwen":
                console.planner = QwenPlanner(adapter=args.adapter)
            console.retriever = SchemaRetriever(args.retrieval)
        except (ImportError, OSError) as exc:
            result = {
                "status": "unavailable",
                "abstained": True,
                "failure_behavior": f"Requested model unavailable: {type(exc).__name__}. Install model dependencies and download weights first.",
            }
            console.audit.append("model_unavailable", result)
            print(json.dumps(result, indent=2))
            return
        result = console.question(args.question)
        if args.propose and result.get("recommendations"):
            result["pending_action"] = console.gate.propose(result["recommendations"][0])
    elif args.command == "decide":
        result = console.gate.decide(args.action_id, args.decision, args.reviewer, args.text)
    else:
        result = console.audit.replay()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
