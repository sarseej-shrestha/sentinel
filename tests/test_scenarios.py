import json
from pathlib import Path

import duckdb
import pytest

from sentinel.data.build_duckdb import build_database, synthetic_records
from sentinel.data.scenarios import DATA_SCENARIOS, scenario_records


@pytest.mark.parametrize("name", DATA_SCENARIOS)
def test_scenario_is_synthetic_and_reproducible(name, tmp_path):
    rows = scenario_records(name)
    assert rows == scenario_records(name)
    assert rows != synthetic_records()
    path = build_database(tmp_path / f"{name}.duckdb", records=rows)
    fixture = next(
        x for x in json.loads(Path("data/sample/scenarios.json").read_text()) if x["name"] == name
    )
    with duckdb.connect(str(path), read_only=True) as con:
        if name in DATA_SCENARIOS[:6]:
            table = (
                "inventory_daily" if name in ("unknown_product", "stale_inventory") else "shipments"
            )
            assert (
                con.execute(
                    f"SELECT count(*) FROM {table} WHERE contains(data_quality_flag, ?)",
                    [fixture["expected"]],
                ).fetchone()[0]
                > 0
            )
        if name == "missing_promised_date":
            assert (
                con.execute("SELECT is_late FROM shipment_view WHERE shipment_id='SH0'").fetchone()[
                    0
                ]
                is None
            )
        if name == "duplicate_shipment":
            assert (
                con.execute(
                    "SELECT count(is_late) FROM shipment_view WHERE shipment_id='SH0'"
                ).fetchone()[0]
                == 0
            )


def test_query_failure_fixtures():
    fixtures = json.loads(Path("data/sample/scenarios.json").read_text())
    assert len(fixtures) == 11
    assert {x["expected"] for x in fixtures if x["kind"] == "query"} == {
        "empty",
        "clarification",
        "blocked",
    }
