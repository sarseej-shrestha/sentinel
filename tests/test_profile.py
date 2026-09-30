from sentinel.data.profile import profile_csv
from sentinel.data.download_kaggle import entries


def test_profile_counts_and_cross_chunk_duplicates(tmp_path):
    file = tmp_path / "small.csv"
    file.write_text("date,value\n2026-01-01,2\n2026-01-02,\n2026-01-01,2\n")
    profile = profile_csv(file, chunksize=1)
    assert profile["row_count"] == 3
    assert profile["column_count"] == 2
    assert profile["missing_values"] == 1
    assert profile["duplicate_rows"] == 1
    assert "2026-01-01" in profile["date_range"]["date"][0]


def test_manifest_all_requested_datasets():
    rows = list(entries())
    assert len(rows) == 21
    assert sum(row["usage"] == "benchmarking" for row in rows) == 9
    assert any("versions/8" in row["url"] for row in rows)
