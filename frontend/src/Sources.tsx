import { useEffect, useState } from "react";
import { api } from "./api";
export type Target = {
  id: string;
  label: string;
  assessed: boolean;
  matching: boolean;
  reasons: string[];
  missing_observations: number;
  evaluable?: number;
  late?: number;
  excluded?: number;
  unassignable?: number;
};
type SourcePage = {
  available: boolean;
  message: string;
  source_id: string | null;
  target: Target | null;
  period: { start: string; end: string } | null;
  reference_date: string;
  total: number;
  offset: number;
  records: {
    id: string;
    kind: string;
    membership: string;
    disposition: string;
    contribution: string | null;
    reasons: string[];
    record: Record<string, string | number | null>;
  }[];
};
export function Sources({
  evidenceId,
  targets,
}: {
  evidenceId: string;
  targets: Target[];
}) {
  const [target, setTarget] = useState(targets[0]?.id ?? ""),
    [kind, setKind] = useState(
      target.startsWith("supplier:") ? "shipment" : "demand",
    ),
    [offset, setOffset] = useState(0),
    [page, setPage] = useState<SourcePage | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!target) return;
    const abort = new AbortController();
    setPage(null);
    setError("");
    api<SourcePage>(
      `/evidence/${evidenceId}/targets/${encodeURIComponent(target)}?kind=${kind}&offset=${offset}&limit=10`,
      undefined,
      abort.signal,
    )
      .then(setPage)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => abort.abort();
  }, [evidenceId, target, kind, offset]);
  if (!targets.length)
    return (
      <p>
        Transaction snapshots are unavailable for this historical evidence
        version.
      </p>
    );
  return (
    <section aria-label="Transaction investigation">
      <h3>Investigate contributing records</h3>
      <label>
        Evidence target
        <select
          value={target}
          onChange={(e) => {
            setTarget(e.target.value);
            setKind(
              e.target.value.startsWith("supplier:") ? "shipment" : "demand",
            );
            setOffset(0);
          }}
        >
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
              {!t.assessed ? " · unevaluable" : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        Record type
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setOffset(0);
          }}
        >
          {(target.startsWith("supplier:")
            ? ["shipment", "supplier", "supplier_event"]
            : ["demand", "inventory"]
          ).map((k) => (
            <option key={k} value={k}>
              {k.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {!page && !error && (
        <p role="status">Verifying captured transaction records…</p>
      )}
      {page && (
        <>
          <p>{page.message}</p>
          <p>
            Reference date: {page.reference_date}
            {page.period
              ? ` · promised dates from ${page.period.start} up to ${page.period.end} (end excluded)`
              : ""}
          </p>
          {page.target?.evaluable !== undefined && (
            <p>
              {page.target.late} late / {page.target.evaluable} evaluable ·{" "}
              {page.target.excluded} excluded · {page.target.unassignable}{" "}
              unassignable dates
            </p>
          )}
          {page.target?.reasons.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Captured transaction records"
          >
            <table>
              <caption className="sr-only">
                Captured transaction records
              </caption>
              <thead>
                <tr>
                  {[
                    "Source record",
                    "Entities",
                    "Observed dates",
                    "Contribution",
                    "Quality / values",
                  ].map((x) => (
                    <th key={x} scope="col">
                      {x}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {page.records.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>
                      {["supplier_id", "product_id", "warehouse_id"]
                        .map((k) => r.record[k])
                        .filter(Boolean)
                        .join(" / ")}
                    </td>
                    <td>
                      {[
                        "promised_date",
                        "delivered_date",
                        "demand_date",
                        "inventory_date",
                        "event_date",
                      ]
                        .filter((k) => k in r.record)
                        .map((k) => (
                          <div key={k}>
                            {k.replaceAll("_", " ")}: {r.record[k] ?? "missing"}
                          </div>
                        ))}
                    </td>
                    <td>
                      {r.membership === "unassignable"
                        ? "Period unknown"
                        : r.disposition}
                      {r.contribution
                        ? " · " + r.contribution.replaceAll("_", " ")
                        : ""}
                    </td>
                    <td>
                      {r.reasons.length ? r.reasons.join("; ") : "Valid record"}
                      <details>
                        <summary>Inspect recorded fields</summary>
                        <pre>{JSON.stringify(r.record, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!page.records.length && (
            <p>No captured records of this type for the selected target.</p>
          )}
          <div className="pagination">
            <button
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 10))}
            >
              Previous source page
            </button>
            <span aria-live="polite">
              Source records {page.total ? offset + 1 : 0}–
              {Math.min(offset + 10, page.total)} of {page.total}
            </span>
            <button
              disabled={offset + 10 >= page.total}
              onClick={() => setOffset(offset + 10)}
            >
              Next source page
            </button>
          </div>
        </>
      )}
    </section>
  );
}
