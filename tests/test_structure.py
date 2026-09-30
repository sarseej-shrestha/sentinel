from pathlib import Path

from sentinel.config import AS_OF, PLANNER_MODEL


def test_project_defaults():
    assert PLANNER_MODEL == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    assert AS_OF == "2026-09-30"
    assert "data/raw/" in Path(".gitignore").read_text()
