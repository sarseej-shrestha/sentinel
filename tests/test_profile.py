from sentinel.data.download_kaggle import entries
from sentinel.data.profile import profile_csv


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


def test_profile_is_independent_of_chunk_dtype_inference(tmp_path):
    file = tmp_path / "mixed.csv"
    file.write_text("name,value\na,2\nb,\na,2\nc,3\n")
    assert profile_csv(file, chunksize=2)["duplicate_rows"] == 1
    assert profile_csv(file, chunksize=3)["duplicate_rows"] == 1


def test_literal_null_marker_is_not_missing(tmp_path):
    file = tmp_path / "null.csv"
    file.write_text("name,value\na,\na,<NULL>\n")
    assert profile_csv(file)["duplicate_rows"] == 0
