"""Immutable semantic holdout; never use runtime grammar to manufacture gold labels."""

import hashlib
import json
import re
from pathlib import Path

from sentinel.nlq.query_plan import validate_query_plan

HELDOUT = Path("data/sample/query_plan_heldout_v1.json")
HELDOUT_SHA256 = "a5c51259c8d0a34ae667a208ad67a003fb921fbfebfbedf9dc7bd63bfd1e47dc"


def normalized_question(question):
    return re.sub(r"[^\w]+", " ", question.casefold()).strip()


def load_holdout(path=HELDOUT):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != HELDOUT_SHA256:
        raise ValueError("Frozen holdout changed; create a new version, never relabel this set")
    data = json.loads(raw)
    seen = set()
    for case in data["cases"]:
        key = normalized_question(case["question"])
        if key in seen:
            raise ValueError("Duplicate held-out question")
        seen.add(key)
        target = validate_query_plan(case["expected"])
        if target != case["expected"]:
            raise ValueError("Gold labels must already be canonical")
        if case["expected_abstention"] != target["abstain"]:
            raise ValueError("Conflicting abstention label")
        if case["expected_evidence_requirements"] != target["evidence_requirements"]:
            raise ValueError("Conflicting evidence label")
    return data


def reject_holdout_leakage(questions):
    reserved = {normalized_question(c["question"]) for c in load_holdout()["cases"]}
    if reserved & {normalized_question(q) for q in questions}:
        raise ValueError("Frozen held-out question leaked into instructions or prompt examples")
