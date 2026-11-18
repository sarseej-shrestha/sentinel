import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { MapContext } from "./Chat";

type MapNode = {
  id: string;
  kind: string;
  record_id: string;
  label: string;
  status: string;
  source_record_id: string | null;
};
type MapEdge = {
  id: string;
  from: string;
  to: string;
  label: string;
  evidence_kind?: string;
  evidence_id?: string;
  source_record_id?: string;
  type: "recorded" | "exploratory";
};
type Graph = { nodes: MapNode[]; edges: MapEdge[]; warnings: string[] };
type Point = { x: number; y: number };
type Selected = { type: "node" | "edge"; id: string };
const initial: Record<string, Point> = {
  "supplier:S2": { x: 90, y: 110 },
  "shipment:SH367": { x: 390, y: 250 },
  "warehouse:W2": { x: 730, y: 100 },
  "order:O367": { x: 730, y: 390 },
  "order_item:OI367": { x: 1050, y: 390 },
};
const WIDTH = 210;
const HEIGHT = 94;

export function SupplyChainMap({
  datasetFingerprint,
  onSource,
  endpoint,
  importedName,
}: {
  datasetFingerprint: string;
  onSource: (record?: MapContext) => void;
  endpoint: string;
  importedName?: string;
}) {
  const storageKey = "sentinel-map-v1:" + datasetFingerprint;
  const stage = useRef<HTMLDivElement>(null);
  const inspected = useRef(0);
  const drag = useRef<{
    id?: string;
    x: number;
    y: number;
    pan?: boolean;
  } | null>(null);
  const [nodes, setNodes] = useState<MapNode[]>([]);
  const [edges, setEdges] = useState<MapEdge[]>([]);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(0.82);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<MapNode[]>([]);
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [connectTarget, setConnectTarget] = useState("");
  const [wiring, setWiring] = useState<{
    from: string;
    x: number;
    y: number;
  } | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [ready, setReady] = useState(false);

  const merge = useCallback(
    (graph: Graph) => {
      setNodes((current) => {
        const byId = new Map(current.map((node) => [node.id, node]));
        for (const node of graph.nodes) byId.set(node.id, node);
        return [...byId.values()];
      });
      setEdges((current) => {
        const byId = new Map(current.map((edge) => [edge.id, edge]));
        for (const edge of graph.edges) byId.set(edge.id, edge);
        return [...byId.values()];
      });
      setPositions((current) => {
        const next = { ...current };
        for (const node of graph.nodes) {
          if (!next[node.id])
            next[node.id] = importedName
              ? {
                  x: 32 + (Object.keys(next).length % 4) * 270,
                  y: 36 + Math.floor(Object.keys(next).length / 4) * 142,
                }
              : (initial[node.id] ?? {
                  x: 180 + Object.keys(next).length * 72,
                  y: 130 + (Object.keys(next).length % 4) * 135,
                });
        }
        return next;
      });
      if (graph.warnings.length) setWarning(graph.warnings.join(" "));
    },
    [importedName],
  );
  useEffect(() => {
    let active = true;
    setLoading(true);
    api<Graph>(endpoint + "/seed")
      .then((graph) => {
        if (!active) return;
        merge(graph);
        try {
          const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? "{}");
          if (saved.positions)
            setPositions((current) => ({ ...current, ...saved.positions }));
          if (saved.exploratory)
            setEdges((current) => [...current, ...saved.exploratory]);
        } catch {
          /* A damaged local layout must not hide operational records. */
        }
        setReady(true);
      })
      .catch((error) => setWarning(error.message))
      .finally(() => setLoading(false));
    return () => {
      active = false;
    };
    // The seed is bound to the current dataset version.
  }, [endpoint, merge, storageKey]);
  useEffect(() => {
    if (!ready) return;
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        positions,
        exploratory: edges.filter((edge) => edge.type === "exploratory"),
      }),
    );
  }, [positions, edges, storageKey, ready]);

  const node =
    selected?.type === "node"
      ? nodes.find((item) => item.id === selected.id)
      : null;
  const edge =
    selected?.type === "edge"
      ? edges.find((item) => item.id === selected.id)
      : null;
  function coordinates(id: string) {
    return positions[id] ?? { x: 0, y: 0 };
  }
  function curve(from: string, to: string) {
    const a = coordinates(from),
      b = coordinates(to);
    const backward = b.x < a.x;
    const x1 = backward ? a.x : a.x + WIDTH,
      y1 = a.y + HEIGHT / 2,
      x2 = backward ? b.x + WIDTH : b.x,
      y2 = b.y + HEIGHT / 2;
    const bend = Math.max(70, Math.abs(x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + (backward ? -bend : bend)} ${y1}, ${x2 + (backward ? bend : -bend)} ${y2}, ${x2} ${y2}`;
  }
  function zoomBy(delta: number) {
    if (!stage.current) return;
    const next = Math.max(importedName ? 0.1 : 0.45, Math.min(2, zoom + delta));
    const center = {
      x: stage.current.clientWidth / 2,
      y: stage.current.clientHeight / 2,
    };
    const ratio = next / zoom;
    setPan({
      x: center.x - (center.x - pan.x) * ratio,
      y: center.y - (center.y - pan.y) * ratio,
    });
    setZoom(next);
  }

  function fit() {
    if (!nodes.length || !stage.current) return;
    const points = nodes.map((item) => coordinates(item.id));
    const minX = Math.min(...points.map((p) => p.x)),
      maxX = Math.max(...points.map((p) => p.x + WIDTH));
    const minY = Math.min(...points.map((p) => p.y)),
      maxY = Math.max(...points.map((p) => p.y + HEIGHT));
    const scale = Math.min(
      1.25,
      Math.max(
        importedName ? 0.1 : 0.45,
        Math.min(
          (stage.current.clientWidth - 60) / (maxX - minX),
          (stage.current.clientHeight - 60) / (maxY - minY),
        ),
      ),
    );
    setZoom(scale);
    setPan({
      x: (stage.current.clientWidth - (maxX - minX) * scale) / 2 - minX * scale,
      y:
        (stage.current.clientHeight - (maxY - minY) * scale) / 2 - minY * scale,
    });
  }
  async function expand(item: MapNode) {
    setLoading(true);
    setWarning("");
    try {
      const graph = await api<Graph>(
        `${endpoint}/expand/${item.kind}/${encodeURIComponent(item.record_id)}`,
      );
      merge(graph);
    } catch (error) {
      setWarning((error as Error).message);
    } finally {
      setLoading(false);
    }
  }
  async function search(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setWarning("");
    try {
      setMatches(
        await api<MapNode[]>(
          `${endpoint}/search?q=${encodeURIComponent(query)}`,
        ),
      );
    } catch (error) {
      setWarning((error as Error).message);
    } finally {
      setLoading(false);
    }
  }
  async function connect(from: string, to: string) {
    if (!to || from === to) return;
    let recorded = edges.find(
      (item) =>
        item.type === "recorded" &&
        ((item.from === from && item.to === to) ||
          (item.from === to && item.to === from)),
    );
    if (!recorded && !importedName) {
      const source = nodes.find((item) => item.id === from);
      if (source) {
        try {
          const graph = await api<Graph>(
            `${endpoint}/expand/${source.kind}/${encodeURIComponent(source.record_id)}`,
          );
          merge(graph);
          recorded = graph.edges.find(
            (item) =>
              (item.from === from && item.to === to) ||
              (item.from === to && item.to === from),
          );
        } catch (error) {
          setWarning((error as Error).message);
          return;
        }
      }
    }
    if (recorded) {
      setSelected({ type: "edge", id: recorded.id });
      selectSource(recorded.evidence_kind, recorded.evidence_id);
      return;
    }
    const newEdge: MapEdge = {
      id: `exploratory:${crypto.randomUUID()}`,
      from,
      to,
      label: "Exploratory — no recorded relationship",
      type: "exploratory",
    };
    setEdges((current) => [...current, newEdge]);
    setSelected({ type: "edge", id: newEdge.id });
    selectSource();
  }
  function selectSource(kind?: string, id?: string) {
    inspected.current++;
    onSource(
      kind && id
        ? {
            kind,
            record_id: id,
            map_id: importedName ? endpoint.split("/").at(-1) : undefined,
          }
        : undefined,
    );
  }

  async function inspect(kind: string, id: string) {
    const request = ++inspected.current;
    setDetail(null);
    onSource();
    try {
      const result = await api<{
        status: string;
        record: Record<string, unknown> | null;
      }>(`${endpoint}/record/${kind}/${encodeURIComponent(id)}`);
      if (request !== inspected.current) return;
      if (result.status === "valid") selectSource(kind, id);
      setDetail(
        result.record ?? {
          status: result.status,
          message: "No unique valid source record is available.",
        },
      );
    } catch (error) {
      setWarning((error as Error).message);
    }
  }
  function move(event: React.PointerEvent<HTMLDivElement>) {
    if (wiring) {
      const bounds = stage.current!.getBoundingClientRect();
      setWiring({
        ...wiring,
        x: (event.clientX - bounds.left - pan.x) / zoom,
        y: (event.clientY - bounds.top - pan.y) / zoom,
      });
    }
    if (!drag.current) return;
    const dx = event.clientX - drag.current.x,
      dy = event.clientY - drag.current.y;
    drag.current.x = event.clientX;
    drag.current.y = event.clientY;
    if (drag.current.pan)
      setPan((current) => ({ x: current.x + dx, y: current.y + dy }));
    else if (drag.current.id)
      setPositions((current) => ({
        ...current,
        [drag.current!.id!]: {
          x: (current[drag.current!.id!]?.x ?? 0) + dx / zoom,
          y: (current[drag.current!.id!]?.y ?? 0) + dy / zoom,
        },
      }));
  }
  return (
    <section className="map-page" aria-label="Supply Chain Map">
      <div className="map-heading">
        <div>
          <h1>{importedName || "Supply Chain Map"}</h1>
          <p>
            Recorded links are solid. Your exploratory links are dashed and
            never change source data.
          </p>
        </div>
        <span className="map-version">
          {importedName
            ? "Uploaded relationships"
            : `Synthetic snapshot · ${datasetFingerprint.slice(0, 8)}`}
        </span>
      </div>
      <div className="map-toolbar">
        <form onSubmit={search} role="search">
          <label htmlFor="map-search">Find a record</label>
          <input
            id="map-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              importedName ? "Find by name or ID" : "Supplier B, SH367, O367…"
            }
          />
          <button type="submit">Find</button>
        </form>
        <button onClick={() => zoomBy(-0.15)} aria-label="Zoom out">
          −
        </button>
        <span aria-live="polite">{Math.round(zoom * 100)}%</span>
        <button onClick={() => zoomBy(0.15)} aria-label="Zoom in">
          +
        </button>
        <button
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
        >
          Reset map zoom
        </button>
        <button onClick={fit}>Fit view</button>
        <button
          onClick={() => setListOpen((value) => !value)}
          aria-expanded={listOpen}
        >
          Relationships
        </button>
      </div>
      {warning && (
        <p className="map-warning" role="alert">
          {warning}
        </p>
      )}
      {loading && (
        <p className="map-loading" role="status">
          Checking recorded relationships…
        </p>
      )}
      {!!matches.length && (
        <div className="map-search-results" aria-label="Search results">
          {matches.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                merge({ nodes: [item], edges: [], warnings: [] });
                setSelected({ type: "node", id: item.id });
                if (item.status === "valid")
                  selectSource(item.kind, item.record_id);
                else selectSource();
                setMatches([]);
              }}
            >
              {item.kind}: {item.label} · {item.status}
            </button>
          ))}
        </div>
      )}
      <div className="map-layout">
        <div
          className="map-stage"
          ref={stage}
          role="application"
          aria-label="Supply chain canvas. Use the relationship list and Connect to controls without dragging."
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) {
              drag.current = { x: event.clientX, y: event.clientY, pan: true };
              event.currentTarget.setPointerCapture(event.pointerId);
            }
          }}
          onPointerMove={move}
          onPointerUp={(event) => {
            if (wiring) {
              const target = (
                document.elementFromPoint(
                  event.clientX,
                  event.clientY,
                ) as HTMLElement | null
              )?.closest<HTMLElement>("[data-socket-node]")?.dataset.socketNode;
              if (target) void connect(wiring.from, target);
              setWiring(null);
            }
            drag.current = null;
          }}
          onWheel={(event) => {
            if (event.ctrlKey || event.metaKey) {
              event.preventDefault();
              setZoom((value) =>
                Math.min(
                  2,
                  Math.max(0.45, value - Math.sign(event.deltaY) * 0.1),
                ),
              );
            }
          }}
        >
          <div
            className="map-world"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            }}
          >
            <svg
              className="map-wires"
              width="1800"
              height="1200"
              aria-hidden="true"
            >
              {edges
                .filter((item) => positions[item.from] && positions[item.to])
                .map((item) => (
                  <path
                    key={item.id}
                    d={curve(item.from, item.to)}
                    className={`map-wire ${item.type} ${selected?.id === item.id ? "selected" : ""}`}
                    onClick={() => {
                      setSelected({ type: "edge", id: item.id });
                      selectSource(item.evidence_kind, item.evidence_id);
                      setDetail(null);
                    }}
                  />
                ))}
              {wiring && (
                <path
                  className="map-wire exploratory"
                  d={`M ${coordinates(wiring.from).x + WIDTH} ${coordinates(wiring.from).y + HEIGHT / 2} L ${wiring.x} ${wiring.y}`}
                />
              )}
            </svg>
            {nodes.map((item) => {
              const point = coordinates(item.id);
              return (
                <div
                  key={item.id}
                  className={`map-node ${selected?.id === item.id ? "selected" : ""}`}
                  style={{ left: point.x, top: point.y }}
                >
                  <button
                    className="map-node-title"
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      drag.current = {
                        id: item.id,
                        x: event.clientX,
                        y: event.clientY,
                      };
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onClick={() => {
                      setSelected({ type: "node", id: item.id });
                      if (item.status === "valid")
                        selectSource(item.kind, item.record_id);
                      else selectSource();
                      setDetail(null);
                    }}
                    aria-label={`Select and drag ${item.kind} ${item.label}`}
                  >
                    <small>{item.kind}</small>
                    <strong>{item.label}</strong>
                  </button>
                  <button
                    className="map-socket input"
                    data-socket-node={item.id}
                    aria-label={`Connect to ${item.label}`}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => {
                      if (wiring) {
                        void connect(wiring.from, item.id);
                        setWiring(null);
                      }
                    }}
                  />
                  <button
                    className="map-socket output"
                    data-socket-node={item.id}
                    aria-label={`Start connection from ${item.label}`}
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      event.currentTarget.setPointerCapture(event.pointerId);
                      setWiring({
                        from: item.id,
                        x: point.x + WIDTH,
                        y: point.y + HEIGHT / 2,
                      });
                    }}
                  />
                </div>
              );
            })}
          </div>
          {!nodes.length && !loading && (
            <p className="map-empty">
              No verified seed network is available. Search for a record to
              begin.
            </p>
          )}
        </div>
        {(selected || listOpen) && (
          <div className="map-inspector" aria-label="Map inspector">
            {selected?.type === "node" && node && (
              <>
                <h2>{node.label}</h2>
                <p>
                  {node.kind} · {node.status} ·{" "}
                  {node.source_record_id ?? "source unavailable"}
                </p>
                <div className="map-inspector-actions">
                  {!importedName && (
                    <button onClick={() => void expand(node)}>
                      Expand one step
                    </button>
                  )}
                  <button
                    onClick={() => void inspect(node.kind, node.record_id)}
                  >
                    Inspect source
                  </button>
                </div>
                <label>
                  Connect to…
                  <select
                    value={connectTarget}
                    onChange={(e) => setConnectTarget(e.target.value)}
                  >
                    <option value="">Choose a visible box</option>
                    {nodes
                      .filter((item) => item.id !== node.id)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.kind}: {item.label}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  disabled={!connectTarget}
                  onClick={() => void connect(node.id, connectTarget)}
                >
                  Connect to selected box
                </button>
              </>
            )}
            {selected?.type === "edge" && edge && (
              <>
                <h2>
                  {edge.type === "recorded"
                    ? "Recorded connection"
                    : "Exploratory connection"}
                </h2>
                <p>
                  {edge.from} → {edge.to}
                </p>
                <p>{edge.label}</p>
                {edge.type === "recorded" ? (
                  <>
                    <p>Evidence: {edge.source_record_id}</p>
                    <button
                      onClick={() =>
                        void inspect(edge.evidence_kind!, edge.evidence_id!)
                      }
                    >
                      View matching source record
                    </button>
                  </>
                ) : (
                  <>
                    <p>
                      No source record supports this user-created wire. It is
                      local to this browser session.
                    </p>
                    <button
                      onClick={() => {
                        setEdges((current) =>
                          current.filter((item) => item.id !== edge.id),
                        );
                        setSelected(null);
                        selectSource();
                      }}
                    >
                      Remove exploratory connection
                    </button>
                  </>
                )}
              </>
            )}
            {detail && (
              <details open>
                <summary>Source record fields</summary>
                <dl className="map-record">
                  {Object.entries(detail).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replaceAll("_", " ")}</dt>
                      <dd>{String(value ?? "Unknown")}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            {listOpen && (
              <div className="map-relations">
                <h2>Relationship list</h2>
                {edges.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => {
                      setSelected({ type: "edge", id: item.id });
                      selectSource(item.evidence_kind, item.evidence_id);
                      setDetail(null);
                    }}
                  >
                    {item.from} → {item.to} ·{" "}
                    {item.type === "recorded" ? item.label : "Exploratory"}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <p className="map-footnote">
        {importedName
          ? "Imported relationships are stored separately from analysis and review evidence."
          : "Map records come from the current synthetic dataset. Saved review evidence remains bound to its historical snapshot in History."}{" "}
        Exploratory wires and positions are stored only in this browser session.
      </p>
    </section>
  );
}
