"""Resolve chat context and citations through bounded, read-only Sentinel services."""

import re
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from sentinel import map as supply_map
from sentinel.config import AS_OF
from sentinel.tasks import StrictModel

WORKSPACE_GUIDE = """
Sentinel is a local supply-chain analysis and decision-support workspace.
- Analyze uses a dated synthetic fixture. Choose Analysis task, set its inputs, then Run analysis.
  Tasks: Supplier late deliveries, Inventory coverage, Demand forecast, Supplier investigation,
  and Demand-increase simulation. Choose supplier and promised-date period for lateness.
  Live truck locations and future delivery schedules are unavailable.
  Supply-chain map and Analyze share this synthetic fixture.
- Published data has separate historical Kaggle retail and SCMS shipment sources. Pick a task
  and scope, then Run published analysis. Tasks are Demand forecast, Coverage and demand what-if,
  Observed shipment lateness, and Shipment model evaluation. Published results are read-only.
  The shipment classifier is a held-out research evaluation, without operational thresholds.
- Demand forecasts use recursive histogram gradient boosting and a seasonal-naive comparison
  repeating the preceding week. The published chart/table provides forecast origin, daily points,
  and estimated quantile bounds. Coverage is on-hand stock divided by mean recent daily demand or
  observed units sold. Zero average makes cover unavailable. What-if changes the demand rate,
  keeps stock fixed, and assumes no replenishment. It is a sensitivity calculation.
- Lateness compares delivered and scheduled/promised dates on evaluable completed records.
  Missing dates are excluded. Sentinel cannot infer open, cancelled or outstanding receipts.
  Vendor history is descriptive. Historical sales can understate demand. Stockout prediction is unavailable.
- Open View evidence after Analyze. Select a captured target, inspect transaction records and
  use pagination. To save a review select a recommendation, enter Reviewer name and Review note,
  and choose Save pending draft. Review edits, simulated approval and rejection are explicit.
  History opens the exact captured snapshot and review. Replay does not run the analysis again.
- Supply Chain Map shows recorded connections as solid wires and exploratory links as dashed.
  Find a record, select a box, use Expand one step and Inspect source when available, or open
  Relationships. Zoom in/out, Reset map zoom and Fit view are map controls.
- Excel import: Supply Chain Map > Import relationships > choose .xlsx or UTF-8 .csv > select
  worksheet when offered > Validate and preview > check row errors and counts > name map > Create map.
  Download blank template or Download example. The Map selector opens persisted maps.
  Required headers: source_type, source_id, relationship, target_type, target_id, source_label, target_label.
  Optional: record_id, event_date (YYYY-MM-DD), quantity (nonnegative), notes. Limit 96 KiB and
  40 relationship rows. Both endpoints and labels are required; keep labels consistent for each
  identity. Use supported types such as supplier and warehouse and a relationship such as supplies.
  Replace formulas with literal values. Use the same header spelling as the template. Fix numbered
  errors, preview again, or confirm just the accepted rows. Imports do not feed predictive models.
- The theme control at the top of the workspace shows Dark or Light and switches the Gruvbox theme.
  Collapse sidebar toggles compact navigation. Ask Qwen opens this assistant.
"""

PUBLISHED_SOURCES = {
    "retail": "https://www.kaggle.com/datasets/anirudhchauhan/retail-store-inventory-forecasting-dataset",
    "shipment": "https://www.kaggle.com/datasets/apoorvwatsky/supply-chain-shipment-pricing-data",
}


class MapContext(StrictModel):
    kind: Literal[
        "supplier",
        "warehouse",
        "shipment",
        "order",
        "order_item",
        "product",
        "store",
        "facility",
        "customer",
        "relationship",
    ]
    record_id: str = Field(min_length=1, max_length=170)
    map_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{24}$")


class Scope(StrictModel):
    label: str = "Workspace help"
    dataset: str | None = None
    version: str | None = None
    analysis_id: str | None = None
    evidence_id: str | None = None
    target_id: str | None = None
    source_id: str | None = None


