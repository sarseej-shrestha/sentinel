import base64
import io
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database
from sentinel.map_import import HEADERS

HEAD = ",".join(HEADERS)
LINE = "supplier,S01,supplies,warehouse,W01,Vendor,Depot"
CLIENT = {"X-Sentinel-Client": "workspace"}


def encode(raw, filename):
    return {"filename": filename, "data": base64.b64encode(raw).decode()}


def test_map_preview_is_atomic_idempotent_and_bound_to_upload(tmp_path):
    db = build_database(tmp_path / "analytics.duckdb")
    state = tmp_path / "state.sqlite3"
    source = (
        f"{HEAD}\n{LINE}\n{LINE}\n{LINE.replace('Vendor', 'Conflicting')}\n"
        f"{LINE.replace('Depot', '=TEXT(3)')}\n"
    )
    upload = encode(source.encode(), "relationships.csv")
    with TestClient(create_app(db, state), headers=CLIENT) as client:
        fixture_before = client.get("/api/map/seed").json()
        history_before = client.get("/api/history").json()
        checked = client.post("/api/imported-maps/preview", json=upload).json()
        assert checked["accepted"] == 2
        assert [error["row"] for error in checked["rejected"]] == [4, 5]
        assert len(checked["graph"]["nodes"]) == 2
        assert len(checked["graph"]["edges"]) == 1
        assert client.get("/api/imported-maps").json() == []
        form = {**upload, "name": "Imported depot", "digest": checked["digest"]}
        assert (
            client.post("/api/imported-maps/confirm", json={**form, "digest": "0" * 64}).status_code
            == 422
        )
        saved = client.post("/api/imported-maps/confirm", json=form).json()
        assert client.post("/api/imported-maps/confirm", json=form).json() == saved
        assert client.get("/api/imported-maps").json() == [saved]
        assert client.get("/api/map/seed").json() == fixture_before
        assert client.get("/api/history").json() == history_before
    with TestClient(create_app(db, state)) as client:
        assert client.get("/api/imported-maps").json() == [saved]
        prefix = f"/api/imported-maps/{saved['id']}"
        graph = client.get(prefix + "/seed").json()
        edge = graph["edges"][0]
        assert edge["from"] == "supplier:S01"
        assert edge["to"] == "warehouse:W01"
        record = client.get(prefix + "/record/relationship/" + edge["evidence_id"]).json()["record"]
        assert record["source_rows"] == "2, 3"
        assert record["file"] == "relationships.csv"


def test_workbook_requires_sheet_and_reports_formula_and_bad_fields(tmp_path):
    db = build_database(tmp_path / "analytics.duckdb")
    book = Workbook()
    sheet = book.active
    sheet.title = "Valid rows"
    sheet.append([*HEADERS, "event_date", "quantity"])
    sheet.append([*LINE.split(","), "2025-01-01", 20])
    sheet.append([*LINE.split(","), "bad date", -1])
    sheet.append([*LINE.replace("Depot", "=2+2").split(","), "2025-01-01", 20])
    book.create_sheet("Empty")
    buf = io.BytesIO()
    book.save(buf)
    upload = encode(buf.getvalue(), "workbook.xlsx")
    with TestClient(create_app(db, tmp_path / "state.sqlite3"), headers=CLIENT) as client:
        path = "/api/imported-maps/preview"
        assert client.post(path, json=upload).json() == {"worksheets": ["Valid rows", "Empty"]}
        assert client.post(path, json={**upload, "worksheet": "Empty"}).status_code == 422
        response = client.post(path, json={**upload, "worksheet": "Valid rows"}).json()
        assert response["accepted"] == 1
        assert [row["row"] for row in response["rejected"]] == [3, 4]
        assert "Formula" in response["rejected"][1]["reason"]
        assert client.post(path, json=encode(buf.getvalue(), "wrong.csv")).status_code == 422
        over_limit = io.BytesIO()
        with ZipFile(over_limit, "w", compression=8) as zip_file:
            zip_file.writestr("[Content_Types].xml", b"x" * 2_000_001)
            zip_file.writestr("xl/workbook.xml", b"x")
        assert (
            client.post(path, json=encode(over_limit.getvalue(), "limit.xlsx")).status_code == 422
        )
