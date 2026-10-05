"""Integrity only: benchmark examples are not semantic development regressions."""

import hashlib
from pathlib import Path


def test_postdevelopment_benchmark_remains_frozen():
    root = Path(__file__).resolve().parents[1] / "data/sample"
    expected = "8185221a88cb0fa8908ef7c62ce2964f20cf0041e7c5abcf2ce4b2593fcb3c38"
    assert (
        hashlib.sha256((root / "semantic_benchmark_v2.json").read_bytes()).hexdigest() == expected
    )
    assert (root / "semantic_benchmark_v2.sha256").read_text().split()[0] == expected