class WorkspaceAction(StrictModel):
    kind: Literal[
        "inspect_map",
        "open_review",
        "open_import",
        "select_map",
        "theme",
        "fit_map",
        "reset_map",
        "draft_review",
    ]
    label: str
    record: MapContext | None = None
    identity: str | None = None
    text: str | None = None
    version: str | None = None


class Citation(StrictModel):
    label: str
    scope: Scope
    href: str | None = None


class EvidenceArgs(StrictModel):
    offset: int = Field(default=0, ge=0, le=300)
    kind: Literal["shipment", "supplier_event", "inventory", "demand"] | None = None


@dataclass
class Fact:
    text: str
    citation: Citation | None = None
    topic: str = "summary"
    model_text: str | None = None


@dataclass
class Context:
    scope: Scope = field(default_factory=Scope)
    facts: dict[str, Fact] = field(default_factory=dict)
    targets: list[dict] = field(default_factory=list)
    notice: str = ""
    actions: list[WorkspaceAction] = field(default_factory=list)
    carry: dict | None = None
    tool_result: bool = False
    found_data: bool = False

    def add(self, text, citation=None, *, topic="summary", model_text=None):
        self.facts[f"F{len(self.facts) + 1}"] = Fact(text, citation, topic, model_text)

    def relevant(self, question, bound):
        if not bound and self.scope.dataset:
            return []
        rules = [
            (r"\b(interval|bounds|range|uncertainty)\b", ("interval", "first_day", "limits")),
            (r"baseline|compare|different", ("first_day", "evaluation")),
            (r"trust|accuracy|performance|reliable", ("evaluation", "limits")),
            (r"outstanding|overdue|receipts", ("limits",)),
            (r"excluded|unevaluable|stale|missing", ("quality", "limits")),
            (r"records behind|show .*records|transaction", ("records", "summary")),
            (r"where.*from|source|provenance|origin|license", ("provenance", "records", "summary")),
            (r"connections|linked", ("connections", "provenance")),
        ]
        topics = next(
            (topics for pattern, topics in rules if re.search(pattern, question, re.I)), ()
        )
        matches = [
            key for topic in topics for key, fact in self.facts.items() if fact.topic == topic
        ]
        if matches:
            return matches[:3]
        return list(self.facts)[:2] if bound else []

    def source_page(self, store, args):
        scope = self.scope
        if not scope.evidence_id or not scope.target_id:
            self.notice = "Ask the user to select one evidence target or run an analysis for a single supplier or item."
            return
        kind = args.kind or ("shipment" if scope.target_id.startswith("supplier:") else "demand")
        # An evidence page is an example, never the basis for inferred totals.
        page = store.source_records(scope.evidence_id, scope.target_id, args.offset, 4, kind)
        if not page["available"]:
            self.add(page["message"])
            return
        if scope.source_id and page["source_id"] != scope.source_id:
            raise HTTPException(
                409, "The captured source version differs. Reopen the saved analysis."
            )
        scope = scope.model_copy(update={"source_id": page["source_id"]})
        self.scope = scope
        snippets = []
        for item in page["records"]:
            record = item["record"]
            fields = {
                "shipment": ("shipment_id", "promised_date", "delivered_date", "is_late"),
                "demand": ("demand_date", "units"),
                "inventory": ("inventory_date", "on_hand"),
                "supplier_event": ("event_date", "event_type"),
            }[kind]
            snippets.append(
                ", ".join(
                    f"{key.replace('_', ' ')} {record[key]}"
                    for key in fields
                    if record.get(key) is not None
                )
            )
        text = (
            f"Captured {kind.replace('_', ' ')} evidence for {page['target']['label']}: "
            f"{page['total']} records in this category and target, including any excluded records. "
            f"This page shows {len(snippets)} examples at offset {args.offset}."
        )
        if snippets:
            text += "\n" + "\n".join(snippet for snippet in snippets if snippet)
        self.add(
            text,
            Citation(label="Open captured source records", scope=scope),
            topic="records",
            model_text="A bounded page of captured source examples is available. Inspect the registered evidence link for the rest; do not infer totals from the examples.",
        )


