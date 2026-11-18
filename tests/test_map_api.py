"""The map may show only relationships backed by unique source records."""

from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.data.build_duckdb import build_database, synthetic_records


def test_seed_expand_search_and_source_are_record_bound(tmp_path):
    db = build_database(tmp_path / "analytics.duckdb")
    with TestClient(create_app(db, tmp_path / "state.sqlite3")) as client:
        graph = client.get("/api/map/seed").json()
        assert {"shipment:SH367", "supplier:S2", "warehouse:W2", "order:O367"} <= {
            node["id"] for node in graph["nodes"]
        }
        shipment_edges = [edge for edge in graph["edges"] if edge["from"] == "shipment:SH367"]
        assert {edge["to"] for edge in shipment_edges} == {
            "supplier:S2",
            "warehouse:W2",
            "order:O367",
        }
        assert all(edge["source_record_id"] == "shipments:367" for edge in shipment_edges)
        assert (
            client.get("/api/map/search", params={"q": "Supplier B"}).json()[0]["id"]
            == "supplier:S2"
        )
        item = client.get("/api/map/expand/order_item/OI367").json()
        assert any(edge["to"] == "product:P2" for edge in item["edges"])
        source = client.get("/api/map/record/shipment/SH367").json()
        assert source["record"]["supplier_id"] == "S2"
        assert source["record"]["warehouse_id"] == "W2"
        assert source["record"]["order_id"] == "O367"
        assert client.get("/api/map/record/unknown/SH367").status_code == 422


def test_ambiguous_parent_does_not_create_verified_wire(tmp_path):
    records = synthetic_records()
    duplicate = next(row for row in records["suppliers"] if row[0] == "S2")
    records["suppliers"].append(duplicate)
    db = build_database(tmp_path / "analytics.duckdb", records=records)
    with TestClient(create_app(db, tmp_path / "state.sqlite3")) as client:
        graph = client.get("/api/map/expand/shipment/SH367").json()
        assert "supplier:S2" not in {edge["to"] for edge in graph["edges"]}
        assert graph["warnings"]
        assert graph["nodes"][0]["status"] == "excluded"
        assert client.get("/api/map/record/supplier/S2").json()["status"] == "ambiguous"
