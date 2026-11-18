"""Versioned, offline-trained models for two independent historical datasets."""

import hashlib
import json
import os
from pathlib import Path

import joblib
import numpy as np
import sklearn
from threadpoolctl import threadpool_limits

from sentinel.analytics.forecast import features, seasonal_naive

VERSION = "phase4-v1"
ROOT = Path("data/processed/phase4")
RISK_FIELDS = ["country", "product_group", "quantity"]


def predict_forecast(models, histories, horizon=14):
    working = [list(y) for y in histories]
    predictions = []
    with threadpool_limits(limits=1):
        for _ in range(horizon):
            x = [features(y, len(y)) for y in working]
            bounds = np.sort(np.maximum(0, [model.predict(x) for model in models]), axis=0).T
            predictions.append(bounds)
            for y, row in zip(working, bounds, strict=True):
                y.append(float(row[1]))
    return np.asarray(predictions).transpose(1, 0, 2)


def seasonal_intervals(histories, horizon=14):
    points = np.asarray([seasonal_naive(y, horizon) for y in histories])
    residuals = histories[:, 7:] - histories[:, :-7]
    low, high = np.quantile(residuals, [0.1, 0.9], axis=1)
    return np.stack(
        [
            np.maximum(0, points + np.minimum(0, low[:, None])),
            points,
            points + np.maximum(0, high[:, None]),
        ],
        axis=2,
    )


class Published:
    def __init__(self):
        path = Path(os.getenv("SENTINEL_PUBLISHED_DIR", str(ROOT))) / "bundle.joblib"
        self.bundle = None
        try:
            metadata = json.loads(path.with_name("manifest.json").read_text())
            if (
                metadata["version"] == VERSION
                and metadata["sklearn"] == sklearn.__version__
                and hashlib.sha256(path.read_bytes()).hexdigest() == metadata["bundle_sha256"]
            ):
                # Administrative, locally created artifacts only.
                self.bundle = joblib.load(path)
        except (OSError, KeyError, TypeError, ValueError, EOFError):
            # A missing or incompatible bundle must not prevent access to saved evidence.
            pass
        self.manifest = self.bundle["manifest"] if self.bundle else None

    def require(self):
        from fastapi import HTTPException

        if self.bundle is None:
            raise HTTPException(
                503, "Historical data is unavailable. Build the Phase 4 data bundle and restart."
            )
        return self.bundle

    def retail_series(self, store, product):
        bundle = self.require()
        if (store, product) not in bundle["entities"]:
            raise ValueError("Select an eligible store and product.")
        rows = bundle["retail"]
        cutoff = self.manifest["retail"]["training_cutoff"]
        selected = rows[
            (rows.store == store) & (rows["product"] == product) & (rows.date <= cutoff)
        ]
        return selected.sort_values("date")

    def forecast(self, store, product):
        from datetime import date, timedelta

        rows = self.retail_series(store, product)
        meta = self.manifest["retail"]
        history = np.array([rows.units_sold.to_numpy(dtype=float)])
        baseline = seasonal_intervals(history)[0]
        method = meta["model"]
        bounds = (
            baseline
            if method == "seasonal_naive"
            else predict_forecast(self.bundle["forecast"], history)[0]
        )
        origin = date.fromisoformat(meta["training_cutoff"])
        return {
            "method": method,
            "scope": f"{store} / {product}",
            "origin": str(origin),
            "units": "units sold per day",
            "history_days": len(rows),
            "excluded_series": meta["excluded_series"],
            "model_version": VERSION,
            "points": [
                {
                    "date": str(origin + timedelta(days=day)),
                    "lower": float(low),
                    "point": float(point),
                    "upper": float(high),
                    "baseline": float(base[1]),
                }
                for day, ((low, point, high), base) in enumerate(
                    zip(bounds, baseline, strict=True), 1
                )
            ],
            "source_rows": [int(rows.source_row.iloc[0]), int(rows.source_row.iloc[-1])],
            "evaluation": meta["final"],
        }

    def coverage(self, store, product, increase):
        rows = self.retail_series(store, product)
        last = rows.iloc[-1]
        history = rows.iloc[-29:-1]
        daily = float(history.units_sold.mean())
        inventory = int(last.opening_inventory)
        return {
            "scope": f"{store} / {product}",
            "origin": str(last.date),
            "opening_inventory": inventory,
            "observed_daily_sales": daily,
            "history_days": len(history),
            "increase_percent": increase,
            "days_of_cover": inventory / daily if daily else None,
            "scenario_days_of_cover": inventory / (daily * (1 + increase / 100)) if daily else None,
            "source_rows": [int(x) for x in [*history.source_row, last.source_row]],
        }

    def lateness(self, vendor):
        rows = self.require()["shipment"]
        scoped = rows[rows.vendor == vendor] if vendor else rows
        if scoped.empty:
            raise ValueError("Select a vendor present in the shipment source.")
        late = scoped[scoped.late == 1]
        return {
            "scope": vendor or "All vendors",
            "origin": self.manifest["shipment"]["reference_date"],
            "assessed": len(scoped),
            "late": len(late),
            "late_rate": len(late) / len(scoped),
            "source_wide_rejections": self.manifest["shipment"]["rejected"],
            "examples": [
                {
                    "source_row": int(row.source_row),
                    "record_id": int(row.record_id),
                    "scheduled": str(row.scheduled.date()),
                    "delivered": str(row.delivered.date()),
                }
                for row in late.head(5).itertuples()
            ],
        }

    def shipment_score(self, record_id):
        import pandas as pd

        bundle = self.require()
        meta = self.manifest["shipment"]
        if record_id not in meta["test_example_ids"]:
            raise ValueError("Select a held-out example from the catalog.")
        row = bundle["shipment"].loc[bundle["shipment"].record_id == record_id].iloc[0]
        x = pd.DataFrame(
            [
                {
                    "country": row.country,
                    "product_group": row.product_group,
                    "quantity": row.quantity,
                }
            ],
            columns=RISK_FIELDS,
        )
        score = float(bundle["risk"].predict_proba(x)[0, 1])
        return {
            "model": meta["model"],
            "model_version": VERSION,
            "status": "research_only",
            "record_id": record_id,
            "source_row": int(row.source_row),
            "prediction_time": str(row.po_date.date()),
            "inputs": x.to_dict("records")[0],
            "score": score,
            "observed_late": bool(row.late),
            "evaluation": meta["test"],
        }
