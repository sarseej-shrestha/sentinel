"""Capture bounded, read-only source snapshots and account for requested populations.

These fixed table reads enrich evidence; they never compile user SQL or approve an
operation. No client chooses a table. Saved source rows are reused during replay.
"""

import json
from collections import Counter, defaultdict
from datetime import date, timedelta

import duckdb

from sentinel.config import AS_OF

TABLES = {
    "shipments",
    "suppliers",
    "supplier_events",
    "inventory_daily",
    "demand_daily",
    "products",
    "warehouses",
}


def target_id(kind, row):
    if kind in {"supplier_delay", "supplier_risk", "shipments"}:
        return "supplier:" + row["supplier_id"]
    return "position:" + row["product_id"] + ":" + row["warehouse_id"]


def capture(database, plan):
    kind = plan["intent"]
    if kind not in {"supplier_delay", "supplier_risk", "stockout", "what_if", "forecast"}:
        return None
    names = (
        {"suppliers", "shipments"}
        if kind.startswith("supplier")
        else {"inventory_daily", "demand_daily", "products", "warehouses"}
    )
    if kind == "supplier_risk":
        names.add("supplier_events")
    data = {}
    with duckdb.connect(
        str(database), read_only=True, config={"enable_external_access": "false"}
    ) as con:
        con.execute("BEGIN TRANSACTION")
        for name in sorted(names):
            if name not in TABLES:
                raise ValueError("Unsupported evidence source")
            cursor = con.execute(f"SELECT * FROM {name} ORDER BY source_record_id LIMIT 10001")
            columns = [x[0] for x in cursor.description]
            values = cursor.fetchall()
            if len(values) > 10000:
                raise ValueError(
                    "Source scope exceeds the local snapshot capacity; no truncated evidence is accepted"
                )
            data[name] = json.loads(
                json.dumps([dict(zip(columns, r)) for r in values], default=str, allow_nan=False)
            )
        con.execute("COMMIT")
    sources, targets = [], {}
    counts = defaultdict(Counter)
    missing = 0
    reference = date.fromisoformat(AS_OF)

    def add(row, target, source_kind, membership="in_scope", contribution=None):
        reasons = [] if row["data_quality_flag"] == "ok" else row["data_quality_flag"].split(";")
        disposition = (
            "unassignable"
            if membership == "unassignable"
            else "excluded"
            if reasons
            else "included"
        )
        counts[source_kind][disposition] += 1
        sources.append(
            {
                "id": row["source_record_id"],
                "target_id": target,
                "kind": source_kind,
                "membership": membership,
                "disposition": disposition,
                "contribution": contribution if disposition == "included" else None,
                "reasons": reasons,
                "record": row,
            }
        )
        return disposition

    if kind.startswith("supplier"):
        supplier = plan["entities"]["supplier_id"]
        span = plan["time_range"]
        groups = defaultdict(list)
        for row in data["shipments"]:
            if supplier and row["supplier_id"] != supplier:
                continue
            promised = row["promised_date"]
            membership = "in_scope"
            if span:
                if promised is None:
                    membership = "unassignable"
                elif not span["start"] <= promised < span["end"]:
                    continue
            target = target_id(kind, row)
            late = bool(promised and row["delivered_date"] and row["delivered_date"] > promised)
            status = add(row, target, "shipment", membership, "late" if late else "on_time")
            groups[target].append((row, status))
        if kind == "supplier_risk" and supplier:
            groups.setdefault("supplier:" + supplier, [])
        for target, rows in groups.items():
            scoped = [(r, s) for r, s in rows if s != "unassignable"]
            valid = [r for r, s in scoped if s == "included"]
            targets[target] = {
                "id": target,
                "label": target.split(":")[1],
                "assessed": bool(valid),
                "matching": bool(valid),
                "period_records": len(scoped),
                "evaluable": len(valid),
                "late": sum(r["delivered_date"] > r["promised_date"] for r in valid),
                "excluded": len(scoped) - len(valid),
                "unassignable": len(rows) - len(scoped),
                "missing_observations": 0,
                "reasons": [] if valid else ["No evaluable deliveries in the requested scope"],
            }
        for row in data["suppliers"]:
            target = "supplier:" + row["supplier_id"]
            if target in targets:
                add(row, target, "supplier")
        for row in data.get("supplier_events", []):
            target = "supplier:" + row["supplier_id"]
            dt = row["event_date"]
            if (
                target in targets
                and row["event_type"] == "delay"
                and (dt is None or str(reference - timedelta(days=7)) < dt <= AS_OF)
            ):
                add(
                    row,
                    target,
                    "supplier_event",
                    "unassignable" if dt is None else "in_scope",
                    "review_rule_input",
                )
    else:
        groups = defaultdict(list)
        for row in data["inventory_daily"]:
            if (
                plan["entities"]["warehouse_id"]
                and row["warehouse_id"] != plan["entities"]["warehouse_id"]
            ):
                continue
            if (
                plan["entities"]["product_id"]
                and row["product_id"] != plan["entities"]["product_id"]
            ):
                continue
            groups[target_id(kind, row)].append(row)
        if kind == "forecast":
            groups.setdefault(
                "position:"
                + plan["entities"]["product_id"]
                + ":"
                + plan["entities"]["warehouse_id"],
                [],
            )
        demand = defaultdict(list)
        for row in data["demand_daily"]:
            target = target_id(kind, row)
            if target in groups and (
                kind == "forecast"
                or row["demand_date"] is None
                or str(reference - timedelta(days=28)) < row["demand_date"] <= AS_OF
            ):
                demand[target].append(row)
        for target, inventory in groups.items():
            last = max((r["inventory_date"] or "" for r in inventory), default="")
            latest = [r for r in inventory if r["inventory_date"] == last]
            history = demand[target]
            valid = [r for r in history if r["data_quality_flag"] == "ok"]
            expected = {str(reference - timedelta(days=i)) for i in range(28)}
            observed = {r["demand_date"] for r in history}
            absent = len(expected - observed) if kind != "forecast" else 0
            missing += absent
            reasons = []
            if kind == "forecast":
                assessed = bool(valid) and len(valid) == len(history)
            else:
                assessed = (
                    len(latest) == 1
                    and latest[0]["data_quality_flag"] == "ok"
                    and not absent
                    and len(valid) == 28
                )
            mean = sum(r["units"] for r in valid) / len(valid) if valid else None
            if kind != "forecast" and not mean:
                assessed = False
            if not assessed:
                reasons.append("Incomplete, ambiguous or invalid inventory/demand evidence")
            cover = latest[0]["on_hand"] / mean if assessed and latest and mean else None
            matching = assessed and (kind != "stockout" or cover < plan["horizon_days"])
            targets[target] = {
                "id": target,
                "label": target.removeprefix("position:").replace(":", " / "),
                "assessed": assessed,
                "matching": bool(matching),
                "missing_observations": absent,
                "reasons": reasons,
            }
            if kind != "forecast":
                for row in latest:
                    add(row, target, "inventory", contribution="coverage_input")
            for row in history:
                add(
                    row,
                    target,
                    "demand",
                    "unassignable" if row["demand_date"] is None else "in_scope",
                    "forecast_input" if kind == "forecast" else "coverage_input",
                )
    total = len(targets)
    assessed = sum(t["assessed"] for t in targets.values())
    matching = sum(t["matching"] for t in targets.values())
    quality = {
        "scope": "Observed entities in the requested scope; unassignable dates are reported separately, never assigned to a period.",
        "entities": {
            "total": total,
            "assessed": assessed,
            "matching": matching,
            "not_matching": assessed - matching,
            "unevaluable": total - assessed,
        },
        "observations": {
            k: {
                **{x: v[x] for x in ("included", "excluded", "unassignable")},
                "total": sum(v.values()),
            }
            for k, v in counts.items()
        },
        "missing_observations": missing,
        "limitations": [
            "No catalog activity is assumed. Missing promised dates have unknown period membership."
        ]
        if kind == "supplier_delay"
        else [],
    }
    return {
        "version": 1,
        "period": plan.get("time_range"),
        "reference_date": AS_OF,
        "quality": quality,
        "targets": list(targets.values()),
        "records": sources,
    }
