"""Download research material through KaggleHub; keep all raw files ignored."""

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sentinel.data.profile import profile_file

CORE = [
    ("ziya07/high-dimensional-supply-chain-inventory-dataset", "inventory; demand; suppliers"),
    (
        "shashwatwork/dataco-smart-supply-chain-for-big-data-analysis",
        "orders; late delivery; relational SQL",
    ),
    (
        "atomicd/retail-store-inventory-and-demand-forecasting/versions/8",
        "demand; promotions; what-if",
    ),
    ("olistbr/brazilian-ecommerce", "multi-table SQL; delivery lifecycle"),
    ("bytadit/ecommerce-order-dataset", "relational schema variation"),
    ("datasetengineer/logistics-and-supply-chain-dataset", "route risk; ETA"),
    ("ziya07/smart-logistics-supply-chain-dataset", "shipment events; delay risk"),
    ("razanihababdellatif/food-delivery-orders-and-eta-logistics-dataset", "ETA; late delivery"),
    ("untadta", "backorder labels; synthetic fallback"),
    ("philiphyde1/time-series-supply-chain-dataset", "daily forecasting"),
    ("shandeep777/retail-supply-chain-sales-dataset", "sales; suppliers"),
    ("fiq423ubf/apmarkfed-season-wise-procurement-details", "seasonal procurement"),
]
BENCHMARKS = [
    "favorita-grocery-sales-forecasting",
    "m5-forecasting-accuracy",
    "demand-forecasting-kernels-only",
    "rohlik-orders-forecasting-challenge",
    "rohlik-sales-forecasting-challenge-v2",
    "grupo-bimbo-inventory-demand",
    "supply-chain-cost-prediction-competition-met-iom",
    "open-shopee-code-league-logistic",
    "datathon-2022-upc-accenture",
]
FIELDS = [
    "handle",
    "url",
    "owner",
    "download_status",
    "access_requirement",
    "license",
    "file_names",
    "size_bytes",
    "row_count",
    "column_count",
    "date_range",
    "missing_values",
    "duplicate_rows",
    "intended_feature",
    "usage",
    "checked_at",
    "notes",
]


def entries():
    for handle, feature in CORE + [(x, "benchmark only") for x in BENCHMARKS]:
        competition = "/" not in handle
        row = dict.fromkeys(FIELDS, "unknown; not inspected")
        row.update(
            handle=handle,
            url=f"https://www.kaggle.com/{'competitions' if competition else 'datasets'}/{handle}",
            owner="unverified competition organizer" if competition else handle.split("/")[0],
            download_status="not_attempted",
            access_requirement="Kaggle login and competition rules acceptance"
            if competition
            else "public access or Kaggle authentication as required",
            intended_feature=feature,
            usage="benchmarking"
            if handle in BENCHMARKS
            else "evaluation research only; not demo or training",
            checked_at="",
            notes="No dataset identifiers are joined across sources.",
        )
        yield row


def save(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def download(row, raw):
    # Set before importing kagglehub. Never log credentials or API responses.
    os.environ.setdefault("KAGGLEHUB_CACHE", str((raw / "cache").resolve()))
    import kagglehub
    import requests

    handle = row["handle"]
    row["checked_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if "/" in handle:
            response = requests.get(
                "https://www.kaggle.com/api/v1/datasets/list",
                params={"search": handle.split("/")[1]},
                timeout=30,
            )
            if response.ok:
                for item in response.json():
                    if item.get("ref") == "/".join(handle.split("/")[:2]):
                        row["license"] = item.get("licenseName", "unverified")
            location = Path(kagglehub.dataset_download(handle))
        else:
            # KaggleHub applies competition access checks; never accept rules automatically.
            location = Path(kagglehub.competition_download(handle))
        profiles, errors = [], []
        files = sorted(p for p in location.rglob("*") if p.is_file())
        row["file_names"] = json.dumps([str(p.relative_to(location)) for p in files])
        row["size_bytes"] = sum(p.stat().st_size for p in files)
        for file in files:
            if file.suffix.lower() in {".csv", ".xlsx"}:
                try:
                    profiles.extend(profile_file(file))
                except Exception as exc:
                    errors.append(f"{file.name}: {type(exc).__name__}")
        for field in (
            "row_count",
            "column_count",
            "date_range",
            "missing_values",
            "duplicate_rows",
        ):
            row[field] = json.dumps({p["file"]: p[field] for p in profiles})
        row["download_status"] = (
            "downloaded_profiled" if profiles and not errors else "downloaded_partial_profile"
        )
        out = Path("data/profiles") / (handle.replace("/", "__") + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(profiles, indent=2))
        row["notes"] = (
            "; ".join(errors)
            or "CSV and XLSX profiles; other formats listed only. No raw samples committed."
        )
    except Exception as exc:
        # Store status, not untrusted exception bodies that may contain credentials.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        row["download_status"] = "unavailable"
        row["notes"] = (
            f"{type(exc).__name__}; HTTP {status or 'unknown'}. No data or license inferred; synthetic fallback."
        )
    print(handle, row["download_status"], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--include-benchmarks", action="store_true")
    parser.add_argument("--handle")
    args = parser.parse_args()
    manifest = Path("data/dataset_manifest.csv")
    rows = list(csv.DictReader(manifest.open())) if manifest.exists() else list(entries())
    if not args.init:
        for row in rows:
            if args.handle and row["handle"] != args.handle:
                continue
            if row["usage"] == "benchmarking" and not args.include_benchmarks:
                continue
            download(row, Path("data/raw"))
            save(rows, manifest)
    save(rows, manifest)


if __name__ == "__main__":
    main()