def published_context(published, request):
    options = request.model_dump(exclude={"kind"}, exclude_none=True)
    kind = request.kind
    meta = published.manifest or published.require()["manifest"]
    retail = kind in {"forecast", "coverage"}
    source = "retail" if retail else "shipment"
    dataset = meta[source]
    if kind == "forecast":
        result = published.forecast(**options)
        label = f"Retail forecast · {result['scope']}"
    elif kind == "coverage":
        result = published.coverage(**options)
        label = f"Retail coverage · {result['scope']}"
    elif kind == "lateness":
        result = published.lateness(options.get("vendor"))
        label = f"SCMS lateness · {result['scope']}"
    else:
        result = published.shipment_score(**options)
        label = f"SCMS held-out line item {result['record_id']}"
    ctx = Context(Scope(label=label, dataset=dataset["identifier"], version=dataset["sha256"]))
    link = Citation(
        label="Open Kaggle dataset documentation", scope=ctx.scope, href=PUBLISHED_SOURCES[source]
    )
    if kind == "forecast":
        first = result["points"][0]
        model = (
            "quantile histogram gradient boosting"
            if result["method"] != "seasonal_naive"
            else "seven-day seasonal naive"
        )
        ctx.add(
            f"{result['scope']} has a {len(result['points'])}-day forecast of units sold per day from historical origin {result['origin']}. Active method: {model}.",
            link,
        )
        ctx.add(
            f"First forecast day {first['date']}: estimate {first['point']:.2f} units sold; seasonal-naive baseline {first['baseline']:.2f}; estimated lower and upper bounds {first['lower']:.2f} to {first['upper']:.2f} units.",
            link,
            topic="first_day",
            model_text="The first forecast date has a point estimate, a seasonal-naive baseline repeating the preceding week, and lower and upper quantile estimates.",
        )
        best, base = (
            result["evaluation"]["gradient_boosting"],
            result["evaluation"]["seasonal_naive"],
        )
        ctx.add(
            f"Historical final evaluation across {best['n']:,} forecasts: boosting MAE {best['mae']:.2f} and RMSE {best['rmse']:.2f} units; seasonal naive MAE {base['mae']:.2f} and RMSE {base['rmse']:.2f} units. Empirical interval coverage was {best['interval_coverage']:.1%} for boosting and {base['interval_coverage']:.1%} for seasonal naive.",
            link,
            topic="evaluation",
            model_text="The held-out historical evaluation compares MAE, RMSE and empirical interval coverage. Boosting had lower MAE and RMSE than seasonal naive in this test.",
        )
        if result["method"] == "quantile_hist_gradient_boosting":
            ctx.add(
                "The model uses 0.1, 0.5 and 0.9 quantile fits to estimate the lower bound, median and upper bound. The displayed span is intended to cover the central 80% under the model. The median need not equal the midpoint of the bounds. This is a prediction interval for a future observation, and the actual value can fall outside it.",
                link,
                topic="interval",
                model_text="The interval represents a central range of plausible units sold, bounded by lower and upper quantile estimates. The point is a median estimate, not a mode. Values can fall outside; empirical coverage is measured separately. Explain the idea without percent figures.",
            )
        ctx.add(
            "Empirical quantile bounds describe historical forecast uncertainty. Results are from synthetic retail data. Units sold can understate demand, and historical evaluation does not establish live performance.",
            link,
            topic="limits",
        )
    elif kind == "coverage":
        if result["days_of_cover"] is None:
            ctx.add("Coverage is unavailable because observed average daily sales are zero.", link)
        else:
            ctx.add(
                f"At {result['scope']} on {result['origin']}, {result['opening_inventory']} opening units divided by {result['observed_daily_sales']:.2f} average daily units sold give {result['days_of_cover']:.2f} days of coverage. At {result['increase_percent']:g}% higher sales, the scenario has {result['scenario_days_of_cover']:.2f} days.",
                link,
            )
        ctx.add(
            "This uses prior 28-day observed sales, with stock fixed and no replenishment in the scenario. It does not predict when stock will run out.",
            link,
        )
    elif kind == "lateness":
        ctx.add(
            f"{result['scope']}: {result['late']:,} of {result['assessed']:,} assessed SCMS line items were late ({result['late_rate']:.1%}). Source-wide rejected records: {result['source_wide_rejections']}.",
            link,
        )
        if result["examples"]:
            examples = "; ".join(
                f"SCMS {row['record_id']}, CSV record {row['source_row']}, scheduled {row['scheduled']}, delivered {row['delivered']}"
                for row in result["examples"][:3]
            )
            ctx.add(
                f"Examples of late completed line items, not the full population: {examples}.", link
            )
        ctx.add(
            "Delivered after scheduled defines lateness. These are completed line items. This result does not establish outstanding orders or future vendor performance.",
            link,
        )
    else:
        measured = result["evaluation"]["calibrated"]
        base = result["evaluation"]["prevalence"]
        ctx.add(
            f"Held-out SCMS record {result['record_id']} (CSV record {result['source_row']}) was scored {result['score']:.1%} at historical PO send date {result['prediction_time']}. The observed delivery was {'late' if result['observed_late'] else 'on time'}.",
            link,
        )
        ctx.add(
            f"Research only. In the {measured['n']}-item test set, ROC AUC was {measured['roc_auc']:.3f} and Brier score was {measured['brier']:.4f} versus {base['brier']:.4f} for the prevalence baseline. No operational threshold was promoted.",
            link,
        )
    ctx.add(
        f"{'Publisher-generated synthetic retail data' if retail else 'Historical SCMS health commodity data attributed by the uploader to USAID'}; dates {dataset['date_range'][0]} through {dataset['date_range'][1]}; {dataset['accepted']:,} accepted CSV records. License: {dataset['license']}.",
        link,
        topic="provenance",
    )
    return ctx


