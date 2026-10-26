import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Database,
  FileSearch,
  History as HistoryIcon,
  Layers,
  LineChart,
  Package,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Truck,
  X,
} from "lucide-react";
import { api } from "./api";
import type {
  Action,
  Analysis,
  Catalog,
  Evidence,
  History,
  Kind,
  Row,
  Task,
} from "./types";

const tasks: {
  kind: Kind;
  title: string;
  short: string;
  description: string;
  icon: typeof Truck;
}[] = [
  {
    kind: "supplier_delay",
    title: "Supplier late deliveries",
    short: "Late deliveries",
    description:
      "Compare observed delivery performance within an explicit historical period.",
    icon: Truck,
  },
  {
    kind: "stockout",
    title: "Inventory coverage",
    short: "Inventory coverage",
    description:
      "Find inventory positions with fewer days of coverage than your review threshold.",
    icon: Package,
  },
  {
    kind: "forecast",
    title: "Demand forecast",
    short: "Demand forecast",
    description:
      "Compare a 14-day point forecast with the seasonal-naïve baseline.",
    icon: LineChart,
  },
  {
    kind: "supplier_risk",
    title: "Supplier investigation",
    short: "Supplier investigation",
    description:
      "Inspect observed performance, review drivers and the evidence behind a supplier flag.",
    icon: Search,
  },
  {
    kind: "what_if",
    title: "Demand-increase simulation",
    short: "What-if simulation",
    description:
      "Explore a demand increase while holding inventory and replenishment constant.",
    icon: SlidersHorizontal,
  },
];
const defaults: Record<Kind, Task> = {
  supplier_delay: {
    kind: "supplier_delay",
    start_date: "2026-08-01",
    end_date: "2026-08-31",
  },
  stockout: { kind: "stockout", horizon_days: 14 },
  forecast: { kind: "forecast", product_id: "P1", warehouse_id: "W3" },
  supplier_risk: { kind: "supplier_risk", supplier_id: "S1" },
  what_if: { kind: "what_if", warehouse_id: "W3", increase_percent: 15 },
};
const labels: Record<string, string> = {
  supplier_id: "Supplier",
  supplier_name: "Name",
  product_id: "Product",
  warehouse_id: "Warehouse",
  late_delivery_rate: "Late deliveries (%)",
  evaluable_shipments: "Evaluable deliveries",
  excluded_shipments: "Excluded deliveries",
  included_shipments: "Period deliveries",
  days_of_cover: "Coverage (days)",
  on_hand: "Stock (units)",
  avg_daily_demand: "Daily demand (units/day)",
  review_level: "Review level",
  review_reasons: "Review drivers",
  scenario_daily_demand: "Scenario demand (units/day)",
  scenario_days_of_cover: "Scenario coverage (days)",
  demand_date: "Date",
  units: "Demand (units)",
  source_record_id: "Source record",
  inventory_date: "Inventory date",
  history_days: "History (days)",
  missing_or_invalid_shipments: "Excluded deliveries",
  recent_delay_events: "Recent delay events",
  data_quality_flag: "Quality",
  excluded_demand_count: "Excluded demand rows",
  forecast: "Forecast (units/day)",
  baseline: "Baseline (units/day)",
  date: "Date",
};
function display(value: unknown, key = ""): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "number")
    return key === "late_delivery_rate"
      ? (value * 100).toFixed(1) + "%"
      : Number.isInteger(value)
        ? value.toLocaleString()
        : value.toFixed(2);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function Table({
  rows,
  columns,
  caption,
}: {
  rows: Row[];
  columns?: string[];
  caption: string;
}) {
  const keys = columns ?? [...new Set(rows.flatMap(Object.keys))];
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {keys.map((k) => (
              <th scope="col" key={k}>
                {labels[k] ?? k.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {keys.map((k) => (
                <td key={k}>{display(row[k], k)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Alert({ children }: { children: React.ReactNode }) {
  return (
    <div className="alert" role="alert">
      <Activity size={18} />
      <span>{children}</span>
    </div>
  );
}
function interpretation(task: Task) {
  switch (task.kind) {
    case "supplier_delay":
      return `${task.supplier_id ?? "All suppliers"} · ${task.start_date} through ${task.end_date}, inclusive · promised-date lateness rate`;
    case "stockout":
      return `All inventory positions · coverage below ${task.horizon_days} days · latest snapshot`;
    case "forecast":
      return `${task.product_id} at ${task.warehouse_id} · next 14 daily demand estimates · seasonal baseline`;
    case "supplier_risk":
      return `${task.supplier_id} · all observed deliveries · review rules and synthetic model drivers`;
    case "what_if":
      return `${task.warehouse_id} · increase demand by ${task.increase_percent}% · stock and replenishment unchanged`;
  }
}
function ForecastChart({
  forecast,
}: {
  forecast: NonNullable<Analysis["forecast"]>;
}) {
  const points = forecast.points,
    max = Math.max(...points.flatMap((p) => [p.forecast, p.baseline])) * 1.15;
  const x = (i: number) => 48 + (i * 520) / (points.length - 1),
    y = (v: number) => 200 - (v / max) * 150;
  const path = (key: "forecast" | "baseline") =>
    points.map((p, i) => `${x(i)},${y(p[key])}`).join(" ");
  return (
    <>
      <div className="chart-legend">
        <span>
          <i className="cyan" />
          Point forecast
        </span>
        <span>
          <i className="amber" />
          Seasonal-naïve baseline
        </span>
      </div>
      <svg
        className="forecast-chart"
        viewBox="0 0 600 250"
        role="img"
        aria-label="Fourteen-day demand forecast and seasonal-naïve baseline. Exact values are available in the table below."
      >
        <text x="48" y="22">
          Units per day
        </text>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1="48"
              y1={y(t * max)}
              x2="570"
              y2={y(t * max)}
              className="grid-line"
            />
            <text x="8" y={y(t * max) + 4}>
              {Math.round(t * max)}
            </text>
          </g>
        ))}
        <polyline points={path("baseline")} className="baseline-line" />
        <polyline points={path("forecast")} className="forecast-line" />
        {[0, 6, 13].map((i) => (
          <text
            key={i}
            x={x(i)}
            y="229"
            textAnchor={i === 0 ? "start" : i === 13 ? "end" : "middle"}
          >
            {points[i].date.slice(5)}
          </text>
        ))}
      </svg>
      <details>
        <summary>View forecast data table</summary>
        <Table
          rows={points}
          columns={["date", "forecast", "baseline"]}
          caption="Daily forecast and baseline, in units per day"
        />
      </details>
    </>
  );
}
function ScenarioChart({ rows }: { rows: Row[] }) {
  const max = Math.max(...rows.map((r) => Number(r.days_of_cover) || 0), 1);
  return (
    <div className="scenario-chart">
      <div className="chart-legend">
        <span>
          <i className="cyan" />
          Baseline coverage
        </span>
        <span>
          <i className="amber" />
          Scenario coverage
        </span>
      </div>
      {rows.map((r, i) => (
        <div className="comparison" key={i}>
          <strong>{String(r.product_id)}</strong>
          <div>
            <div
              className="bar cyan"
              style={{ width: `${(Number(r.days_of_cover) / max) * 100}%` }}
            />
            <div
              className="bar amber"
              style={{
                width: `${(Number(r.scenario_days_of_cover) / max) * 100}%`,
              }}
            />
          </div>
          <span>
            {display(r.days_of_cover)} → {display(r.scenario_days_of_cover)}{" "}
            days
          </span>
        </div>
      ))}
    </div>
  );
}
function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => {
      element?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog ref={ref} onCancel={onClose} aria-label={title}>
      <div className="dialog-heading">
        <h2>{title}</h2>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close dialog"
        >
          <X />
        </button>
      </div>
      {children}
    </dialog>
  );
}
function EvidenceDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [page, setPage] = useState<Evidence | null>(null),
    [offset, setOffset] = useState(0),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setError("");
    api<Evidence>(
      `/evidence/${id}?offset=${offset}&limit=25`,
      undefined,
      controller.signal,
    )
      .then(setPage)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [id, offset]);
  return (
    <Dialog title="Inspect source evidence" onClose={onClose}>
      {error ? (
        <Alert>{error}</Alert>
      ) : !page ? (
        <p role="status">Verifying the snapshot and retrieving source rows…</p>
      ) : (
        <>
          <div className="verified">
            <ShieldCheck size={18} /> Snapshot integrity verified
          </div>
          <p className="muted">
            Reference date {page.reference_date} · {page.included_count} result
            rows · {page.excluded_count} excluded source observations
          </p>
          {page.truncated && (
            <Alert>
              This result reached the query cap. Do not treat it as complete
              evidence.
            </Alert>
          )}
          <Table
            rows={page.rows}
            caption="Full source evidence, including lineage and quality flags"
          />
          <div className="pagination">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >
              <ChevronLeft size={16} /> Previous
            </button>
            <span aria-live="polite">
              {offset + 1}–{Math.min(offset + 25, page.total)} of {page.total}
            </span>
            <button
              disabled={offset + 25 >= page.total}
              onClick={() => setOffset(offset + 25)}
            >
              Next <ChevronRight size={16} />
            </button>
          </div>
          <details>
            <summary>Provenance and query details</summary>
            <dl>
              <dt>Dataset fingerprint</dt>
              <dd className="hash">{page.dataset_fingerprint}</dd>
              <dt>Evidence ID</dt>
              <dd className="hash">{page.id}</dd>
              <dt>Configuration versions</dt>
              <dd>
                <pre>{JSON.stringify(page.versions, null, 2)}</pre>
              </dd>
              <dt>Verified query and parameters</dt>
              <dd>
                <pre>
                  {page.sql}
                  {"\n"}
                  {JSON.stringify(page.parameters, null, 2)}
                </pre>
              </dd>
            </dl>
          </details>
          <p className="fine">
            Local hashes detect changes to stored snapshots. They do not
            establish independent authenticity.
          </p>
        </>
      )}
    </Dialog>
  );
}
function Review({
  analysis,
  existing,
  onSaved,
}: {
  analysis: Analysis;
  existing?: Action;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState(existing?.recommendation_id ?? ""),
    [text, setText] = useState(existing?.text ?? ""),
    [reviewer, setReviewer] = useState(existing?.reviewer ?? ""),
    [action, setAction] = useState<Action | undefined>(existing),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const retry = useRef<{ signature: string; key: string } | null>(null);
  const final = action && action.state !== "pending";
  const dirty = !!action && text !== action.text;
  async function submit(decision?: "edit" | "approve" | "reject") {
    setBusy(true);
    setError("");
    const endpoint = action
      ? `/proposals/${action.id}/decisions`
      : "/proposals";
    const payload = action
      ? {
          decision,
          reviewer,
          expected_revision: action.revision,
          ...(decision === "edit" ? { text } : {}),
        }
      : {
          analysis_id: analysis.analysis_id,
          recommendation_id: selected,
          reviewer,
          text,
        };
    const signature = JSON.stringify([endpoint, payload]);
    if (retry.current?.signature !== signature)
      retry.current = { signature, key: crypto.randomUUID() };
    try {
      const saved = await api<Action>(endpoint, {
        ...payload,
        key: retry.current.key,
      });
      setAction(saved);
      setText(saved.text);
      retry.current = null;
      onSaved();
      setConfirm(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="review-section">
      <div className="section-title">
        <ClipboardCheck size={19} />
        <h3>Human review</h3>
        <span className="tag">Simulation only</span>
      </div>
      <p className="muted">
        Choose exactly what to review. A saved draft stays pending until you
        explicitly approve or reject it.
      </p>
      {error && <Alert>{error}</Alert>}
      {!action && (
        <label>
          Recommendation
          <select
            aria-label="Recommendation"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setText(
                analysis.recommendations.find((r) => r.id === e.target.value)
                  ?.text ?? "",
              );
            }}
          >
            <option value="">Choose a recommendation</option>
            {analysis.recommendations.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label} — {r.text}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Reviewer name
        <input
          value={reviewer}
          maxLength={100}
          disabled={!!final || busy}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="Your name"
          autoComplete="name"
        />
      </label>
      <label>
        Review note
        <textarea
          value={text}
          maxLength={2000}
          rows={3}
          disabled={!!final || busy}
          onChange={(e) => setText(e.target.value)}
          placeholder="Select a recommendation, then edit your review note."
        />
      </label>
      <p className="fine">
        Reviewer names are local attribution, not authenticated identities. No
        orders, inventory or suppliers will be changed.
      </p>
      {final ? (
        <div className="verified" role="status">
          <Check size={18} />
          {action.state === "approved_simulation"
            ? "Simulation approved"
            : "Simulation rejected"}{" "}
          · revision {action.revision}. This decision is final.
        </div>
      ) : (
        <div className="actions">
          {!action ? (
            <button
              className="primary"
              disabled={busy || !selected || !text.trim() || !reviewer.trim()}
              onClick={() => submit()}
            >
              {busy ? "Saving draft…" : "Save pending draft"}
            </button>
          ) : (
            <>
              <span className="tag">Pending · revision {action.revision}</span>
              <button
                disabled={busy || !dirty}
                onClick={() => setText(action.text)}
              >
                Revert draft
              </button>
              <button
                disabled={busy || !dirty || !text.trim() || !reviewer.trim()}
                onClick={() => submit("edit")}
              >
                Save edits
              </button>
              <button
                disabled={busy || dirty || !reviewer.trim()}
                onClick={() => setConfirm("reject")}
              >
                Reject simulation
              </button>
              <button
                className="primary"
                disabled={busy || dirty || !reviewer.trim()}
                onClick={() => setConfirm("approve")}
              >
                Approve simulation
              </button>
            </>
          )}
        </div>
      )}
      {dirty && (
        <p className="fine">
          Save edits before making a final decision. Revert affects only your
          unsaved text.
        </p>
      )}
      {confirm && (
        <Dialog
          title={
            confirm === "approve"
              ? "Approve this simulation?"
              : "Reject this simulation?"
          }
          onClose={() => {
            if (!busy) setConfirm(null);
          }}
        >
          <p>
            This records a final simulated decision for the selected evidence.
            It cannot be edited or undone. No external action will occur.
          </p>
          <blockquote>{action?.text}</blockquote>
          {error && <Alert>{error}</Alert>}
          <div className="actions">
            <button disabled={busy} onClick={() => setConfirm(null)}>
              Keep reviewing
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => submit(confirm)}
            >
              {busy
                ? "Committing decision…"
                : `Confirm ${confirm === "approve" ? "approval" : "rejection"}`}
            </button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
export default function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [catalogError, setCatalogError] = useState(""),
    [kind, setKind] = useState<Kind>("supplier_delay"),
    [forms, setForms] = useState(defaults),
    [view, setView] = useState<"workspace" | "history">("workspace"),
    [result, setResult] = useState<Analysis | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [evidence, setEvidence] = useState<string | null>(null),
    [history, setHistory] = useState<History | null>(null),
    [historyError, setHistoryError] = useState(""),
    [historyLoading, setHistoryLoading] = useState(false),
    [existing, setExisting] = useState<Action | undefined>(),
    [replay, setReplay] = useState(false);
  const sequence = useRef(0),
    controller = useRef<AbortController | null>(null),
    resultHeading = useRef<HTMLHeadingElement>(null);
  const task = tasks.find((t) => t.kind === kind)!,
    form = forms[kind];
  function loadCatalog() {
    setCatalogError("");
    api<Catalog>("/catalog")
      .then(setCatalog)
      .catch((e) => setCatalogError(e.message));
  }
  function loadHistory() {
    setHistoryLoading(true);
    setHistoryError("");
    api<History>("/history")
      .then(setHistory)
      .catch((e) => setHistoryError(e.message))
      .finally(() => setHistoryLoading(false));
  }
  useEffect(() => {
    loadCatalog();
    return () => controller.current?.abort();
  }, []);
  function reset() {
    sequence.current++;
    controller.current?.abort();
    setLoading(false);
    setResult(null);
    setError("");
    setExisting(undefined);
    setReplay(false);
  }
  function choose(next: Kind) {
    reset();
    setKind(next);
    setView("workspace");
  }
  function update(values: Partial<Task>) {
    reset();
    setForms({ ...forms, [kind]: { ...form, ...values } });
  }
  async function run(e: React.FormEvent) {
    e.preventDefault();
    const id = ++sequence.current;
    controller.current?.abort();
    controller.current = new AbortController();
    setLoading(true);
    setError("");
    setResult(null);
    setExisting(undefined);
    setReplay(false);
    try {
      const next = await api<Analysis>(
        "/analyses",
        form,
        controller.current.signal,
      );
      if (id === sequence.current) {
        setResult(next);
        setTimeout(() => resultHeading.current?.focus(), 0);
      }
    } catch (e) {
      if (id === sequence.current && (e as Error).name !== "AbortError")
        setError((e as Error).message);
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }
  async function openSnapshot(id: string, action?: Action) {
    reset();
    setView("workspace");
    setLoading(true);
    const serial = sequence.current;
    try {
      const next = await api<Analysis>("/analyses/" + id);
      if (serial !== sequence.current) return;
      setResult(next);
      setExisting(action);
      setReplay(true);
      if (next.task) {
        setKind(next.task.kind);
        setForms((f) => ({ ...f, [next.task!.kind]: next.task! }));
      }
      setTimeout(() => resultHeading.current?.focus(), 0);
    } catch (e) {
      if (serial === sequence.current) setError((e as Error).message);
    } finally {
      if (serial === sequence.current) setLoading(false);
    }
  }
  function select(
    name: "supplier_id" | "warehouse_id" | "product_id",
    title: string,
    optional = false,
  ) {
    return (
      <label>
        {title}
        <select
          value={String(form[name] ?? "")}
          onChange={(e) => update({ [name]: e.target.value || undefined })}
          required={!optional}
        >
          {optional && <option value="">All suppliers</option>}
          {Object.entries(catalog?.entities[name] ?? {}).map(
            ([id, aliases]) => (
              <option key={id} value={id}>
                {aliases.find(
                  (a) =>
                    a.startsWith("Supplier ") ||
                    a.startsWith("Warehouse ") ||
                    a.startsWith("Product "),
                ) ?? id}{" "}
                · {id}
              </option>
            ),
          )}
        </select>
      </label>
    );
  }
  const status = loading
    ? "loading"
    : error
      ? "error"
      : (result?.state ?? "initial");
  const columns: Record<Kind, string[]> = {
    supplier_delay: [
      "supplier_id",
      "supplier_name",
      "evaluable_shipments",
      "excluded_shipments",
      "late_delivery_rate",
    ],
    stockout: [
      "product_id",
      "warehouse_id",
      "on_hand",
      "avg_daily_demand",
      "days_of_cover",
      "review_level",
    ],
    forecast: ["demand_date", "units"],
    supplier_risk: [
      "supplier_id",
      "late_delivery_rate",
      "evaluable_shipments",
      "missing_or_invalid_shipments",
      "recent_delay_events",
      "review_level",
      "review_reasons",
    ],
    what_if: [
      "product_id",
      "on_hand",
      "avg_daily_demand",
      "scenario_daily_demand",
      "days_of_cover",
      "scenario_days_of_cover",
    ],
  };
  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        Skip to workspace
      </a>
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Sentinel workspace home">
          <div className="brand-mark">
            <ShieldCheck />
          </div>
          <span>
            SENTINEL<small>Evidence before action</small>
          </span>
        </a>
        <div className="nav-label">ANALYSIS WORKSPACE</div>
        <nav aria-label="Analysis tasks">
          {tasks.map((t, i) => (
            <button
              key={t.kind}
              className={`nav-task ${kind === t.kind && view === "workspace" ? "active" : ""}`}
              aria-current={
                kind === t.kind && view === "workspace" ? "page" : undefined
              }
              onClick={() => choose(t.kind)}
            >
              <t.icon size={19} />
              <span>{t.short}</span>
              <small>0{i + 1}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-divider" />
        <button
          className={`nav-task ${view === "history" ? "active" : ""}`}
          onClick={() => {
            reset();
            setView("history");
            loadHistory();
          }}
        >
          <HistoryIcon size={19} />
          <span>Decision history</span>
        </button>
        <div className="sidebar-bottom">
          <div className="local-badge">
            <span className={catalog ? "online-dot" : "offline-dot"} />
            {catalog
              ? "Local workspace connected"
              : "Connecting to local service"}
          </div>
          <p>
            Single-user · Synthetic data
            <br />
            Human-reviewed simulations
          </p>
          <small>SIGSEGV / CMPS 4200</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            <Layers size={16} /> Operations /{" "}
            {view === "history" ? "History" : "Analysis"}
          </span>
          <div>
            <span className="synthetic-badge">SYNTHETIC DATA</span>
            <span className="reference">
              Reference date{" "}
              <strong>{catalog?.reference_date ?? "Loading…"}</strong>
            </span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          {catalogError && (
            <Alert>
              {catalogError} <button onClick={loadCatalog}>Reconnect</button>
            </Alert>
          )}
          {view === "history" ? (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">REVIEW & ACCOUNTABILITY</div>
                  <h1>Decision history</h1>
                  <p>
                    Verified snapshots of what was reviewed, edited and decided.
                  </p>
                </div>
                <button onClick={loadHistory} disabled={historyLoading}>
                  <RefreshCw size={16} /> Refresh history
                </button>
              </div>
              {historyError && <Alert>{historyError}</Alert>}
              {historyLoading ? (
                <div className="panel empty-panel" role="status">
                  Verifying the audit chain…
                </div>
              ) : (
                history && (
                  <>
                    <div className="history-summary panel">
                      <ShieldCheck />
                      <div>
                        <strong>
                          {history.event_count} audit events verified
                        </strong>
                        <p>
                          Snapshot replay does not re-execute queries or
                          actions. Local hashes do not authenticate reviewers.
                        </p>
                      </div>
                    </div>
                    <section className="panel">
                      <h2>Saved proposals</h2>
                      {!history.actions.length ? (
                        <p className="muted">
                          No proposals yet. Complete an analysis and save a
                          selected recommendation to begin.
                        </p>
                      ) : (
                        history.actions.map((a) => (
                          <article className="history-item" key={a.id}>
                            <div>
                              <span
                                className={`tag ${a.state === "pending" ? "amber-tag" : ""}`}
                              >
                                {a.state.replaceAll("_", " ")}
                              </span>
                              <h3>{a.text}</h3>
                              <p>
                                {a.reviewer} · revision {a.revision} · simulated
                                decision
                              </p>
                            </div>
                            <button
                              onClick={() => openSnapshot(a.analysis_id, a)}
                            >
                              Open review <ArrowRight size={16} />
                            </button>
                          </article>
                        ))
                      )}
                    </section>
                    <section className="panel">
                      <h2>Analysis snapshots</h2>
                      {history.events
                        .filter((e) => e.event_type === "analysis_saved")
                        .reverse()
                        .map((e) => (
                          <div className="event-row" key={e.event_id}>
                            <span>
                              <FileSearch size={17} />
                              {new Date(e.timestamp).toLocaleString()}
                            </span>
                            <button
                              onClick={() =>
                                openSnapshot(String(e.payload.analysis_id))
                              }
                            >
                              Replay snapshot
                            </button>
                          </div>
                        ))}
                      {history.events
                        .filter((e) => e.event_type === "legacy_imported")
                        .map((e) => (
                          <details key={e.event_id}>
                            <summary>
                              Preserved legacy audit ·{" "}
                              {String(e.payload.event_count)} events
                            </summary>
                            <Legacy identity={String(e.payload.snapshot_id)} />
                          </details>
                        ))}
                    </section>
                    <details className="panel">
                      <summary>Verified audit details</summary>
                      <p className="hash">Chain head: {history.head_hash}</p>
                      {history.events.map((e) => (
                        <details key={e.event_id}>
                          <summary>
                            {e.event_type} · {e.actor} ·{" "}
                            {new Date(e.timestamp).toLocaleString()}
                          </summary>
                          <pre>{JSON.stringify(e, null, 2)}</pre>
                        </details>
                      ))}
                    </details>
                  </>
                )
              )}
            </>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">
                    {String(tasks.indexOf(task) + 1).padStart(2, "0")} /
                    SUPPLY-CHAIN ANALYSIS
                  </div>
                  <h1>{task.title}</h1>
                  <p>{task.description}</p>
                </div>
                <div className="heading-icon">
                  <task.icon size={30} />
                </div>
              </div>
              <div className="analysis-grid">
                <section className="panel task-panel">
                  <div className="section-title">
                    <SlidersHorizontal size={18} />
                    <h2>Define the analysis</h2>
                  </div>
                  <p className="muted">
                    Use supported fields to keep every constraint explicit.
                  </p>
                  <form onSubmit={run}>
                    <fieldset disabled={!catalog || loading}>
                      <legend className="sr-only">{task.title} inputs</legend>
                      {kind === "supplier_delay" && (
                        <>
                          {select("supplier_id", "Supplier", true)}
                          <div className="field-pair">
                            <label>
                              From date
                              <input
                                type="date"
                                required
                                max={catalog?.reference_date}
                                value={form.start_date ?? ""}
                                onChange={(e) =>
                                  update({ start_date: e.target.value })
                                }
                              />
                            </label>
                            <label>
                              Through date
                              <input
                                type="date"
                                required
                                min={form.start_date}
                                max={catalog?.reference_date}
                                value={form.end_date ?? ""}
                                onChange={(e) =>
                                  update({ end_date: e.target.value })
                                }
                              />
                            </label>
                          </div>
                          <p className="fine">
                            Both dates are included. Rates use promised delivery
                            dates and evaluable observations.
                          </p>
                        </>
                      )}
                      {kind === "stockout" && (
                        <>
                          <label>
                            Coverage threshold (days)
                            <input
                              type="number"
                              min="1"
                              max="90"
                              step="1"
                              required
                              value={form.horizon_days ?? ""}
                              onChange={(e) =>
                                update({
                                  horizon_days: e.target.value
                                    ? Number(e.target.value)
                                    : undefined,
                                })
                              }
                            />
                          </label>
                          <p className="fine">
                            All products and warehouses. Coverage below this
                            threshold triggers review; it is not a guaranteed
                            stockout.
                          </p>
                        </>
                      )}
                      {kind === "forecast" && (
                        <>
                          {select("product_id", "Product")}
                          {select("warehouse_id", "Warehouse")}
                          <div className="fixed-value">
                            <span>Forecast horizon</span>
                            <strong>14 days</strong>
                          </div>
                        </>
                      )}
                      {kind === "supplier_risk" &&
                        select("supplier_id", "Supplier")}
                      {kind === "what_if" && (
                        <>
                          {select("warehouse_id", "Warehouse")}
                          <label>
                            Demand increase (%)
                            <input
                              type="number"
                              min="0"
                              max="200"
                              step="0.1"
                              required
                              value={form.increase_percent ?? ""}
                              onChange={(e) =>
                                update({
                                  increase_percent: e.target.value
                                    ? Number(e.target.value)
                                    : undefined,
                                })
                              }
                            />
                          </label>
                          <p className="fine">
                            An increase <strong>by</strong> this percentage, not
                            an increase to it. All products at the selected
                            warehouse.
                          </p>
                        </>
                      )}
                      <div className="interpretation">
                        <span>INTERPRETATION PREVIEW</span>
                        <p>{interpretation(form)}</p>
                        <small>Change any field above before running.</small>
                      </div>
                      <button className="primary run-button" type="submit">
                        {loading ? (
                          "Analyzing verified data…"
                        ) : (
                          <>
                            Run analysis <ArrowRight size={17} />
                          </>
                        )}
                      </button>
                    </fieldset>
                  </form>
                  <div className="trust-note">
                    <ShieldCheck size={17} />
                    <p>
                      Validated fields. Read-only analysis.
                      <br />
                      Human review before every simulated decision.
                    </p>
                  </div>
                </section>
                <section
                  className={`panel result-panel state-${status}`}
                  aria-busy={loading}
                >
                  <div className="section-title">
                    <Activity size={18} />
                    <h2 ref={resultHeading} tabIndex={-1}>
                      Analysis result
                    </h2>
                    <span className="tag">
                      {replay
                        ? "Verified snapshot"
                        : status === "initial"
                          ? "Ready"
                          : status}
                    </span>
                  </div>
                  <div
                    role="status"
                    aria-live="polite"
                    className={
                      status === "initial" ? "sr-only" : "status-message"
                    }
                  >
                    {loading
                      ? "Checking the request, running read-only analysis and saving evidence…"
                      : error
                        ? "Analysis unavailable. Your task inputs are preserved."
                        : (result?.message ?? "Ready to analyze.")}
                  </div>
                  {loading ? (
                    <div className="loading-state">
                      <div className="spinner" />
                      <h3>Working with the source evidence</h3>
                      <p>
                        Validating your task, computing results and preserving
                        the snapshot.
                      </p>
                    </div>
                  ) : error ? (
                    <>
                      <Alert>{error}</Alert>
                      <p className="muted">
                        Review the inputs or service status, then use Run
                        analysis to retry.
                      </p>
                    </>
                  ) : !result ? (
                    <div className="empty-panel">
                      <div className="empty-icon">
                        <task.icon size={32} />
                      </div>
                      <h3>A clear path from question to decision</h3>
                      <p>
                        Set the analysis fields, inspect the interpretation,
                        then run against the synthetic dataset.
                      </p>
                      <ol className="process-list">
                        <li>
                          <span>1</span>Analyze read-only data
                        </li>
                        <li>
                          <span>2</span>Inspect evidence and limitations
                        </li>
                        <li>
                          <span>3</span>Review a simulated decision
                        </li>
                      </ol>
                      <div className="empty-footer">
                        <Database size={16} /> Your results will appear here. No
                        analysis has run yet.
                      </div>
                    </div>
                  ) : (
                    <>
                      {replay && (
                        <p className="snapshot-note">
                          Saved snapshot · {result.reference_date} dataset
                          reference. Replaying does not run the analysis again.
                        </p>
                      )}
                      {result.state !== "success" && (
                        <div className="recovery">
                          <h3>
                            {result.state === "empty"
                              ? "No matching observations"
                              : result.state === "uncertain"
                                ? "Review the available information"
                                : "Analysis could not complete"}
                          </h3>
                          <p>
                            {result.state === "empty"
                              ? "The query completed without matching rows. Adjust the threshold or date range; no recommendation was invented."
                              : "No operational recommendation was inferred. Inspect available evidence, correct the data or fields, then run again."}
                          </p>
                        </div>
                      )}
                      {result.state === "success" && (
                        <>
                          <div className="result-meta">
                            <div>
                              <small>Evidence rows</small>
                              <strong>{result.row_count}</strong>
                            </div>
                            <div>
                              <small>Reference date</small>
                              <strong className="date-stat">
                                {result.reference_date}
                              </strong>
                            </div>
                            <div>
                              <small>Decision mode</small>
                              <strong className="date-stat">
                                Human review
                              </strong>
                            </div>
                          </div>
                          <p className="actual-interpretation">
                            {result.interpretation}
                          </p>
                          {result.forecast ? (
                            <>
                              <ForecastChart forecast={result.forecast} />
                              <p className="fine">
                                {result.forecast.history_days} daily historical
                                observations · point estimates only.
                              </p>
                            </>
                          ) : (
                            <>
                              {kind === "what_if" && (
                                <ScenarioChart rows={result.rows} />
                              )}
                              <Table
                                rows={result.rows}
                                columns={columns[kind]}
                                caption={task.title + " results"}
                              />
                            </>
                          )}
                        </>
                      )}
                      {result.evidence_id && (
                        <button
                          className="evidence-button"
                          onClick={() => setEvidence(result.evidence_id)}
                        >
                          <FileSearch size={18} /> Inspect source evidence{" "}
                          <ArrowRight size={16} />
                        </button>
                      )}
                      <div className="limitations">
                        <h3>Context & limitations</h3>
                        <ul>
                          {result.warnings.map((w) => (
                            <li key={w}>{w}</li>
                          ))}
                        </ul>
                      </div>
                      {result.recommendations.length > 0 && (
                        <Review
                          key={result.analysis_id + (existing?.id ?? "")}
                          analysis={result}
                          existing={existing}
                          onSaved={() => {}}
                        />
                      )}
                    </>
                  )}
                </section>
              </div>
            </>
          )}
          <footer className="footer">
            <span>Sentinel / SIGSEGV</span>
            <span>
              Synthetic observations. Explicit assumptions. Human-reviewed
              simulations.
            </span>
          </footer>
        </main>
      </div>
      {evidence && (
        <EvidenceDialog id={evidence} onClose={() => setEvidence(null)} />
      )}
    </div>
  );
}
function Legacy({ identity }: { identity: string }) {
  const [events, setEvents] = useState<unknown>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api("/legacy/" + identity)
      .then(setEvents)
      .catch((e) => setError(e.message));
  }, [identity]);
  return error ? (
    <Alert>{error}</Alert>
  ) : (
    <pre>
      {events
        ? JSON.stringify(events, null, 2)
        : "Loading original verified events…"}
    </pre>
  );
}
