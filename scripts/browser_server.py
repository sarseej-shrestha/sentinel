"""Real local backend for browser tests, isolated from every user database."""

import tempfile
from pathlib import Path

import uvicorn

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database


def main():
    with tempfile.TemporaryDirectory(prefix="sentinel-browser-") as scratch:
        root = Path(scratch)
        db = build_database(root / "analytics.duckdb")
        app = create_app(db, root / "state.sqlite3", Path("frontend/dist"))
        uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False)


if __name__ == "__main__":
    main()
