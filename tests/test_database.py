import duckdb
import pytest
from sentinel.data.build_duckdb import build_database, TABLES, VIEWS, synthetic_records
from sentinel.data.normalize import PROVENANCE


@pytest.fixture
def database(tmp_path):
    return build_database(tmp_path / "demo.duckdb")


def test_canonical_schema_and_provenance(database):
    with duckdb.connect(str(database), read_only=True) as con:
        tables = con.execute("SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'").fetchall()
        assert {x[0] for x in tables} == set(TABLES)
        for name in TABLES:
            assert set(PROVENANCE) <= {x[0] for x in con.execute(f"DESCRIBE {name}").fetchall()}
        for name in VIEWS:
            assert con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] > 0
        assert con.execute("SELECT DISTINCT source_dataset FROM shipments").fetchall() == [("sentinel_synthetic_v1",)]


def test_synthetic_reproducibility():
    assert synthetic_records() == synthetic_records()


def test_preserve_existing_database(database):
    with pytest.raises(FileExistsError):
        build_database(database)