def analysis_context(service, identity, target_id):
    snap = service.store.analysis(identity)
    targets = snap.get("targets") or []
    target = next((item for item in targets if item["id"] == target_id), None)
    if target_id and target is None:
        raise HTTPException(422, "The selected target is outside this saved analysis.")
    if not target and len(targets) == 1:
        target = targets[0]
    task = snap.get("task") or {}
    kind = task.get("kind", "analysis")
    focus = target["label"] if target else task.get("supplier_id") or "Selected result"
    pack = service.store.evidence(snap["evidence_id"], limit=1) if snap.get("evidence_id") else {}
    scope = Scope(
        label=f"{kind.replace('_', ' ').capitalize()} · {focus}",
        dataset="Sentinel synthetic fixture",
        version=snap["dataset_fingerprint"],
        analysis_id=identity,
        evidence_id=snap.get("evidence_id"),
        target_id=target["id"] if target else None,
        source_id=pack.get("source_id"),
    )
    ctx = Context(scope, targets=targets)
    ref = Citation(
        label="Open captured evidence" if scope.evidence_id else "Open analysis", scope=scope
    )
    period = (
        f" Promised-date period {task['start_date']} to {task['end_date']}."
        if task.get("start_date") and task.get("end_date")
        else ""
    )
    ctx.add(
        f"Saved {kind.replace('_', ' ')} analysis on the Sentinel synthetic fixture, reference date {snap['reference_date']}.{period}",
        ref,
        topic="provenance",
    )
    for item in [target] if target else targets[:6]:
        if item.get("evaluable") is not None and item["id"].startswith("supplier:"):
            supplier_id = item["id"].split(":", 1)[1]
            name = next(
                (
                    row.get("supplier_name")
                    for row in snap["rows"]
                    if row.get("supplier_id") == supplier_id
                ),
                None,
            )
            label = f"{name} ({supplier_id})" if name else item["label"]
            ctx.add(
                f"{label}: {item['late']} late of {item['evaluable']} evaluable deliveries, {item['excluded']} excluded and {item['unassignable']} with unassignable dates.",
                ref,
                model_text=f"The aggregate shows late and evaluable delivery counts for {name or 'the selected supplier'}, with exclusions and unassignable dates accounted for.",
            )
    population = (snap.get("quality") or {}).get("entities") or {}
    if population:
        ctx.add(
            f"Captured population quality: {population.get('assessed', 0)} assessed, {population.get('matching', 0)} matching, and {population.get('unevaluable', 0)} unevaluable.",
            ref,
            topic="quality",
            model_text="The verified snapshot accounts for assessed, matching, and unevaluable populations.",
        )
    for item in [target] if target else targets[:5]:
        if item.get("reasons"):
            ctx.add(
                f"{item['label']} quality reasons: " + "; ".join(item["reasons"]),
                ref,
                topic="quality",
            )
    if kind in {"supplier_delay", "supplier_risk"}:
        ctx.add(
            "These are observed deliveries. The shipment data has no open or cancelled status or receipt quantities. Missing delivered dates do not establish outstanding overdue orders. Purchase orders are not linked to receipts.",
            ref,
            topic="limits",
        )
    if snap.get("forecast"):
        forecast = snap["forecast"]
        first = forecast["points"][0]
        when = snap.get("forecast_context") or {}
        ctx.add(
            f"{task.get('product_id')} at {task.get('warehouse_id')}, origin {when.get('forecast_origin')}: {len(forecast['points'])} days of point estimates. On {first['date']}, estimated {first['forecast']:.2f} units versus {first['baseline']:.2f} for seasonal naive.",
            ref,
        )
        ctx.add(
            "The fixture forecast shows point estimates. Its uncertainty bands are omitted. Seasonal naive repeats the previous week.",
            ref,
        )
    if kind in {"stockout", "what_if"}:
        for row in snap["rows"][:4]:
            if (
                row.get("days_of_cover") is None
                or target
                and not target["id"].endswith(f"{row['product_id']}:{row['warehouse_id']}")
            ):
                continue
            scenario = (
                f" Scenario coverage is {row['scenario_days_of_cover']:.2f} days."
                if row.get("scenario_days_of_cover") is not None
                else ""
            )
            ctx.add(
                f"{row['product_id']} at {row['warehouse_id']}: {row['on_hand']} units, {row['avg_daily_demand']:.2f} mean daily units, {row['days_of_cover']:.2f} days of cover.{scenario}",
                ref,
            )
    if snap["state"] != "success":
        ctx.add(snap["message"], ref)
    return ctx


