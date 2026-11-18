import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { PublishedContext } from "./Chat";

type Metric = {
  n: number;
  mae: number;
  rmse: number;
  interval_coverage: number;
};
type ScoreMetric = {
  n: number;
  roc_auc: number;
  average_precision: number;
  brier: number;
  positive_rate: number;
};
type Forecast = {
  scope: string;
  origin: string;
  method: string;
  units: string;
  history_days: number;
  excluded_series: number;
  source_rows: number[];
  points: {
    date: string;
    point: number;
    lower: number;
    upper: number;
    baseline: number;
  }[];
  evaluation: Record<string, Metric>;
};
type Coverage = {
  scope: string;
  origin: string;
  opening_inventory: number;
  observed_daily_sales: number;
  days_of_cover: number | null;
  scenario_days_of_cover: number | null;
  increase_percent: number;
  source_rows: number[];
};
type Lateness = {
  scope: string;
  origin: string;
  assessed: number;
  late: number;
  late_rate: number;
  source_wide_rejections: number;
  examples: {
    source_row: number;
    record_id: number;
    scheduled: string;
    delivered: string;
  }[];
};
type Shipment = {
  record_id: number;
  source_row: number;
  prediction_time: string;
  score: number;
  observed_late: boolean;
  inputs: Record<string, string | number>;
  evaluation: Record<string, ScoreMetric>;
};
type Catalog = {
  entities: [string, string][];
  vendors: string[];
  sources: {
    retail: { source: string; date_range: string[] };
    shipment: {
      source: string;
      date_range: string[];
      test_example_ids: number[];
    };
  };
};

const tasks = {
  forecast: "Demand forecast",
  coverage: "Coverage and demand what-if",
  lateness: "Observed shipment lateness",
  shipment: "Shipment model evaluation",
};
const format = (value: number | null) =>
  value === null ? "Unavailable" : value.toFixed(2);
type Result =
  | { kind: "forecast"; data: Forecast }
  | { kind: "coverage"; data: Coverage }
  | { kind: "lateness"; data: Lateness }
  | { kind: "shipment"; data: Shipment };

