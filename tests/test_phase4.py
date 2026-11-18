from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scripts.train_phase4 import errors, ship_features, temporal_partitions
from sentinel.api import create_app
from sentinel.chat import Chat, Choice
from sentinel.data.build_duckdb import build_database
from sentinel.published import Published

HEADERS = {"X-Sentinel-Client": "workspace"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINEL_QWEN_URL", raising=False)
    monkeypatch.setenv("SENTINEL_PUBLISHED_DIR", str(tmp_path))
    db = build_database(tmp_path / "fixture.duckdb")
    with TestClient(create_app(db, tmp_path / "state.sqlite3")) as test_client:
        yield test_client


def test_chat_contract_and_unavailable(client):
    assert client.post("/api/chat", headers=HEADERS, json={"message": "  "}).status_code == 422
    assert client.post("/api/chat", headers=HEADERS, json={"message": "a" * 601}).status_code == 422
    assert (
        client.post(
            "/api/chat", headers=HEADERS, json={"message": "Help", "previous": ["a"] * 3}
        ).status_code
        == 422
    )
    response = client.post("/api/chat", headers=HEADERS, json={"message": "Help"})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]
    assert client.get("/api/published/catalog").status_code == 503
    assert client.get("/api/published/shipment-score?record_id=-1").status_code == 422


def test_model_cannot_invent_a_citation(client, monkeypatch):
    async def unsupported_fact(self, payload):
        return Choice(mode="use", facts=["result"])

    monkeypatch.setattr(Chat, "select", unsupported_fact)
    response = client.post("/api/chat", headers=HEADERS, json={"message": "Invent a result"})
    assert response.status_code == 502
    assert "unavailable evidence" in response.json()["detail"]
    assert (
        client.post(
            "/api/chat", headers=HEADERS, json={"message": "Explain", "analysis_id": "fake"}
        ).status_code
        == 404
    )


def test_grounding_cites_existing_verified_snapshot(client, monkeypatch):
    task = {
        "kind": "supplier_delay",
        "supplier_id": "S2",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }
    result = client.post("/api/analyses", headers=HEADERS, json=task).json()

    async def select(self, payload):
        assert "S2" in payload["available_facts"]["scope"]
        # Evidence is server-resolved. Arbitrary model text or URLs have no field in Choice.
        return Choice(mode="limits", facts=["scope", "scope"])

    monkeypatch.setattr(Chat, "select", select)
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={"message": "What period?", "analysis_id": result["analysis_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"].count("S2") == 1
    assert body["citations"][0]["analysis_id"] == result["analysis_id"]


def test_untrusted_provider_payload_maps_to_recoverable_error(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"mode":"help","facts":[],"url":"https://example.invalid"}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setenv("SENTINEL_QWEN_URL", "http://testserver/v1/chat/completions")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(HTTPException, match="unusable response"):
        import asyncio

        asyncio.run(Chat().select({"question": "Help"}))


def test_linked_orders_and_late_labels_are_excluded():
    frame = pd.DataFrame(
        {
            "order": ["cross", "cross", "still_open", "known"],
            "po_date": pd.to_datetime(["2011-01-01", "2012-02-01", "2011-12-30", "2010-01-01"]),
            "delivered": pd.to_datetime(["2011-02-01", "2012-03-01", "2012-02-01", "2011-01-01"]),
            "country": ["A"] * 4,
            "product_group": ["B"] * 4,
            "quantity": [4] * 4,
            "late": [False, True, True, False],
        }
    )
    _, crossing, late_label = temporal_partitions(
        frame, pd.Timestamp("2012-01-01"), pd.Timestamp("2014-01-01")
    )
    assert crossing.tolist() == [True, True, False, False]
    assert late_label.tolist() == [False, False, True, False]
    assert list(ship_features(frame)) == ["country", "product_group", "quantity"]


def test_artifact_compatibility_and_source_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_PUBLISHED_DIR", str(tmp_path))
    (tmp_path / "bundle.joblib").write_bytes(b"must not deserialize")
    (tmp_path / "manifest.json").write_text('{"version":"incompatible","sklearn":""}')
    assert Published().bundle is None

    historical = Published()
    historical.bundle = {"entities": [("S001", "P0001")]}
    historical.manifest = {"retail": {}}
    with pytest.raises(ValueError, match="eligible store"):
        historical.retail_series("W2", "P2")
    historical.bundle = {"shipment": SimpleNamespace()}
    historical.manifest = {"shipment": {"test_example_ids": [123]}}
    with pytest.raises(ValueError, match="held-out example"):
        historical.shipment_score(456)


def test_missing_held_out_sales_are_excluded_from_metrics():
    truth = np.array([[0.0, np.nan, 10.0]])
    bounds = np.array([[[0, 1, 2], [0, 100, 101], [8, 10, 11]]])
    assert errors(truth, bounds) == {
        "n": 2,
        "missing_outcomes": 1,
        "mae": 0.5,
        "rmse": np.sqrt(0.5),
        "interval_coverage": 1.0,
    }