def map_context(service, imported_maps, selected):
    from urllib.parse import quote

    start = "/api/map/record/"
    if selected.map_id:
        source = imported_maps.get(selected.map_id)
        key = (
            selected.record_id
            if selected.kind == "relationship"
            else f"{selected.kind}:{selected.record_id}"
        )
        if key not in source["records"]:
            raise HTTPException(422, "Select an existing imported map record.")
        item = source["records"][key]
        rows = (
            [row["source_row"] for row in item["rows"]] if "rows" in item else item["source_rows"]
        )
        # Imported notes, labels, filenames, worksheet names and relationship content never reach Qwen.
        scope = Scope(
            label=f"Imported map · {selected.kind}",
            dataset="Spreadsheet relationship import",
            version=source["digest"],
        )
        ctx = Context(scope)
        href = f"/api/imported-maps/{selected.map_id}/record/{selected.kind}/{quote(selected.record_id, safe='')}"
        ctx.add(
            f"This selected {selected.kind.replace('_', ' ')} is supported by uploaded spreadsheet row(s) {', '.join(map(str, rows[:8]))}. Imported relationship data is independent of the analytical datasets.",
            Citation(label="Open imported source record", scope=scope, href=href),
            topic="provenance",
            model_text="This imported connection is traceable to contributing rows in a user-uploaded spreadsheet. The app provides the row identifiers and source record link. This is separate from analytical data.",
        )
        return ctx
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", selected.record_id):
        raise HTTPException(422, "Select a verified fixture map record.")
    record = supply_map.record(service.database, selected.kind, selected.record_id)
    if record["status"] != "valid":
        raise HTTPException(422, "Inspect a uniquely verified map source record first.")
    row = record["record"]
    scope = Scope(
        label=f"Map · {selected.kind} {selected.record_id}",
        dataset="Sentinel synthetic fixture",
        version=service.dataset_fingerprint,
    )
    ctx = Context(scope)
    ref = Citation(
        label="Open verified map source record",
        scope=scope,
        href=start + selected.kind + "/" + quote(selected.record_id, safe=""),
    )
    ctx.add(
        f"{selected.record_id} comes from verified source record {row['source_record_id']} in the Sentinel synthetic fixture, reference date {AS_OF}.",
        ref,
        topic="provenance",
        model_text="The selected map object comes from a verified record in Sentinel’s synthetic fixture. The source ID and reference date are provided by the application.",
    )
    if selected.kind == "shipment":
        ctx.add(
            f"Shipment {selected.record_id} links to supplier {row['supplier_id']}, destination warehouse {row['warehouse_id']}, and order {row['order_id']}.",
            ref,
            topic="connections",
            model_text="The selected shipment has a recorded supplier, a destination warehouse and an associated order.",
        )
    if selected.kind == "shipment" and row.get("promised_date") and row.get("delivered_date"):
        promised, delivered = row["promised_date"], row["delivered_date"]
        ctx.add(
            f"Promised date {promised}. Delivered date {delivered}. The recorded delivery was {'late' if delivered > promised else 'on time'}.",
            ref,
            topic="timing",
            model_text="This fixture shipment has both recorded promised and delivered dates, which can be inspected and compared. Do not infer order receipt quantities or an outstanding order status.",
        )
    summaries = {
        "order": (
            ("order_date", "order date"),
            ("warehouse_id", "recorded warehouse"),
            ("status", "recorded order status"),
        ),
        "supplier": (("supplier_name", "supplier"), ("lead_time_days", "listed lead time in days")),
        "warehouse": (("warehouse_name", "warehouse"),),
        "product": (("product_name", "product"), ("supplier_id", "listed supplier")),
        "order_item": (
            ("order_id", "order"),
            ("product_id", "product"),
            ("quantity", "line quantity"),
        ),
    }
    fields = [
        f"{label} {row[key]}"
        for key, label in summaries.get(selected.kind, ())
        if row.get(key) is not None
    ]
    if fields:
        ctx.add(
            f"{selected.kind.replace('_', ' ').capitalize()} {selected.record_id}: "
            + "; ".join(fields)
            + ".",
            ref,
        )
    ctx.add(
        "Solid wires represent recorded fixture relationships. Dashed exploratory wires are workspace annotations.",
        ref,
    )
    return ctx


def dataset_context(published, workspace):
    if workspace == "published" and published.manifest:
        ctx = Context(Scope(label="Published data · choose a result"))
        for name in ("retail", "shipment"):
            item = published.manifest[name]
            origin = (
                "Publisher-generated synthetic retail"
                if name == "retail"
                else "SCMS health commodity shipments attributed to USAID by the Kaggle uploader"
            )
            scope = Scope(label=origin, dataset=item["identifier"], version=item["sha256"])
            ctx.add(
                f"{origin}; {item['accepted']:,} accepted records, {item['date_range'][0]} to {item['date_range'][1]}. License: {item['license']}.",
                Citation(
                    label="Open dataset documentation", scope=scope, href=PUBLISHED_SOURCES[name]
                ),
            )
        return ctx
    return Context(
        Scope(
            label={
                "workspace": "Analyze · choose a result",
                "map": "Map · no record selected",
                "history": "History · workspace help",
            }.get(workspace, "Workspace help")
        )
    )