export function PublishedData({
  onContext,
}: {
  onContext: (context?: PublishedContext) => void;
}) {
  const analysisRequest = useRef<AbortController | null>(null);
  const [catalog, setCatalog] = useState<Catalog>();
  const [catalogError, setCatalogError] = useState("");
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [kind, setKind] = useState<keyof typeof tasks>("forecast");
  const [entity, setEntity] = useState(0);
  const [vendor, setVendor] = useState("");
  const [record, setRecord] = useState("");
  const [increase, setIncrease] = useState("20");
  const [result, setResult] = useState<Result>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api<Catalog>("/published/catalog", undefined, controller.signal)
      .then(setCatalog)
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setCatalogError(failure.message);
      });
    return () => controller.abort();
  }, [catalogAttempt]);

  useEffect(() => () => analysisRequest.current?.abort(), []);

  function clear() {
    setResult(undefined);
    setError("");
    onContext();
  }

  async function run(event: React.FormEvent) {
    event.preventDefault();
    if (!catalog || analysisRequest.current) return;
    const controller = new AbortController();
    analysisRequest.current = controller;
    clear();
    setPending(true);
    const [store, product] = catalog.entities[entity];
    const retail = new URLSearchParams({ store, product });
    try {
      if (kind === "forecast") {
        const data = await api<Forecast>(
          "/published/forecast?" + retail,
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setResult({ kind, data });
        onContext({ kind, store, product });
      } else if (kind === "coverage") {
        const change = Number(increase);
        retail.set("increase", String(change));
        const data = await api<Coverage>(
          "/published/coverage?" + retail,
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setResult({ kind, data });
        onContext({ kind, store, product, increase: change });
      } else if (kind === "lateness") {
        const data = await api<Lateness>(
          "/published/lateness?vendor=" + encodeURIComponent(vendor),
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setResult({ kind, data });
        onContext({ kind, vendor });
      } else {
        const record_id = Number(
          record || catalog.sources.shipment.test_example_ids[0],
        );
        const data = await api<Shipment>(
          "/published/shipment-score?record_id=" + record_id,
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setResult({ kind, data });
        onContext({ kind, record_id });
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError((failure as Error).message);
    } finally {
      analysisRequest.current = null;
      if (!controller.signal.aborted) setPending(false);
    }
  }

  return (
    <section className="published-page">
      <h1>Published data</h1>
      <p>Historical analysis of independent Kaggle sources.</p>
      {catalogError ? (
        <p className="phase4-error" role="alert">
          {catalogError}{" "}
          <button
            onClick={() => {
              setCatalogError("");
              setCatalogAttempt((n) => n + 1);
            }}
          >
            Retry loading sources
          </button>
        </p>
      ) : !catalog ? (
        <p role="status">Loading sources…</p>
      ) : (
        <>
          <form className="panel published-form" onSubmit={run}>
            <fieldset disabled={pending}>
              <label>
                Task{" "}
                <select
                  aria-label="Task"
                  value={kind}
                  onChange={(e) => {
                    clear();
                    setKind(e.target.value as keyof typeof tasks);
                  }}
                >
                  {Object.entries(tasks).map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              {kind === "forecast" || kind === "coverage" ? (
                <label>
                  Store and product
                  <select
                    value={entity}
                    onChange={(e) => {
                      clear();
                      setEntity(Number(e.target.value));
                    }}
                  >
                    {catalog.entities.map(([store, product], index) => (
                      <option key={index} value={index}>
                        {store} / {product}
                      </option>
                    ))}
                  </select>
                </label>
              ) : kind === "lateness" ? (
                <label>
                  Vendor
                  <select
                    value={vendor}
                    onChange={(e) => {
                      clear();
                      setVendor(e.target.value);
                    }}
                  >
                    <option value="">All vendors</option>
                    {catalog.vendors.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  Held-out SCMS line item
                  <select
                    value={
                      record || catalog.sources.shipment.test_example_ids[0]
                    }
                    onChange={(e) => {
                      clear();
                      setRecord(e.target.value);
                    }}
                  >
                    {catalog.sources.shipment.test_example_ids.map((id) => (
                      <option key={id}>{id}</option>
                    ))}
                  </select>
                </label>
              )}
              {kind === "coverage" && (
                <label>
                  Sales increase (%){" "}
                  <input
                    type="number"
                    min={0}
                    max={200}
                    step="any"
                    required
                    value={increase}
                    onChange={(e) => {
                      clear();
                      setIncrease(e.target.value);
                    }}
                  />
                </label>
              )}
            </fieldset>
            <button className="primary" aria-disabled={pending}>
              {error ? "Retry published analysis" : "Run published analysis"}
            </button>
          </form>
          <p role="status">
            {pending ? "Running analysis…" : result ? "Analysis ready." : ""}
          </p>
          {error && (
            <p className="phase4-error" role="alert">
              {error}
            </p>
          )}
          {result && (
            <section className="panel" aria-label="Published result">
              <h2>{tasks[result.kind]}</h2>
              <PublishedResult result={result} />
              <p>
                <a
                  href={
                    (result.kind === "forecast" || result.kind === "coverage"
                      ? catalog.sources.retail
                      : catalog.sources.shipment
                    ).source
                  }
                >
                  {result.kind === "forecast" || result.kind === "coverage"
                    ? "Kaggle synthetic retail source"
                    : "Kaggle SCMS shipment source"}
                </a>
              </p>
            </section>
          )}
        </>
      )}
    </section>
  );
}

function PublishedResult({ result }: { result: Result }) {
  if (result.kind === "forecast") {
    const { data } = result;
    return (
      <>
        <p>
          {data.scope} · historical origin {data.origin} · {data.points.length}{" "}
          days · {data.units}
        </p>
        <p>
          {data.method === "seasonal_naive"
            ? "Seasonal naive"
            : "Quantile histogram gradient boosting"}{" "}
          · {data.history_days} daily observations · {data.excluded_series}{" "}
          excluded series
        </p>
        <p>
          Quantile ranges are empirical. Recorded sales can understate demand.
          Source CSV records {data.source_rows.join(" through ")} bound the
          series.
        </p>
        <div className="table-wrap">
          <table>
            <caption>Daily forecasts and seasonal comparison</caption>
            <thead>
              <tr>
                {["Date", "Estimate", "Lower", "Upper", "Seasonal naive"].map(
                  (item) => (
                    <th scope="col" key={item}>
                      {item}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.points.map((point) => (
                <tr key={point.date}>
                  <th scope="row">{point.date}</th>
                  <td>{format(point.point)}</td>
                  <td>{format(point.lower)}</td>
                  <td>{format(point.upper)}</td>
                  <td>{format(point.baseline)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Untouched final evaluation</h3>
        <ul>
          {Object.entries(data.evaluation).map(([name, metric]) => (
            <li key={name}>
              {name.replaceAll("_", " ")} · n={metric.n} · MAE{" "}
              {format(metric.mae)} · RMSE {format(metric.rmse)} · interval
              coverage {(metric.interval_coverage * 100).toFixed(1)}%
            </li>
          ))}
        </ul>
      </>
    );
  }
  if (result.kind === "coverage") {
    const { data } = result;
    return (
      <>
        <p>
          {data.scope} · opening stock on {data.origin}
        </p>
        <p>
          <strong>
            {format(data.days_of_cover)} days of observed coverage ·{" "}
            {format(data.scenario_days_of_cover)} days at +
            {data.increase_percent}% sales
          </strong>
        </p>
        <p>
          {data.opening_inventory} opening units divided by{" "}
          {format(data.observed_daily_sales)} mean units sold on the preceding
          28 days. The scenario assumes fixed stock and no replenishment.
        </p>
        <p>
          The data do not establish a future stockout label. Opening stock CSV
          record {data.source_rows.at(-1)}.
        </p>
      </>
    );
  }
  if (result.kind === "lateness") {
    const { data } = result;
    return (
      <>
        <p>
          {data.scope} · historical delivery records through {data.origin}
        </p>
        <p>
          <strong>
            {data.late} of {data.assessed} assessed line items were late (
            {(data.late_rate * 100).toFixed(1)}%).
          </strong>
        </p>
        <p>
          {data.source_wide_rejections} source-wide rejected records. Vendor
          rates describe observed performance.
        </p>
        <ul>
          {data.examples.map((item) => (
            <li key={item.source_row}>
              SCMS {item.record_id} · CSV record {item.source_row} · due{" "}
              {item.scheduled} · delivered {item.delivered}
            </li>
          ))}
        </ul>
      </>
    );
  }
  const { data } = result;
  return (
    <>
      <p>
        <strong>Research only · no operational threshold</strong>
      </p>
      <p>
        Sigmoid-calibrated logistic regression scored held-out line item{" "}
        {data.record_id} at {(data.score * 100).toFixed(1)}% as of PO send date{" "}
        {data.prediction_time}. Observed result was{" "}
        {data.observed_late ? "late" : "on time"}.
      </p>
      <p>
        CSV record {data.source_row}. The model used{" "}
        {Object.entries(data.inputs)
          .map(([key, value]) => key.replaceAll("_", " ") + " " + value)
          .join(", ")}
        .
      </p>
      <ul>
        {Object.entries(data.evaluation).map(([name, metric]) => (
          <li key={name}>
            {name} · n={metric.n} · ROC AUC {metric.roc_auc.toFixed(3)} ·
            average precision {metric.average_precision.toFixed(3)} · Brier{" "}
            {metric.brier.toFixed(3)}
          </li>
        ))}
      </ul>
      <p>
        Calibration underperformed the training-prevalence baseline on Brier
        score. Supplier prediction remains unavailable.
      </p>
    </>
  );
}
