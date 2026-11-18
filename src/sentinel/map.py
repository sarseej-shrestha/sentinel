"""Read-only, allowlisted record relationships for the local supply-chain map."""

from contextlib import contextmanager
from datetime import date, datetime

import duckdb

TABLES = {
    "supplier": ("suppliers", "supplier_id", "supplier_name"),
    "warehouse": ("warehouses", "warehouse_id", "warehouse_name"),
    "product": ("products", "product_id", "product_name"),
    "order": ("orders", "order_id", None),
    "order_item": ("order_items", "item_id", None),
    "shipment": ("shipments", "shipment_id", None),
}


def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


@contextmanager
def _connection(database):
    con = duckdb.connect(str(database), read_only=True)
    try:
        yield con
    finally:
        con.close()


def _rows(con, table, where, params=(), limit=50):
    cursor = con.execute(f"SELECT * FROM {table} WHERE {where} LIMIT {limit}", params)
    names = [column[0] for column in cursor.description]
    return [{key: _value(value) for key, value in zip(names, row)} for row in cursor.fetchall()]


def _record(con, kind, identity):
    if kind not in TABLES or not identity or len(identity) > 100:
        raise ValueError("Choose a supported map record")
    table, key, _ = TABLES[kind]
    matches = _rows(con, table, f"{key} = ?", (identity,), 2)
    if not matches:
        return {"kind": kind, "id": identity, "status": "missing", "record": None}
    if len(matches) > 1:
        return {"kind": kind, "id": identity, "status": "ambiguous", "record": None}
    row = matches[0]
    status = "valid" if row.get("data_quality_flag") == "ok" else "excluded"
    return {"kind": kind, "id": identity, "status": status, "record": row}


def _node(record):
    kind, identity, row = record["kind"], record["id"], record["record"]
    name = row.get(TABLES[kind][2]) if row and TABLES[kind][2] else None
    return {
        "id": f"{kind}:{identity}",
        "kind": kind,
        "record_id": identity,
        "label": f"{name} ({identity})" if name else identity,
        "status": record["status"],
        "source_record_id": row.get("source_record_id") if row else None,
    }


def _links(con, record):
    if record["status"] != "valid":
        return [], [f"{record['kind']} {record['id']} is {record['status']}; relationships cannot be verified."]
    kind, row, identity = record["kind"], record["record"], record["id"]
    links = []
    if kind == "shipment":
        links = [
            ("supplier", row.get("supplier_id"), "recorded supplier", "shipment", identity),
            ("warehouse", row.get("warehouse_id"), "destination warehouse", "shipment", identity),
            ("order", row.get("order_id"), "recorded order", "shipment", identity),
        ]
    elif kind == "order":
        links = [("warehouse", row.get("warehouse_id"), "order warehouse", "order", identity)]
        links += [
            ("order_item", item["item_id"], "order line item", "order_item", item["item_id"])
            for item in _rows(con, "order_items", "order_id = ?", (identity,), 30)
        ]
    elif kind == "order_item":
        links = [
            ("order", row.get("order_id"), "line item order", "order_item", identity),
            ("product", row.get("product_id"), "line item product", "order_item", identity),
        ]
    elif kind == "product":
        links = [("supplier", row.get("supplier_id"), "listed product supplier", "product", identity)]
        links += [
            ("order_item", item["item_id"], "order line item", "order_item", item["item_id"])
            for item in _rows(con, "order_items", "product_id = ?", (identity,), 10)
        ]
    elif kind == "supplier":
        links = [
            ("shipment", item["shipment_id"], "recorded shipment", "shipment", item["shipment_id"])
            for item in _rows(con, "shipments", "supplier_id = ? ORDER BY promised_date", (identity,), 10)
        ]
        links += [
            ("product", item["product_id"], "listed product", "product", item["product_id"])
            for item in _rows(con, "products", "supplier_id = ?", (identity,), 10)
        ]
    elif kind == "warehouse":
        links = [
            ("shipment", item["shipment_id"], "received shipment", "shipment", item["shipment_id"])
            for item in _rows(con, "shipments", "warehouse_id = ? ORDER BY promised_date", (identity,), 10)
        ]
    nodes, edges, warnings = [], [], []
    for other_kind, other_id, label, evidence_kind, evidence_id in links:
        if not other_id:
            warnings.append(f"{label}: source record has no linked ID.")
            continue
        other = _record(con, other_kind, other_id)
        if other["status"] != "valid":
            warnings.append(f"{label}: {other_kind} {other_id} is {other['status']}; no verified wire was drawn.")
            continue
        nodes.append(_node(other))
        source = f"{kind}:{identity}"
        target = f"{other_kind}:{other_id}"
        evidence = _record(con, evidence_kind, evidence_id)
        if evidence["status"] != "valid":
            warnings.append(f"{label}: contributing record is {evidence['status']}.")
            continue
        edges.append({
            "id": "|".join(sorted((source, target))) + "|" + label,
            "from": source,
            "to": target,
            "label": label,
            "evidence_kind": evidence_kind,
            "evidence_id": evidence_id,
            "source_record_id": evidence["record"]["source_record_id"],
            "type": "recorded",
        })
    return (nodes, edges), warnings


def expand(database, kind, identity):
    with _connection(database) as con:
        selected = _record(con, kind, identity)
        result, warnings = _links(con, selected)
        if result:
            nodes, edges = result
        else:
            nodes, edges = [], []
        return {"nodes": [_node(selected), *nodes], "edges": edges, "warnings": warnings}


def seed(database):
    with _connection(database) as con:
        match = con.execute(
            "SELECT shipment_id FROM shipments WHERE supplier_id = ? AND warehouse_id = ? "
            "AND promised_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-31' "
            "AND data_quality_flag = 'ok' ORDER BY promised_date, shipment_id LIMIT 1",
            ["S2", "W2"],
        ).fetchone()
    if not match:
        return {"nodes": [], "edges": [], "warnings": ["No verified S2/W2 seed shipment is available."]}
    first = expand(database, "shipment", match[0])
    order = next((n for n in first["nodes"] if n["kind"] == "order"), None)
    if order:
        second = expand(database, "order", order["record_id"])
        by_id = {n["id"]: n for n in [*first["nodes"], *second["nodes"]]}
        by_edge = {e["id"]: e for e in [*first["edges"], *second["edges"]]}
        return {"nodes": list(by_id.values()), "edges": list(by_edge.values()), "warnings": first["warnings"] + second["warnings"]}
    return first


def search(database, query):
    query = query.strip()
    if not 1 <= len(query) <= 60:
        raise ValueError("Search for a record ID or name (1–60 characters)")
    found = []
    with _connection(database) as con:
        for kind, (table, key, name) in TABLES.items():
            if kind == "order_item":
                continue
            condition = f"CAST({key} AS VARCHAR) ILIKE ?"
            params = [f"%{query}%"]
            if name:
                condition += f" OR {name} ILIKE ?"
                params.append(f"%{query}%")
            matches = _rows(con, table, condition, params, 20)
            for identity in dict.fromkeys(row[key] for row in matches):
                found.append(_node(_record(con, kind, identity)))
    return found[:30]


def record(database, kind, identity):
    with _connection(database) as con:
        return _record(con, kind, identity)
