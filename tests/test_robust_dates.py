import pytest

from sentinel.nlq.dates import extract_date_range, historical_window
from sentinel.nlq.query_plan import request_plan


@pytest.mark.parametrize(
    "text,start,end,grain",
    [
        ("previous calendar month", "2025-12-01", "2026-01-01", "month"),
        ("next calendar month", "2026-02-01", "2026-03-01", "month"),
        ("current month", "2026-01-01", "2026-02-01", "month"),
        ("past thirty days", "2025-12-07", "2026-01-06", "range"),
        ("last 6 completed days", "2025-12-31", "2026-01-06", "range"),
        ("previous calendar quarter", "2025-10-01", "2026-01-01", "quarter"),
        ("between 2025-11-02 and 2025-11-08", "2025-11-02", "2025-11-09", "range"),
        ("between 2025-11-02 and 2025-11-08 exclusive", "2025-11-02", "2025-11-08", "range"),
        ("November 2 through November 8, 2025", "2025-11-02", "2025-11-09", "range"),
        ("daily history over past thirty days", "2025-12-07", "2026-01-06", "day"),
        ("weekly history during previous calendar month", "2025-12-01", "2026-01-01", "week"),
    ],
)
def test_compositional_date_normalization(text, start, end, grain):
    window, _ = extract_date_range(text, "2026-01-06")
    assert (window.start, window.end, window.granularity) == (start, end, grain)


@pytest.mark.parametrize(
    "text",
    [
        "02/03/2026",
        "last period",
        "some time in June",
        "June 2 through June 9",
        "current month or previous calendar month",
        "past 367 days",
        "last 0 completed days",
        "November 30 through November 2, 2025",
    ],
)
def test_ambiguous_or_invalid_dates_reject(text):
    with pytest.raises(ValueError):
        extract_date_range(text, "2026-01-06")


def test_interpretation_is_not_permission_to_query_future_evidence():
    assert extract_date_range("next calendar month", "2026-01-06")[0].start == "2026-02-01"
    with pytest.raises(ValueError, match="future"):
        historical_window("next calendar month", "2026-01-06")


@pytest.mark.parametrize(
    "period", ["current month", "past thirty days", "previous calendar quarter"]
)
def test_curated_supplier_sentence_uses_date_resolver(period):
    plan = request_plan(f"Rank suppliers by their late-arrival share during {period}.")
    assert plan["intent"] == "supplier_delay"
    assert plan["time_range"] == historical_window(period)[0].boundaries()
