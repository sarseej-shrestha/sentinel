"""Bounded, source-backed graph retrieval shared by workspace tools."""

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from pydantic import Field, model_validator

from sentinel import map as supply_map
from sentinel.nlq.registry import ENTITIES, normalized
from sentinel.tasks import StrictModel

# Directed source fields. Reverse lookup follows the same recorded edge.
FIELDS = {
    "shipment": {"supplier": "supplier_id", "warehouse": "warehouse_id", "order": "order_id"},
    "order": {"warehouse": "warehouse_id"},
    "order_item": {"order": "order_id", "product": "product_id"},
    "product": {"supplier": "supplier_id"},
}
KINDS = (*supply_map.TABLES, "store", "facility", "customer")
# Cross-record associations have explicit meanings. Arbitrary connected paths
# would incorrectly associate orders sharing a warehouse or supplier.
PATHS = (
    ("supplier", "shipment", "order"),
    ("supplier", "shipment", "warehouse"),
    ("warehouse", "shipment", "order"),
    ("order", "order_item", "product"),
    ("shipment", "order", "order_item", "product"),
    ("supplier", "shipment", "order", "order_item", "product"),
)


class Lookup(StrictModel):
    entity: str = Field(
        min_length=1, max_length=120, description="Exact label, ID or stored alias from user input"
    )
    related: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Requested destination entity types, e.g. order and warehouse. Empty means inspect the entity.",
    )
    within: str | None = Field(
        default=None,
        max_length=120,
        description="Optional second entity label or ID for filtering, e.g. Warehouse 2",
    )
    map_name: str | None = Field(
        default=None, max_length=80, description="Optional exact map name from the user"
    )
    start: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Inclusive earliest recorded promised date or imported event date",
    )
    end: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Inclusive latest recorded promised date or imported event date",
    )
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=10, ge=1, le=10)

    @model_validator(mode="after")
    def validate_filters(self):
        from datetime import date

        if len(set(self.related)) != len(self.related) or any(
            kind not in KINDS for kind in self.related
        ):
            raise ValueError("Choose supported and distinct entity types.")
        if self.start:
            date.fromisoformat(self.start)
        if self.end:
            date.fromisoformat(self.end)
        if self.start and self.end and self.start > self.end:
            raise ValueError("Start date must be on or before end date.")
        if (self.start or self.end or self.within) and not self.related:
            raise ValueError("Specify a relationship type for filters.")
        return self


@dataclass(frozen=True)
class Node:
    kind: str
    identity: str
    label: str
    aliases: tuple[str, ...] = ()
    source: str = ""

    @property
    def key(self):
        return f"{self.kind}:{self.identity}"


@dataclass(frozen=True)
class Edge:
    left: str
    right: str
    record_kind: str
    record_id: str
    source: str
    event_date: str | None = None


