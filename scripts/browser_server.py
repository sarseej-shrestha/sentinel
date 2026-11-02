"""Real local backend for browser tests, isolated from every user database."""

import tempfile
from datetime import date
from pathlib import Path

import uvicorn
from starlette.routing import Mount

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database, synthetic_records


def main():
    with tempfile.TemporaryDirectory(prefix="sentinel-browser-") as scratch:
        root = Path(scratch)
        db = build_database(root / "analytics.duckdb")
        app = create_app(db, root / "state.sqlite3", Path("frontend/dist"))
        # Fixed disposable failure fixtures; these routes exist only in this test server.
        for name in (
            "missing-demand",
            "unevaluable-supplier",
            "unevaluable-all",
            "stale-forecast",
        ):
            rows = synthetic_records()
            if name == "missing-demand":
                rows["demand_daily"] = [r for r in rows["demand_daily"] if r[2:4] != ("P1", "W3")]
            elif name == "stale-forecast":
                rows["demand_daily"] = [
                    r
                    for r in rows["demand_daily"]
                    if r[2:4] != ("P1", "W3") or r[1] <= date(2026, 9, 23)
                ]
            else:
                rows["shipments"] = [
                    r[:6] + (None,) + r[7:] if name == "unevaluable-all" or r[2] == "S1" else r
                    for r in rows["shipments"]
                ]
            case_db = build_database(root / (name + ".duckdb"), records=rows)
            app.router.routes.insert(
                0, Mount("/cases/" + name, create_app(case_db, root / (name + ".sqlite3")))
            )
        uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False)


if __name__ == "__main__":
    main()
