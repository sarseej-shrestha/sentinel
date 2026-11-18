"""HTTP contracts for published-source analytics."""

from typing import Literal

from pydantic import Field

from sentinel.tasks import StrictModel


class ForecastContext(StrictModel):
    kind: Literal["forecast"]
    store: str = Field(min_length=1, max_length=30)
    product: str = Field(min_length=1, max_length=30)


class CoverageContext(StrictModel):
    kind: Literal["coverage"]
    store: str = Field(min_length=1, max_length=30)
    product: str = Field(min_length=1, max_length=30)
    increase: float = Field(ge=0, le=200)


class LatenessContext(StrictModel):
    kind: Literal["lateness"]
    vendor: str | None = Field(default=None, max_length=180)


class ShipmentContext(StrictModel):
    kind: Literal["shipment"]
    record_id: int = Field(gt=0)


class ForecastMetrics(StrictModel):
    n: int
    missing_outcomes: int = 0
    mae: float
    rmse: float
    interval_coverage: float


class ForecastPoint(StrictModel):
    date: str
    point: float
    lower: float
    upper: float
    baseline: float


class PublishedForecast(StrictModel):
    method: Literal["seasonal_naive", "quantile_hist_gradient_boosting"]
    scope: str
    origin: str
    units: Literal["units sold per day"]
    model_version: str
    history_days: int
    excluded_series: int
    points: list[ForecastPoint]
    source_rows: list[int]
    evaluation: dict[str, ForecastMetrics]


class PublishedCoverage(StrictModel):
    scope: str
    origin: str
    opening_inventory: int
    observed_daily_sales: float
    history_days: int
    increase_percent: float
    days_of_cover: float | None
    scenario_days_of_cover: float | None
    source_rows: list[int]


class LateExample(StrictModel):
    source_row: int
    record_id: int
    scheduled: str
    delivered: str


class PublishedLateness(StrictModel):
    scope: str
    origin: str
    assessed: int
    late: int
    late_rate: float
    source_wide_rejections: int
    examples: list[LateExample]


class ScoreMetrics(StrictModel):
    n: int
    positive_rate: float
    mean_probability: float
    roc_auc: float
    average_precision: float
    brier: float
    log_loss: float


class PublishedScore(StrictModel):
    model: str
    model_version: str
    status: Literal["research_only"]
    record_id: int
    source_row: int
    prediction_time: str
    inputs: dict[str, str | int | float]
    score: float
    observed_late: bool
    evaluation: dict[str, ScoreMetrics]