class Graph:
    def __init__(self, identity, label, version, nodes, edges, *, omissions=0):
        self.identity, self.label, self.version = identity, label, version
        self.nodes = {node.key: node for node in nodes}
        self.edges = defaultdict(list)
        self.omissions = omissions
        for edge in edges:
            self.edges[edge.left].append((edge.right, edge))
            self.edges[edge.right].append((edge.left, edge))

    def candidates(self, query):
        phrase = normalized(query)
        exact = []
        partial = []
        for node in self.nodes.values():
            names = tuple(map(normalized, (node.identity, node.label, *node.aliases)))
            if phrase in names:
                exact.append(node)
            elif any(phrase in value for value in names):
                partial.append(node)
        return exact, partial

    def related(self, origin, kind, request, other=None):
        def date_matches(edge):
            return bool(
                edge.event_date
                and (not request.start or edge.event_date >= request.start)
                and (not request.end or edge.event_date <= request.end)
            )

        # Direct relationships, then only semantically supported fixture paths.
        # Imported files describe a graph, so use at most three hops there.
        if self.identity:
            patterns = [(origin.kind, *(None for _ in range(hops - 1)), kind) for hops in (1, 2, 3)]
        else:
            patterns = [(origin.kind, kind)]
            for path in PATHS:
                for directed in (path, path[::-1]):
                    if directed[0] == origin.kind and directed[-1] == kind:
                        patterns.append(directed)
        results = {}

        def visit(keys, edges, remaining):
            if not remaining:
                if other and other.key not in keys:
                    anchor = (
                        [key for key in keys if self.nodes[key].kind == "shipment"]
                        if not self.identity
                        else [keys[-1]]
                    )
                    if not any(
                        any(dest == other.key for dest, _ in self.edges[key]) for key in anchor
                    ):
                        return
                if (request.start or request.end) and not any(date_matches(edge) for edge in edges):
                    return
                target = keys[-1]
                if target not in results or len(edges) < len(results[target][1]):
                    results[target] = (keys, edges)
                return
            for dest, edge in self.edges[keys[-1]]:
                if dest in keys or remaining[0] not in (None, self.nodes[dest].kind):
                    continue
                visit((*keys, dest), (*edges, edge), remaining[1:])

        for pattern in patterns:
            visit((origin.key,), (), pattern[1:])
        selected = sorted(results.values(), key=lambda item: self.nodes[item[0][-1]].identity)
        return len(selected), selected[request.offset : request.offset + request.limit]


@lru_cache(maxsize=4)
def fixture_graph(database, version):
    nodes, records, edges = {}, {}, []
    omissions = 0
    with supply_map._connection(database) as con:
        for kind, (table, key, title) in supply_map.TABLES.items():
            fields = [key, "source_record_id", "data_quality_flag", *FIELDS.get(kind, {}).values()]
            if title:
                fields.append(title)
            if kind == "shipment":
                fields.append("promised_date")
            cursor = con.execute(
                f"SELECT {', '.join(fields)}, count(*) OVER (PARTITION BY {key}) AS copies "
                f"FROM {table} ORDER BY {key} LIMIT 20001"
            )
            rows = cursor.fetchall()
            if len(rows) > 20000:
                raise ValueError("The fixture exceeds the assistant's indexed graph limit.")
            for values in rows:
                row = dict(zip([*fields, "copies"], values, strict=True))
                identity = row[key]
                if not identity or row["copies"] != 1 or row["data_quality_flag"] != "ok":
                    omissions += 1
                    continue
                aliases = tuple(ENTITIES.get(key, {}).get(identity, ()))
                node = Node(
                    kind,
                    identity,
                    (row[title] if title else None) or identity,
                    aliases,
                    row["source_record_id"],
                )
                nodes[node.key], records[node.key] = node, row
        for key, node in nodes.items():
            row = records[key]
            for kind, field in FIELDS.get(node.kind, {}).items():
                target = f"{kind}:{row[field]}"
                if target not in nodes:
                    omissions += 1
                    continue
                when = row.get("promised_date")
                edges.append(
                    Edge(
                        key,
                        target,
                        node.kind,
                        node.identity,
                        node.source,
                        when.isoformat() if when else None,
                    )
                )
    return Graph(
        None, "Sentinel synthetic fixture", version, nodes.values(), edges, omissions=omissions
    )


def imported_graph(meta, stored):
    nodes = [
        Node(row["kind"], row["record_id"], row["label"], source=row["source_record_id"])
        for row in stored["graph"]["nodes"]
    ]
    edges = []
    for edge in stored["graph"]["edges"]:
        for row in stored["records"][edge["id"]]["rows"]:
            edges.append(
                Edge(
                    edge["from"],
                    edge["to"],
                    "relationship",
                    edge["id"],
                    f"upload row {row['source_row']}",
                    row.get("event_date") or None,
                )
            )
    return Graph(
        meta["id"],
        meta["name"],
        stored["digest"],
        nodes,
        edges,
        omissions=len(stored.get("rejected", [])),
    )
