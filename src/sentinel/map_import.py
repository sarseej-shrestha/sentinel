"""Bounded spreadsheet relationship imports for independent, immutable maps."""

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError, fromstring
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import Field

from sentinel.state import Conflict
from sentinel.tasks import StrictModel

MAX_BYTES = 96 * 1024
MAX_ROWS = 40
HEADERS = (
    "source_type",
    "source_id",
    "relationship",
    "target_type",
    "target_id",
    "source_label",
    "target_label",
)
OPTIONAL = ("record_id", "event_date", "quantity", "notes")
TYPES = {
    "supplier",
    "warehouse",
    "shipment",
    "product",
    "order",
    "order_item",
    "store",
    "facility",
    "customer",
}
RELATIONSHIPS = {
    "supplies",
    "ships_to",
    "contains",
    "fulfills",
    "stocks",
    "transfers_to",
    "delivers_to",
    "sourced_from",
}


class Upload(StrictModel):
    filename: str = Field(min_length=1, max_length=120)
    data: str = Field(min_length=4, max_length=MAX_BYTES * 4 // 3)
    worksheet: str | None = Field(default=None, max_length=31)


class Confirm(Upload):
    name: str = Field(min_length=1, max_length=64)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def decode(upload):
    try:
        raw = base64.b64decode(upload.data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Upload a valid CSV or XLSX file.") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("Files must be at most 96 KiB.")
    return raw, Path(upload.filename).suffix.lower()


def workbook_rows(raw, worksheet):
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if (
                len(names) > 100
                or len(set(names)) != len(names)
                or sum(item.file_size for item in members) > 2_000_000
                or any(item.flag_bits & 1 for item in members)
                or any("vbaproject" in name.lower() for name in names)
                or "[Content_Types].xml" not in names
                or "xl/workbook.xml" not in names
            ):
                raise ValueError("Workbook exceeds archive limits or uses an unsupported format.")
            for name in names:
                if not name.endswith((".xml", ".rels")):
                    continue
                xml = archive.read(name)
                if b"<!" in xml or b"\x00" in xml or b"macroEnabled" in xml:
                    raise ValueError("Use plain XLSX without macros or custom XML declarations.")
                root = fromstring(xml)
                namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                if root.tag != namespace + "worksheet":
                    continue
                for node in root.iter():
                    if node.tag == namespace + "row" and not re.fullmatch(
                        r"(?:[1-9]|[1-3][0-9]|4[01])", node.get("r", "")
                    ):
                        raise ValueError("Worksheet exceeds 40 relationship rows.")
                    if node.tag == namespace + "c" and not re.fullmatch(
                        r"[A-K](?:[1-9]|[1-3][0-9]|4[01])", node.get("r", "")
                    ):
                        raise ValueError("Worksheet exceeds 40 relationship rows or 11 columns.")
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            sheets = [sheet.title for sheet in book.worksheets]
            if not sheets or len(sheets) > 12:
                raise ValueError("Choose an XLSX workbook with 1–12 worksheets.")
            if not worksheet and len(sheets) > 1:
                return sheets, None
            sheet = worksheet or sheets[0]
            if sheet not in sheets:
                raise ValueError("Select a worksheet from this workbook.")
            selected = book[sheet]
            if (selected.max_column or 0) > len(HEADERS + OPTIONAL) or (
                selected.max_row or 0
            ) > MAX_ROWS + 1:
                raise ValueError("Worksheet exceeds 40 relationship rows or 11 columns.")
            selected.reset_dimensions()
            rows = []
            for cells in selected.iter_rows():
                if len(cells) > len(HEADERS + OPTIONAL) or len(rows) > MAX_ROWS:
                    raise ValueError("Worksheet exceeds 40 relationship rows or 11 columns.")
                rows.append([Formula() if cell.data_type == "f" else cell.value for cell in cells])
            return sheets, rows
        finally:
            book.close()
    except (
        BadZipFile,
        InvalidFileException,
        ParseError,
        RuntimeError,
        KeyError,
        TypeError,
        OSError,
        NotImplementedError,
        IndexError,
    ):
        raise ValueError("Upload a readable, ordinary XLSX workbook.") from None


class Formula:
    pass


def cell_text(value):
    if isinstance(value, Formula):
        raise ValueError("Formula cells are unsupported.")
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time().isoformat() != "00:00:00":
            raise ValueError("Date cells must contain dates without times.")
        value = value.date()
    if isinstance(value, bool) or not isinstance(value, (date, str, float, int)):
        raise ValueError("Unsupported cell value.")
    text = str(value).strip()
    if len(text) > 240 or any(ord(char) < 32 for char in text):
        raise ValueError("Use at most 240 characters per cell and remove control characters.")
    if text.startswith(("=", "+", "-", "@")):
        raise ValueError("Formula-like cells are unsupported.")
    return text


def row_values(headers, values):
    if len(values) > len(headers) and any(
        value is not None and value != "" for value in values[len(headers) :]
    ):
        raise ValueError("Values occur outside the header columns.")
    row = dict(zip(headers, map(cell_text, values)))
    if any(not row.get(key) for key in HEADERS):
        raise ValueError("Fill all required fields, including both endpoints and labels.")
    if (
        not {row["source_type"], row["target_type"]} <= TYPES
        or row["relationship"] not in RELATIONSHIPS
    ):
        raise ValueError("Use one of the supported node and relationship types.")
    if any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", row[key]) for key in ("source_id", "target_id")
    ):
        raise ValueError(
            "Endpoint IDs must be 1–60 characters using letters, digits, underscore, dot or dash."
        )
    if (row["source_type"], row["source_id"]) == (row["target_type"], row["target_id"]):
        raise ValueError("Choose distinct endpoints.")
    if row.get("event_date"):
        try:
            if date.fromisoformat(row["event_date"]).isoformat() != row["event_date"]:
                raise ValueError
        except ValueError:
            raise ValueError("Event date must use YYYY-MM-DD.") from None
    if row.get("quantity"):
        try:
            quantity = float(row["quantity"])
            if not math.isfinite(quantity) or not 0 <= quantity <= 1e12:
                raise ValueError
        except ValueError:
            raise ValueError("Quantity must be numeric and between 0 and 1 trillion.") from None
    return row


def preview(upload):
    raw, extension = decode(upload)
    sheets = []
    if extension == ".xlsx":
        sheets, lines = workbook_rows(raw, upload.worksheet)
        if lines is None:
            return {"worksheets": sheets}
    elif extension == ".csv" and not upload.worksheet:
        try:
            lines = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True))
        except (ValueError, csv.Error):
            raise ValueError("Upload a UTF-8 comma-delimited CSV with valid quoting.") from None
    else:
        raise ValueError("Use a .csv or .xlsx file.")
    if not lines or len(lines) == 1:
        raise ValueError("The selected sheet or CSV is empty.")
    if len(lines) > MAX_ROWS + 1:
        raise ValueError("Upload at most 40 relationship rows.")
    headers = [cell_text(value) for value in lines[0]]
    if len(headers) != len(set(headers)) or not set(HEADERS) <= set(headers) <= set(
        HEADERS + OPTIONAL
    ):
        raise ValueError("Use the required headers from the template. Each header must be unique.")
    nodes, edges, records, rejected, examples = {}, {}, {}, [], []
    for number, values in enumerate(lines[1:], 2):
        try:
            row = row_values(headers, values)
            start = f"{row['source_type']}:{row['source_id']}"
            end = f"{row['target_type']}:{row['target_id']}"
            endpoints = ((start, "source"), (end, "target"))
            if any(
                key in nodes and nodes[key]["label"] != row[f"{side}_label"]
                for key, side in endpoints
            ):
                raise ValueError(
                    "This endpoint has a conflicting label; use one label per identity."
                )
            edge_id = f"{start}|{row['relationship']}|{end}"
            row["source_row"] = number
            for key, side in endpoints:
                nodes.setdefault(
                    key,
                    {
                        "id": key,
                        "kind": row[f"{side}_type"],
                        "record_id": row[f"{side}_id"],
                        "label": row[f"{side}_label"],
                        "status": "valid",
                        "source_record_id": f"Upload row {number}",
                    },
                )
                records.setdefault(key, {"source_rows": [], "label": row[f"{side}_label"]})[
                    "source_rows"
                ].append(number)
            edge = edges.setdefault(
                edge_id,
                {
                    "id": edge_id,
                    "from": start,
                    "to": end,
                    "label": row["relationship"],
                    "type": "recorded",
                    "evidence_kind": "relationship",
                    "evidence_id": edge_id,
                    "source_record_id": f"Upload row {number}",
                },
            )
            records.setdefault(edge["id"], {"rows": []})["rows"].append(row)
            if len(examples) < 3:
                examples.append(row)
        except ValueError as error:
            rejected.append({"row": number, "reason": str(error)})
    sheet = upload.worksheet or (sheets[0] if sheets else None)
    digest = hashlib.sha256(raw + b"\0" + (sheet or "").encode()).hexdigest()
    return {
        "worksheets": sheets,
        "worksheet": sheet,
        "digest": digest,
        "accepted": len(lines) - 1 - len(rejected),
        "rejected": rejected,
        "examples": examples,
        "graph": {"nodes": list(nodes.values()), "edges": list(edges.values()), "warnings": []},
        "records": records,
        "filename": Path(upload.filename).name,
    }


