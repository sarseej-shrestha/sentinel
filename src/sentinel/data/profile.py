"""Profile CSVs without printing or retaining customer records."""

import argparse
import json
import warnings
from pathlib import Path

import pandas as pd


def profile_csv(path: Path, chunksize=50_000):
    return profile_chunks(
        path,
        pd.read_csv(
            path,
            chunksize=chunksize,
            encoding="utf-8-sig",
            encoding_errors="replace",
            low_memory=False,
            dtype="string",
        ),
    )


def profile_file(path):
    if path.suffix.lower() == ".csv":
        return [profile_csv(path)]
    if path.suffix.lower() == ".xlsx":
        return [
            dict(profile_chunks(path, [frame]), file=f"{path.name}:{sheet}")
            for sheet, frame in pd.read_excel(path, sheet_name=None).items()
        ]
    return []


def profile_chunks(path, chunks):
    rows = missing = duplicates = 0
    hashes = set()
    columns = []
    dates = {}
    for chunk in chunks:
        columns = list(chunk.columns)
        rows += len(chunk)
        missing += int(chunk.isna().sum().sum())
        # Read CSVs as strings so chunk-local numeric inference cannot change hashes.
        # Preserve NA rather than conflating it with a literal sentinel string.
        for value in pd.util.hash_pandas_object(chunk.astype("string"), index=False):
            duplicates += int(value in hashes)
            hashes.add(value)
        for col in columns:
            if any(word in col.lower() for word in ("date", "timestamp", "time_stamp")):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(
                        chunk[col].astype("string"), errors="coerce", utc=True, format="mixed"
                    )
                if parsed.notna().any():
                    low, high = str(parsed.min()), str(parsed.max())
                    old = dates.get(col, [low, high])
                    dates[col] = [min(low, old[0]), max(high, old[1])]
    return {
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "row_count": rows,
        "column_count": len(columns),
        "columns": columns,
        "date_range": dates,
        "missing_values": missing,
        "duplicate_rows": duplicates,
        "duplicate_method": "64-bit normalized row hash; negligible collision risk",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    print(json.dumps(profile_csv(args.file), indent=2))
