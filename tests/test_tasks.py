import pytest
from pydantic import ValidationError

from sentinel.nlq.authority import resolve
from sentinel.nlq.query_plan import QUERY_PLAN_SCHEMA
from sentinel.tasks import Task

TASKS = [
    {"kind": "supplier_delay", "start_date": "2026-08-01", "end_date": "2026-08-31"},
    {"kind": "stockout", "horizon_days": 14},
    {"kind": "forecast", "product_id": "P1", "warehouse_id": "W3"},
    {"kind": "supplier_risk", "supplier_id": "S1"},
    {"kind": "what_if", "warehouse_id": "W3", "increase_percent": 15.0},
]


@pytest.mark.parametrize("values", TASKS)
def test_forms_and_language_share_one_plan(values):
    task = Task(**values)
    resolution = resolve(task.question())
    assert resolution["state"] == "supported"
    assert {k: resolution["plan"][k] for k in QUERY_PLAN_SCHEMA["properties"]} == task.plan()


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "stockout", "horizon_days": 14, "warehouse_id": "W3"},
        {"kind": "forecast", "product_id": "P1"},
        {"kind": "what_if", "warehouse_id": "W3", "increase_percent": float("nan")},
        {"kind": "supplier_delay", "start_date": "2026-09-30", "end_date": "2026-10-01"},
        {"kind": "supplier_risk", "supplier_id": "Unknown"},
        {"kind": "stockout", "horizon_days": True},
    ],
)
def test_invalid_fields_are_never_ignored(values):
    with pytest.raises(ValidationError):
        Task(**values)


def test_aliases_normalize():
    task = Task(kind="forecast", product_id="Product 1", warehouse_id="Warehouse 3")
    assert task.product_id == "P1" and task.warehouse_id == "W3"
