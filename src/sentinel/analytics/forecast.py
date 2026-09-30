"""Leakage-free recursive quantile forecasts and chronological evaluation."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from sentinel.config import SEED


def validate_history(history):
    y = np.asarray(history, dtype=float)
    if y.ndim != 1 or not len(y) or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("Supply a nonempty, finite, nonnegative daily demand series with no missing days.")
    return y


def seasonal_naive(history, horizon=14, season=7):
    y = validate_history(history)
    period = min(len(y), season)
    return [float(y[-period + i % period]) for i in range(horizon)]


def features(history, t):
    return [history[-1], history[-7], history[-14], float(np.mean(history[-7:])), float(np.mean(history[-14:])), t % 7]


def forecast(history, horizon=14):
    y = validate_history(history)
    if not isinstance(horizon, int) or not 1 <= horizon <= 90:
        raise ValueError("Forecast horizon must be 1 to 90 days")
    baseline = seasonal_naive(y, horizon)
    warnings = ["Intervals are conditional quantile estimates; empirical coverage must be checked. No confidence percentage is claimed."]
    if len(y) < 56:
        warnings.append("Sparse history: fewer than 56 daily observations; use seasonal-naive only.")
        residual = y[7:] - y[:-7]
        if len(residual) >= 14:
            low, high = np.quantile(residual, [0.1, 0.9])
            lower = [float(max(0, min(v, v+low))) for v in baseline]
            upper = [float(max(v, v+high)) for v in baseline]
        else:
            lower = upper = [None]*horizon
            warnings.append("Insufficient residual history to estimate an interval.")
        return {"method": "seasonal_naive", "point": baseline, "lower": lower, "upper": upper, "baseline": baseline, "warnings": warnings, "quantiles": [0.1, 0.5, 0.9], "history_days": len(y)}
    if np.mean(y[-7:]) > 1.75*max(np.mean(y[-28:-7]), 1) or y[-1] > 2*max(np.mean(y[-8:-1]), 1):
        warnings.append("Recent demand spike: historical patterns may understate future demand.")
    x = np.asarray([features(y[:t], t) for t in range(14, len(y))])
    with threadpool_limits(limits=1):
        models = [HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=60, max_leaf_nodes=7, min_samples_leaf=5, l2_regularization=1, early_stopping=False, random_state=SEED).fit(x, y[14:]) for q in (0.1, 0.5, 0.9)]
        working = list(y)
        intervals = []
        for _ in range(horizon):
            f = [features(working, len(working))]
            low, median, high = sorted(max(0, float(model.predict(f)[0])) for model in models)
            intervals.append((low, median, high))
            working.append(median)
    return {"method": "quantile_hist_gradient_boosting", "lower": [x[0] for x in intervals], "point": [x[1] for x in intervals],
            "upper": [x[2] for x in intervals], "baseline": baseline, "warnings": warnings, "quantiles": [0.1, 0.5, 0.9], "history_days": len(y)}


def walk_forward(history, horizon=14, min_train=84, step=14):
    y = validate_history(history)
    folds = []
    for origin in range(min_train, len(y)-horizon+1, step):
        output = forecast(y[:origin], horizon)
        truth = y[origin:origin+horizon]
        intervals_available = all(v is not None for v in output["lower"])
        folds.append({"train_end_index": origin-1, "test_start_index": origin, "test_end_index": origin+horizon-1,
                      "mae": float(np.mean(np.abs(truth-output["point"]))), "baseline_mae": float(np.mean(np.abs(truth-output["baseline"]))),
                      "interval_coverage": float(np.mean((truth >= output["lower"]) & (truth <= output["upper"]))) if intervals_available else None})
    return {"folds": folds, "mean_mae": float(np.mean([f["mae"] for f in folds])) if folds else None,
            "mean_baseline_mae": float(np.mean([f["baseline_mae"] for f in folds])) if folds else None,
            "warning": None if folds else "Insufficient history for walk-forward evaluation."}


def forecast_from_result(result, horizon=14):
    from datetime import date
    from sentinel.analytics.evidence import verified
    result = verified(result)
    rows = sorted(result.rows, key=lambda row: row["demand_date"])
    if len({(r["product_id"], r["warehouse_id"]) for r in rows}) != 1:
        raise ValueError("Forecast one product at one warehouse at a time")
    dates = [date.fromisoformat(str(r["demand_date"])) for r in rows]
    if any((b-a).days != 1 for a, b in zip(dates, dates[1:])):
        raise ValueError("Missing or duplicate daily observations; review data before forecasting")
    if any(r.get("data_quality_flag", "ok") != "ok" for r in rows) or result.truncated:
        raise ValueError("A complete, valid demand series is required")
    output = forecast([r["units"] for r in rows], horizon)
    output.update(evidence_id=result.evidence_id, evidence_rows=rows, last_observed_date=dates[-1].isoformat())
    return output
