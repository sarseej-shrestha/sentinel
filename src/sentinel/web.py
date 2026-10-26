"""Launch the compiled local workspace with an isolated v1 demo and SQLite state."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/v1"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create synthetic data only if absent; never replace a database",
    )
    parser.add_argument(
        "--import-legacy",
        type=Path,
        help="Read and preserve an existing DuckDB audit snapshot without modifying it",
    )
    args = parser.parse_args()
    from sentinel.api import create_app
    from sentinel.data.build_duckdb import build_database

    database = args.data_dir / "analytics.duckdb"
    if args.init and not database.exists():
        build_database(database)
    static = Path(__file__).resolve().parents[2] / "frontend/dist"
    if not static.is_dir():
        parser.error(
            "Build the frontend first: npm --prefix frontend ci && npm --prefix frontend run build"
        )
    app = create_app(database, args.data_dir / "state.sqlite3", static)
    if args.import_legacy:
        from sentinel.audit.logger import AuditLog

        identity = app.state.service.store.import_legacy(AuditLog(args.import_legacy).replay())
        print("Preserved legacy audit snapshot:", identity)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
