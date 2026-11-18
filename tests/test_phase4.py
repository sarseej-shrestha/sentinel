from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from scripts.train_phase4 import errors, ship_features, temporal_partitions
from sentinel.api import create_app
from sentinel.chat import Chat
from sentinel.data.build_duckdb import build_database
from sentinel.published import Published

HEADERS = {"X-Sentinel-Client": "workspace"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_QWEN_URL", "http://provider.invalid/v1/chat/completions")
    monkeypatch.setenv("SENTINEL_PUBLISHED_DIR", str(tmp_path))
    db = build_database(tmp_path / "fixture.duckdb")
    with TestClient(create_app(db, tmp_path / "state.sqlite3")) as test_client:
        yield test_client


def send(client, message, **scope):
    return client.post("/api/chat", headers=HEADERS, json={"message": message, **scope})


@pytest.fixture
def shipment(client):
    task = {
        "kind": "supplier_delay",
        "supplier_id": "S2",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }
    return client.post("/api/analyses", headers=HEADERS, json=task).json()


def test_chat_contract_and_unavailable(client, monkeypatch):
    async def offline(self, http, body, context, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(Chat, "complete", offline)
    assert send(client, "  ").status_code == 422
    assert send(client, "a" * 601).status_code == 422
    assert send(client, "Help", history=[{"question": "a", "answer": "b"}] * 5).status_code == 422
    assert send(client, "Help", target_id="supplier:S2").status_code == 422
    response = send(client, "Help")
    assert response.status_code == 503
    assert "configured model" in response.json()["detail"]
    assert client.get("/api/published/catalog").status_code == 503


def test_free_text_help_with_no_selected_facts(client, monkeypatch):
    async def plain(self, http, body, context, **kwargs):
        assert context.facts == {}
        return {"content": "Open Supply Chain Map, then choose Import relationships."}

    monkeypatch.setattr(Chat, "complete", plain)
    result = send(client, "How do I upload an Excel file?").json()
    assert "Import relationships" in result["answer"]
    assert result["citations"] == [] and result["notice"] is None


@pytest.mark.parametrize(
    "legacy",
    [
        '{"mode":"limits","facts":["columns"]}',
        "The selected supplier is unsafe. [[F99]]",
    ],
)
def test_legacy_schema_and_unknown_reference_repair(client, monkeypatch, legacy):
    attempts = []

    async def generated(self, http, body, context, *, offer_tool, repair):
        attempts.append(repair)
        return {
            "content": legacy
            if len(attempts) == 1
            else "Download the relationship template to see the required headers."
        }

    monkeypatch.setattr(Chat, "complete", generated)
    body = send(client, "What columns does my spreadsheet need?").json()
    assert len(attempts) == 2 and attempts[1]
    assert "template" in body["answer"] and "unsafe" not in body["answer"]
    assert body["citations"] == []


def test_bad_reference_drops_dependent_prose_after_one_repair(client, monkeypatch, shipment):
    attempts = []

    async def generated(self, http, body, context, **kwargs):
        attempts.append(kwargs)
        return {
            "content": "These are outstanding overdue orders. [[F999]]\n\nGeneral guidance: Inspect delivery status before acting."
        }

    monkeypatch.setattr(Chat, "complete", generated)
    body = send(client, "What supports this result?", analysis_id=shipment["analysis_id"]).json()
    assert len(attempts) == 2
    assert "outstanding overdue orders" not in body["answer"]
    assert body["answer"] == "General guidance: Inspect delivery status before acting."
    assert body["notice"] and body["verified"]
    assert all(ref["scope"]["evidence_id"] == shipment["evidence_id"] for ref in body["citations"])


def test_scoped_records_preserve_target_and_evidence_version(client, monkeypatch, shipment):
    async def generated(self, http, body, context, **kwargs):
        assert not kwargs["offer_tool"]  # Explicit request already retrieved a page.
        assert context.scope.target_id == "supplier:S2"
        assert any("This page shows 4 examples" in fact.text for fact in context.facts.values())
        return {"content": "The captured evidence includes completed deliveries. [[F2]]"}

    monkeypatch.setattr(Chat, "complete", generated)
    identity = shipment["analysis_id"]
    result = send(client, "Show me the records behind that number.", analysis_id=identity).json()
    assert any("7 late of 31" in value for value in result["verified"])
    assert any("4 examples" in value for value in result["verified"])
    assert all(
        ref["scope"]["version"] == shipment["dataset_fingerprint"] for ref in result["citations"]
    )
    assert result["scope"]["source_id"]
    assert send(client, "Explain", analysis_id=identity, target_id="supplier:S1").status_code == 422
    assert send(client, "Explain", analysis_id="fake").status_code == 404


@pytest.mark.parametrize("arguments", ['{"offset":', '{"offset":-1}', '{"analysis_id":"other"}'])
def test_malformed_tool_request_has_one_bounded_repair(client, monkeypatch, shipment, arguments):
    attempts = []

    async def generated(self, http, body, context, *, offer_tool, repair):
        attempts.append((offer_tool, repair))
        if offer_tool:
            return {
                "tool_calls": [{"function": {"name": "captured_records", "arguments": arguments}}]
            }
        return {"content": "The count comes from the saved delivery evidence. [[F2]]"}

    monkeypatch.setattr(Chat, "complete", generated)
    result = send(client, "What supports this result?", analysis_id=shipment["analysis_id"]).json()
    assert attempts[0] == (True, None) and attempts[1][0] is False and attempts[1][1]
    assert len(attempts) == 2 and result["notice"] is None
    assert result["scope"]["target_id"] == "supplier:S2"


def test_native_tool_runs_one_bounded_page(client, monkeypatch, shipment):
    sent = []

    async def generated(self, http, body, context, *, offer_tool, repair):
        sent.append(offer_tool)
        if offer_tool:
            return {
                "tool_calls": [
                    {"function": {"name": "captured_records", "arguments": {"offset": 4}}}
                ]
            }
        handle = next(key for key, fact in context.facts.items() if "offset 4" in fact.text)
        return {"content": f"These are examples from the captured deliveries. [[{handle}]]"}

    monkeypatch.setattr(Chat, "complete", generated)
    result = send(client, "What supports this result?", analysis_id=shipment["analysis_id"]).json()
    assert sent == [True, False]
    assert any("offset 4" in value for value in result["verified"])


def test_provider_truncation_never_renders_cut_response(client, monkeypatch):
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(kwargs["json"])
        choice = {"message": {"content": "An incomplete"}}
        if len(calls) == 1:
            choice["finish_reason"] = "length"
        else:
            choice["message"]["content"] = (
                "Coverage means how long existing stock can meet typical demand."
            )
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [choice]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = send(
        client,
        "What does coverage mean?",
        history=[{"question": "What is a warehouse?", "answer": "A stock location."}],
    ).json()
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 420
    assert [turn["role"] for turn in calls[0]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "typical demand" in result["answer"] and "incomplete" not in result["answer"]


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
