"""Explicit demo defaults. No external action credentials are supported."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATABASE = Path(os.getenv("SENTINEL_DB", "data/processed/sentinel.duckdb"))
AS_OF = "2026-09-30"
SEED = 4200
ROW_LIMIT = 200
QUERY_TIMEOUT_SECONDS = 3.0
PLANNER_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
