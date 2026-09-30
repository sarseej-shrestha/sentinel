"""Arithmetic scenarios do not imply causal estimates or external actions."""

import math


def demand_scenario(row, increase=0.15):
    if not math.isfinite(increase) or not -0.9 <= increase <= 2:
        raise ValueError("Demand change must be between -0.9 and 2")
    if row.get("avg_daily_demand") is None or row.get("on_hand") is None:
        raise ValueError("Demand and inventory evidence are required")
    demand = row["avg_daily_demand"] * (1 + increase)
    return {
        "warehouse_id": row["warehouse_id"],
        "product_id": row["product_id"],
        "demand_change": increase,
        "scenario_daily_demand": demand,
        "scenario_days_of_cover": row["on_hand"] / demand if demand > 0 else None,
        "evidence_rows": [dict(row)],
        "source": "constant_stock_demand_scaling_rule_v1",
        "simulated_action_only": True,
        "assumptions": [
            "Inventory, replenishment, price and supplier capacity held constant.",
            "Arithmetic sensitivity analysis; not a causal forecast.",
        ],
    }
