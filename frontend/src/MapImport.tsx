import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { MapContext, WorkspaceAction } from "./Chat";
import { SupplyChainMap } from "./SupplyChainMap";

type SavedMap = { id: string; name: string };
type Upload = { filename: string; data: string; worksheet?: string };
type Preview = {
  chat_preview_id?: string;
  worksheets: string[];
  worksheet?: string;
  digest?: string;
  accepted?: number;
  rejected?: { row: number; reason: string }[];
  examples?: {
    source_row: number;
    source_id: string;
    relationship: string;
    target_id: string;
  }[];
  graph?: { nodes: unknown[]; edges: unknown[] };
};

const header =
  "source_type,source_id,relationship,target_type,target_id,source_label,target_label,record_id,event_date,quantity,notes";
const example =
  "supplier,S001,supplies,warehouse,W001,Example supplier,Example warehouse,REL001,2025-01-01,24,Example row";
const download = (content: string) =>
  "data:text/csv;charset=utf-8," + encodeURIComponent(content + "\n");

function requestedMap(command?: WorkspaceAction) {
  if (command?.kind === "select_map") return command.identity ?? "";
  if (command?.kind === "inspect_map") return command.record?.map_id ?? "";
  return undefined;
}

export function MapWorkspace({
  datasetFingerprint,
  onSource,
  onMapChange,
  onPreview,
  command,
}: {
  datasetFingerprint: string;
  onSource: (record?: MapContext) => void;
  onMapChange: (identity: string) => void;
  onPreview: (token?: string) => void;
  command?: WorkspaceAction;
}) {
  const selector = useRef<HTMLSelectElement>(null);
  const [maps, setMaps] = useState<SavedMap[]>([]);
  const [active, setActive] = useState(() => requestedMap(command) ?? "");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<SavedMap[]>("/imported-maps", undefined, controller.signal)
      .then(setMaps)
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const map = requestedMap(command);
    if (map !== undefined) setActive(map);
  }, [command]);
  useEffect(() => {
    onMapChange(active);
  }, [active, onMapChange]);

  return (
    <>
      <div className="map-source-controls">
        <label>
          Map{" "}
          <select
            ref={selector}
            aria-label="Map"
            value={active}
            onChange={(event) => {
              onSource();
              setActive(event.target.value);
            }}
          >
            <option value="">Synthetic supply chain</option>
            {maps.map(({ id, name }) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {error && (
          <p role="alert" className="phase4-error">
            {error}
          </p>
        )}
        <MapImport
          command={command}
          onPreview={onPreview}
          onSaved={(saved) => {
            setMaps((current) =>
              [...current.filter(({ id }) => id !== saved.id), saved].sort(
                (a, b) => a.name.localeCompare(b.name),
              ),
            );
            onSource();
            setActive(saved.id);
            selector.current?.focus();
          }}
        />
      </div>
      <SupplyChainMap
        key={active || "synthetic"}
        datasetFingerprint={active || datasetFingerprint}
        importedName={maps.find(({ id }) => id === active)?.name}
        endpoint={active ? "/imported-maps/" + active : "/map"}
        onSource={onSource}
        command={
          requestedMap(command) === undefined ||
          requestedMap(command) === active
            ? command
            : undefined
        }
      />
    </>
  );
}

function MapImport({
  onSaved,
  command,
  onPreview,
}: {
  onSaved: (saved: SavedMap) => void;
  command?: WorkspaceAction;
  onPreview: (token?: string) => void;
}) {
  const importButton = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(command?.kind === "open_import");
  const [file, setFile] = useState<File>();
  const [upload, setUpload] = useState<Upload>();
  const [worksheet, setWorksheet] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (command?.kind === "open_import") {
      setOpen(true);
      importButton.current?.focus();
    }
  }, [command]);
  useEffect(() => {
    onPreview(preview?.chat_preview_id);
  }, [preview?.chat_preview_id, onPreview]);

  function clear() {
    setOpen(false);
    setFile(undefined);
    setUpload(undefined);
    setWorksheet("");
    setPreview(undefined);
    setName("");
    setError("");
  }

  async function validate(event: React.FormEvent) {
    event.preventDefault();
    if (!file || pending) return;
    setPending(true);
    setError("");
    try {
      if (file.size > 96 * 1024)
        throw new Error("Files must be at most 96 KiB.");
      let encoded = upload?.data;
      if (!encoded) {
        const bytes = new Uint8Array(await file.arrayBuffer());
        encoded = btoa(
          Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""),
        );
      }
      const current = {
        filename: file.name,
        data: encoded,
        worksheet: worksheet || undefined,
      };
      const result = await api<Preview>("/imported-maps/preview", current);
      setUpload({
        ...current,
        worksheet: result.worksheet ?? current.worksheet,
      });
      setPreview(result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function confirm() {
    if (!upload || !preview?.digest || !name.trim() || pending) return;
    setPending(true);
    setError("");
    try {
      const saved = await api<SavedMap>("/imported-maps/confirm", {
        ...upload,
        name: name.trim(),
        digest: preview.digest,
      });
      clear();
      onSaved(saved);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        ref={importButton}
        aria-expanded={open}
        onClick={() => {
          if (!pending) {
            if (open) clear();
            else setOpen(true);
          }
        }}
        aria-disabled={pending}
      >
        Import relationships
      </button>
      {open && (
        <section className="panel map-import" aria-label="Import relationships">
          <p>
            Upload up to 40 relationship rows in a UTF-8 CSV or XLSX. The map
            uses valid rows from this source.
          </p>
          <p>
            <a
              download="sentinel-relationship-template.csv"
              href={download(header)}
            >
              Download blank template
            </a>
            {" · "}
            <a
              download="sentinel-relationship-example.csv"
              href={download(header + "\n" + example)}
            >
              Download example
            </a>
          </p>
          <details>
            <summary>Supported types and limits</summary>
            <p>
              Node types include supplier, warehouse, shipment, product, order,
              order_item, store, facility and customer.
            </p>
            <p>
              Relationships include supplies, ships_to, contains, fulfills,
              stocks, transfers_to, delivers_to and sourced_from. Formula cells
              are rejected. The file limit is 96 KiB.
            </p>
          </details>
          <form onSubmit={validate}>
            <label>
              Relationship file{" "}
              <input
                type="file"
                accept=".csv,.xlsx"
                required
                disabled={pending}
                onChange={(event) => {
                  const next = event.target.files?.[0];
                  setFile(next);
                  setName(
                    next?.name.replace(/\.[^.]+$/, "").slice(0, 64) ?? "",
                  );
                  setUpload(undefined);
                  setWorksheet("");
                  setPreview(undefined);
                  setError("");
                }}
              />
            </label>
            {!!preview?.worksheets.length && (
              <label>
                Worksheet{" "}
                <select
                  value={worksheet || preview.worksheet || ""}
                  disabled={pending}
                  onChange={(event) => {
                    setWorksheet(event.target.value);
                    setPreview({ worksheets: preview.worksheets });
                  }}
                >
                  <option value="">Select a worksheet</option>
                  {preview.worksheets.map((sheet) => (
                    <option key={sheet}>{sheet}</option>
                  ))}
                </select>
              </label>
            )}
            <button
              type="submit"
              disabled={
                pending ||
                !file ||
                (!!preview?.worksheets.length &&
                  !worksheet &&
                  !preview.worksheet)
              }
            >
              Validate and preview
            </button>
          </form>
          <p role="status">{pending ? "Checking map import…" : ""}</p>
          {preview?.graph && (
            <>
              <p>
                <strong>
                  {preview.accepted} accepted · {preview.rejected?.length}{" "}
                  rejected · {preview.graph.nodes.length} nodes ·{" "}
                  {preview.graph.edges.length} relationships
                </strong>
              </p>
              {!!preview.examples?.length && (
                <ul>
                  {preview.examples.map((row) => (
                    <li key={row.source_row}>
                      Row {row.source_row} · {row.source_id} {row.relationship}{" "}
                      {row.target_id}
                    </li>
                  ))}
                </ul>
              )}
              {!!preview.rejected?.length && (
                <ul>
                  {preview.rejected.map(({ row, reason }) => (
                    <li key={row}>
                      Row {row} · {reason}
                    </li>
                  ))}
                </ul>
              )}
              <label>
                Map name{" "}
                <input
                  value={name}
                  maxLength={64}
                  disabled={pending}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="primary"
                disabled={pending || !preview.accepted || !name.trim()}
                onClick={() => void confirm()}
              >
                Create map
              </button>
            </>
          )}
          {error && (
            <p role="alert" className="phase4-error">
              {error}
            </p>
          )}
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              clear();
              importButton.current?.focus();
            }}
          >
            Cancel import
          </button>
        </section>
      )}
    </div>
  );
}
