"""Import pinned Kaggle archives and evaluate and persist historical models."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from sentinel.analytics.forecast import features
from sentinel.config import SEED
from sentinel.published import (
    RISK_FIELDS,
    ROOT,
    VERSION,
    predict_forecast,
    seasonal_intervals,
)

SOURCES = {
    "retail": (
        "anirudhchauhan/retail-store-inventory-forecasting-dataset",
        "retail_store_inventory.csv",
        "c7b6019887d85e6bb311a76da72560463e6bf269bfadbe4b326fbff3b1000468",
        "CC0: Public Domain",
        "utf-8",
    ),
    "shipment": (
        "apoorvwatsky/supply-chain-shipment-pricing-data",
        "SCMS_Delivery_History_Dataset_20150929.csv",
        "9cffdb4ab8c6135b0f999c91b3a5c6e88a3c3dfc28c7c85feb9e97f986e3578a",
        "Data files © Original Authors; redistribution permission unresolved",
        "cp1252",
    ),
}
RAW = Path("data/raw/phase4")
HORIZON = 14


def read_source(kind, mapping):
    slug, name, digest, license_name, encoding = SOURCES[kind]
    with ZipFile(RAW / (kind + ".zip")) as archive:
        raw = archive.read(name)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"{name} differs from the pinned Kaggle version")
    headers = next(csv.reader(io.StringIO(raw.decode(encoding))))
    frame = pd.read_csv(io.BytesIO(raw), encoding=encoding, usecols=list(mapping))
    frame = frame.rename(columns=mapping)
    frame.insert(0, "source_row", np.arange(2, len(frame) + 2))
    return frame, {
        "source": f"https://www.kaggle.com/datasets/{slug}",
        "identifier": slug,
        "kaggle_version": 1,
        "archive_local_mtime_utc": datetime.fromtimestamp(
            (RAW / (kind + ".zip")).stat().st_mtime, UTC
        ).isoformat(),
        "filename": name,
        "sha256": digest,
        "license": license_name,
        "raw_rows": len(frame),
        "source_schema": headers,
        "mapping": mapping,
        "source_row": "CSV record ordinal including header; first data record is 2",
    }


def fit_forecast(y):
    x = np.concatenate([[features(row[:t], t) for t in range(14, len(row))] for row in y])
    with threadpool_limits(limits=1):
        return [
            HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                max_iter=60,
                max_leaf_nodes=7,
                min_samples_leaf=5,
                l2_regularization=1,
                early_stopping=False,
                random_state=SEED,
            ).fit(x, y[:, 14:].ravel())
            for q in (0.1, 0.5, 0.9)
        ]


def errors(truth, bounds):
    observed = np.isfinite(truth)
    if not observed.any():
        raise ValueError("No observed final outcomes to evaluate.")
    covered = (truth >= bounds[:, :, 0]) & (truth <= bounds[:, :, 2])
    difference = (truth - bounds[:, :, 1])[observed]
    return {
        "n": int(observed.sum()),
        "missing_outcomes": int((~observed).sum()),
        "mae": float(np.abs(difference).mean()),
        "rmse": float(np.sqrt(np.mean(difference**2))),
        "interval_coverage": float(covered[observed].mean()),
    }


def train_retail():
    rows, manifest = read_source(
        "retail",
        {
            "Date": "date",
            "Store ID": "store",
            "Product ID": "product",
            "Inventory Level": "opening_inventory",
            "Units Sold": "units_sold",
            "Units Ordered": "units_ordered",
        },
    )
    keys = ["store", "product", "date"]
    rows["date"] = pd.to_datetime(rows.date, format="%Y-%m-%d", errors="coerce")
    counts = ["opening_inventory", "units_sold", "units_ordered"]
    for field in counts:
        rows[field] = pd.to_numeric(rows[field], errors="coerce")
    invalid = rows[keys + counts].isna().any(axis=1) | (
        ~np.isfinite(rows[counts]) | (rows[counts] < 0) | (rows[counts] % 1 != 0)
    ).any(axis=1)
    duplicate = rows.duplicated(keys, keep=False) & ~invalid
    clean = rows[~(invalid | duplicate)].copy()
    dates = pd.date_range(clean.date.min(), clean.date.max())
    series = clean.pivot(index=["store", "product"], columns="date", values="units_sold")
    series = series.reindex(columns=dates)
    origin = len(dates) - HORIZON
    excluded = series.iloc[:, :origin].isna().any(axis=1)
    complete = series[~excluded]
    y = complete.to_numpy(dtype=float)
    validation = []
    for end in (origin - 84, origin - 56, origin - 28):
        fold_eligible = ~series.iloc[:, :end].isna().any(axis=1)
        fold = series[fold_eligible].to_numpy(dtype=float)
        training, truth = fold[:, :end], fold[:, end : end + HORIZON]
        validation.append(
            {
                "origin": str(dates[end - 1].date()),
                "gradient_boosting": errors(
                    truth, predict_forecast(fit_forecast(training), training)
                ),
                "seasonal_naive": errors(truth, seasonal_intervals(training)),
            }
        )
    promote = all(
        item["gradient_boosting"]["mae"] < item["seasonal_naive"]["mae"]
        and item["gradient_boosting"]["rmse"] < item["seasonal_naive"]["rmse"]
        for item in validation
    )
    training, truth = y[:, :origin], y[:, origin:]
    models = fit_forecast(training)
    forecast = predict_forecast(models, training)
    baseline = seasonal_intervals(training)
    with_outcomes = np.isfinite(truth).any(axis=1)
    series_errors = np.nanmean(
        np.abs(truth[with_outcomes] - (forecast if promote else baseline)[with_outcomes, :, 1]),
        axis=1,
    )
    weakest_series = complete.index[with_outcomes][int(np.argmax(series_errors))]
    manifest.update(
        {
            "provenance": "Publisher-generated synthetic retail observations",
            "reference_date": str(dates[origin - 1].date()),
            "date_range": [str(dates[0].date()), str(dates[-1].date())],
            "accepted": len(clean),
            "rejected": int(invalid.sum() + duplicate.sum()),
            "reasons": {
                "invalid": int(invalid.sum()),
                "duplicate_entity_date": int(duplicate.sum()),
            },
            "excluded_series": int(excluded.sum()),
            "eligible_series": len(y),
            "validation": validation,
            "model": "quantile_hist_gradient_boosting" if promote else "seasonal_naive",
            "training_cutoff": str(dates[origin - 1].date()),
            "final_dates": [str(dates[origin].date()), str(dates[-1].date())],
            "final": {
                "gradient_boosting": errors(truth, forecast),
                "seasonal_naive": errors(truth, baseline),
            },
            "weakest_series": "/".join(weakest_series),
            "by_horizon": [
                {
                    "day": i + 1,
                    "gradient_boosting": errors(truth[:, i : i + 1], forecast[:, i : i + 1]),
                    "seasonal_naive": errors(truth[:, i : i + 1], baseline[:, i : i + 1]),
                }
                for i in range(HORIZON)
            ],
        }
    )
    clean["date"] = clean.date.dt.strftime("%Y-%m-%d")
    manifest["feature_schema"] = ["lag_1", "lag_7", "lag_14", "mean_7", "mean_14", "weekly_phase"]
    dispositions = {
        "invalid": rows.loc[invalid, "source_row"].tolist(),
        "duplicate_entity_date": rows.loc[duplicate, "source_row"].tolist(),
        "excluded_series": clean.loc[
            ~pd.MultiIndex.from_frame(clean[["store", "product"]]).isin(complete.index),
            "source_row",
        ].tolist(),
    }
    manifest["excluded_rows"] = len(dispositions["excluded_series"])
    return manifest, clean, list(complete.index), models, dispositions


def ship_features(frame):
    return frame[RISK_FIELDS].copy()


def temporal_partitions(rows, calibration_start, test_start):
    # Group before filtering prediction eligibility so an excluded sibling cannot bridge splits.
    stage = np.select(
        [rows.po_date < calibration_start, rows.po_date < test_start], [0, 1], default=2
    )
    labeled = rows.assign(stage=stage)
    crossing = labeled.groupby("order").stage.transform("nunique") > 1
    # Consider the label known only once delivered. Purge outcomes unavailable at the next stage.
    late_label = ((stage == 0) & (rows.delivered >= calibration_start)) | (
        (stage == 1) & (rows.delivered >= test_start)
    )
    return labeled, crossing, late_label


def risk_metrics(y, score):
    return {
        "n": len(y),
        "positive_rate": float(y.mean()),
        "mean_probability": float(np.mean(score)),
        "roc_auc": roc_auc_score(y, score),
        "average_precision": average_precision_score(y, score),
        "brier": brier_score_loss(y, score),
        "log_loss": log_loss(y, score),
    }


def train_shipment():
    rows, manifest = read_source(
        "shipment",
        {
            "ID": "record_id",
            "PO / SO #": "order",
            "Vendor": "vendor",
            "Country": "country",
            "Product Group": "product_group",
            "Line Item Quantity": "quantity",
            "Scheduled Delivery Date": "scheduled",
            "Delivered to Client Date": "delivered",
            "PO Sent to Vendor Date": "po_date",
            "Delivery Recorded Date": "recorded",
        },
    )
    for field in ("scheduled", "delivered", "po_date", "recorded"):
        rows[field] = pd.to_datetime(rows[field], format="mixed", errors="coerce").astype(
            "datetime64[ns]"
        )
    rows["quantity"] = pd.to_numeric(rows.quantity, errors="coerce")
    invalid = rows[["scheduled", "delivered", "record_id"]].isna().any(axis=1)
    duplicate = rows.record_id.duplicated(keep=False) & ~invalid
    clean = rows[~(invalid | duplicate)].copy()
    clean["late"] = (clean.delivered > clean.scheduled).astype(int)
    missing_po = clean.po_date.isna() | clean.order.isna()
    bad_features = (
        clean[["country", "product_group", "quantity"]].isna().any(axis=1)
        | ~np.isfinite(clean.quantity)
        | (clean.quantity < 1)
        | (clean.scheduled <= clean.po_date)
        | (clean.delivered < clean.po_date)
    )
    missing_recorded = clean.recorded.isna() | (clean.recorded < clean.delivered)
    cal_start, test_start = pd.Timestamp("2012-01-01"), pd.Timestamp("2014-01-01")
    grouped = rows.dropna(subset=["order", "po_date"]).groupby("order").po_date
    crosses = ((grouped.min() < cal_start) & (grouped.max() >= cal_start)) | (
        (grouped.min() < test_start) & (grouped.max() >= test_start)
    )
    crossing_orders = clean.order.isin(crosses[crosses].index)
    eligible = clean[~(missing_po | bad_features | missing_recorded | crossing_orders)]
    labeled, _, late_label = temporal_partitions(eligible, cal_start, test_start)
    late_label = (
        late_label
        | ((labeled.stage == 0) & (labeled.recorded >= cal_start))
        | ((labeled.stage == 1) & (labeled.recorded >= test_start))
    )
    train, cal, test = [labeled[(labeled.stage == stage) & ~late_label] for stage in range(3)]
    prep = ColumnTransformer(
        [
            ("categorical", OneHotEncoder(handle_unknown="ignore"), RISK_FIELDS[:2]),
            ("numeric", StandardScaler(), RISK_FIELDS[2:]),
        ]
    )
    base = make_pipeline(prep, LogisticRegression(random_state=SEED, max_iter=500))
    base.fit(ship_features(train), train.late)
    model = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
    model.fit(ship_features(cal), cal.late)
    score = model.predict_proba(ship_features(test))[:, 1]
    fit_rate = float(train.late.mean())
    anchor = clean.delivered.max()
    manifest.update(
        {
            "provenance": "Kaggle uploader attributes SCMS source to USAID dataset 0162a542-4f2e-4fe2-ad5d-8f6ed2344056",
            "date_range": [str(clean.delivered.min().date()), str(anchor.date())],
            "reference_date": str(anchor.date()),
            "accepted": len(clean),
            "rejected": int(invalid.sum() + duplicate.sum()),
            "reasons": {
                "invalid_delivery_dates_or_id": int(invalid.sum()),
                "duplicate_source_id": int(duplicate.sum()),
            },
            "prediction_exclusions": {
                "missing_po_or_order": int(missing_po.sum()),
                "bad_features_after_po_check": int((~missing_po & bad_features).sum()),
                "missing_or_inconsistent_recorded_after_feature_check": int(
                    (~(missing_po | bad_features) & missing_recorded).sum()
                ),
                "linked_orders_crossing_partitions": int(
                    (~(missing_po | bad_features | missing_recorded) & crossing_orders).sum()
                ),
                "outcome_not_available_at_boundary": int(late_label.sum()),
            },
            "calibration_start": str(cal_start.date()),
            "test_start": str(test_start.date()),
            "partitions": [
                {"n": len(s), "positive_rate": float(s.late.mean()), "orders": s.order.nunique()}
                for s in (train, cal, test)
            ],
            "test": {
                "calibrated": risk_metrics(test.late, score),
                "uncalibrated": risk_metrics(
                    test.late, base.predict_proba(ship_features(test))[:, 1]
                ),
                "prevalence": risk_metrics(test.late, np.full(len(test), fit_rate)),
            },
            "model": "sigmoid_calibrated_logistic_regression",
            "test_example_ids": [int(x) for x in test.record_id.head(30)],
        }
    )
    manifest["feature_schema"] = RISK_FIELDS
    manifest["prediction_excluded_rows"] = sum(manifest["prediction_exclusions"].values())
    dispositions = {
        "invalid_delivery_dates_or_id": rows.loc[invalid, "source_row"].tolist(),
        "duplicate_source_id": rows.loc[duplicate, "source_row"].tolist(),
        "missing_po_or_order": clean.loc[missing_po, "source_row"].tolist(),
        "bad_features_after_po_check": clean.loc[~missing_po & bad_features, "source_row"].tolist(),
        "missing_or_inconsistent_recorded_after_feature_check": clean.loc[
            ~(missing_po | bad_features) & missing_recorded, "source_row"
        ].tolist(),
        "linked_orders_crossing_partitions": clean.loc[
            ~(missing_po | bad_features | missing_recorded) & crossing_orders, "source_row"
        ].tolist(),
        "outcome_not_available_at_boundary": labeled.loc[late_label, "source_row"].tolist(),
    }
    return manifest, clean, model, dispositions


def main():
    retail, sales, entities, forecast, retail_dispositions = train_retail()
    shipment, shipments, risk, shipment_dispositions = train_shipment()
    manifest = {
        "version": VERSION,
        "sklearn": sklearn.__version__,
        "seed": SEED,
        "retail": retail,
        "shipment": shipment,
    }
    bundle = {
        "manifest": manifest,
        "retail": sales,
        "entities": entities,
        "forecast": forecast,
        "shipment": shipments,
        "risk": risk,
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    artifact = ROOT / "bundle.joblib"
    temp = artifact.with_suffix(".tmp")
    joblib.dump(bundle, temp, compress=3)
    temp.replace(artifact)
    manifest["bundle_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path = ROOT / "manifest.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, indent=2) + "\n")
    temp.replace(path)
    (ROOT / "excluded-source-rows.json").write_text(
        json.dumps({"retail": retail_dispositions, "shipment": shipment_dispositions}, indent=2)
        + "\n"
    )
    for name, fields in [
        ("retail", ["final", "eligible_series", "model"]),
        ("shipment", ["test", "partitions"]),
    ]:
        print(name, {k: manifest[name][k] for k in fields}, flush=True)


if __name__ == "__main__":
    main()