class ImportedMaps:
    def __init__(self, state):
        self.path = Path(state).with_suffix(".maps.sqlite3")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS maps (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, body TEXT NOT NULL)"
        )
        return db

    def save(self, upload):
        result = preview(upload)
        name = upload.name.strip()
        if not name or any(ord(char) < 32 for char in name):
            raise ValueError("Enter a valid map name.")
        if result.get("digest") != upload.digest or not result.get("accepted"):
            raise ValueError("Preview a file with usable relationships before confirming.")
        identity = hashlib.sha256(f"{name.casefold()}\0{result['digest']}".encode()).hexdigest()[
            :24
        ]
        with closing(self.connect()) as db, db:
            try:
                db.execute(
                    "INSERT INTO maps VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
                    (identity, name, json.dumps(result, ensure_ascii=False)),
                )
            except sqlite3.IntegrityError:
                raise Conflict("A different imported map already uses this name.") from None
        return {"id": identity, "name": name}

    def list(self):
        with closing(self.connect()) as db:
            return [
                {"id": identity, "name": name}
                for identity, name in db.execute("SELECT id, name FROM maps ORDER BY name")
            ]

    def get(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT body FROM maps WHERE id=?", [identity]).fetchone()
        if not row:
            raise KeyError("Map unavailable.")
        return json.loads(row[0])
