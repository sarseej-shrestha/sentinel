"""Small calibrated classifiers plus transparent review rules on synthetic data."""
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sentinel.config import SEED

FEATURES = {"stockout": ["days_of_cover", "inventory_age_days"],
            "late_delivery": ["distance_km", "traffic_index"],
            "supplier_reliability": ["late_delivery_rate", "evaluable_shipments"]}
RISK_SCHEMA = {"type": "object", "required": ["risk_type", "risk_level", "drivers", "evidence_rows", "missing_information", "recommended_review"],
               "properties": {"risk_type": {"enum": list(FEATURES)}, "risk_level": {"enum": ["low", "medium", "high", "unknown"]},
               "drivers": {"type": "array", "items": {"type": "string"}}, "evidence_rows": {"type": "array", "minItems": 1},
               "missing_information": {"type": "array"}, "recommended_review": {"type": "boolean"}}}


def synthetic_training(kind, seed=SEED, size=1200):
    rng = np.random.default_rng(seed)
    if kind == "stockout":
        cover = rng.uniform(0, 40, size)
        age = rng.integers(0, 15, size)
        x = np.column_stack([cover, age])
        # Simulated 14-day realized consumption against the stock-cover estimate.
        y = (cover < rng.normal(14, 4, size) + age*0.3).astype(int)
    elif kind == "late_delivery":
        distance = rng.uniform(50, 1000, size)
        traffic = rng.uniform(0, 1, size)
        x = np.column_stack([distance, traffic])
        logit = -3 + distance/650 + traffic*3
        y = rng.binomial(1, 1/(1+np.exp(-logit)))
    elif kind == "supplier_reliability":
        rate = rng.uniform(0, 1, size)
        n = rng.integers(5, 200, size)
        x = np.column_stack([rate, n])
        y = (rng.binomial(20, rate)/20 > 0.3).astype(int)
    else:
        raise ValueError("Unsupported risk type")
    return x, y


class RiskEngine:
    def __init__(self):
        self.models, self.metrics = {}, {}

    def fit(self):
        for kind in FEATURES:
            x, y = synthetic_training(kind)
            base = make_pipeline(StandardScaler(), LogisticRegression(random_state=SEED))
            base.fit(x[:720], y[:720])
            calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
            calibrated.fit(x[720:960], y[720:960])
            prediction = calibrated.predict_proba(x[960:])[:, 1]
            self.models[kind] = calibrated
            self.metrics[kind] = {"test_brier_score": float(brier_score_loss(y[960:], prediction)), "train_rows": 720,
                                  "calibration_rows": 240, "test_rows": 240, "data": "independent synthetic simulation only",
                                  "limitation": "Calibration assessed on synthetic generator; not evidence of real operational accuracy."}
        return self

    def assess(self, kind, row):
        if kind not in FEATURES:
            raise ValueError("Unsupported risk type")
        missing = [name for name in FEATURES[kind] if row.get(name) is None or not np.isfinite(row[name])]
        drivers = []
        invalid = row.get("data_quality_flag", "ok") != "ok"
        if invalid:
            missing.append("valid evidence: " + row["data_quality_flag"])
        if kind == "stockout" and (row.get("inventory_age_days") or 0) > 3:
            missing.append("fresh inventory (at most 3 days old)")
        if kind == "supplier_reliability" and (row.get("evaluable_shipments") or 0) < 10:
            missing.append("at least 10 evaluable shipments")
        if missing:
            level, source = "unknown", "data_quality_rule_v1"
        else:
            if kind not in self.models:
                raise RuntimeError("Fit risk models before assessment")
            probability = self.models[kind].predict_proba([[row[name] for name in FEATURES[kind]]])[0, 1]
            level = "high" if probability >= 0.65 else "medium" if probability >= 0.3 else "low"
            source = "synthetic_logistic_sigmoid_v1 + deterministic_review_rules_v1"
            drivers = [f"{name}={row[name]}" for name in FEATURES[kind]]
            if kind == "stockout" and row["days_of_cover"] < 14:
                level = "high"
                drivers.append("Rule: fewer than 14 days of inventory coverage")
            if kind == "supplier_reliability" and row["late_delivery_rate"] > 0.3:
                level = "high"
                drivers.append("Rule: observed late-delivery rate above 0.3 with at least 10 deliveries")
            if kind == "late_delivery" and row["traffic_index"] > 0.8:
                level = "high"
                drivers.append("Rule: traffic index above 0.8")
            if kind == "supplier_reliability" and row.get("recent_delay_events", 0) >= 3:
                level = "high"
                drivers.append("Rule: at least three supplier delay events in the past seven days")
        return {"risk_type": kind, "risk_level": level, "drivers": drivers, "evidence_rows": [dict(row)],
                "missing_information": missing, "recommended_review": level != "low", "source": source,
                "assumptions": ["Synthetic simulation; model is not validated for real operations."]}
