"""Shared guided-task contract. Natural language may suggest, never broaden, this contract."""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentinel.config import AS_OF
from sentinel.nlq.query_plan import make_plan, validate_query_plan
from sentinel.nlq.registry import resolve_entity


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Task(StrictModel):
    kind: Literal["supplier_delay", "stockout", "forecast", "supplier_risk", "what_if"]
    supplier_id: str | None = None
    product_id: str | None = None
    warehouse_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    horizon_days: int | None = Field(default=None, ge=1, le=90)
    increase_percent: float | None = Field(default=None, ge=0, le=200)

    @model_validator(mode="after")
    def validate_task(self):
        allowed = {
            "supplier_delay": {"supplier_id", "start_date", "end_date"},
            "stockout": {"horizon_days"},
            "forecast": {"product_id", "warehouse_id"},
            "supplier_risk": {"supplier_id"},
            "what_if": {"warehouse_id", "increase_percent"},
        }[self.kind]
        required = allowed - ({"supplier_id"} if self.kind == "supplier_delay" else set())
        values = self.model_dump(exclude_none=True)
        if set(values) - allowed - {"kind"}:
            raise ValueError("This task does not support these additional filters")
        if required - set(values):
            raise ValueError(
                "Complete required fields: " + ", ".join(sorted(required - set(values)))
            )
        for name in ("supplier_id", "product_id", "warehouse_id"):
            if getattr(self, name) is not None:
                setattr(self, name, resolve_entity(name, getattr(self, name)))
        if self.kind == "supplier_delay":
            start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
            if start > end or end > date.fromisoformat(AS_OF):
                raise ValueError(
                    "Choose an increasing historical date range ending on or before " + AS_OF
                )
        self.plan()
        return self

    def plan(self):
        values = {}
        entities = {
            "supplier_id": self.supplier_id,
            "warehouse_id": self.warehouse_id,
            "product_id": self.product_id,
        }
        if self.kind == "supplier_delay":
            values["time_range"] = {
                "start": self.start_date,
                "end": (date.fromisoformat(self.end_date) + timedelta(days=1)).isoformat(),
            }
        if self.kind == "stockout":
            values["horizon_days"] = self.horizon_days
        if self.kind == "forecast":
            values["horizon_days"] = 14
        if self.kind == "what_if":
            values["scenario"] = {"demand_increase": self.increase_percent / 100}
        return validate_query_plan(make_plan(self.kind, entities=entities, **values))

    def question(self):
        if self.kind == "supplier_delay":
            subject = self.supplier_id or "suppliers"
            return (
                f"Show {subject} late delivery rate between {self.start_date} and {self.end_date}"
            )
        if self.kind == "stockout":
            return f"Show inventory coverage below {self.horizon_days} days"
        if self.kind == "forecast":
            return f"Forecast demand for {self.product_id} at {self.warehouse_id}"
        if self.kind == "supplier_risk":
            return f"Show supplier evidence for {self.supplier_id}"
        return (
            f"What happens if demand increases by {self.increase_percent}% at {self.warehouse_id}?"
        )
