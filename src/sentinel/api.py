"""Same-origin local HTTP adapter. All business decisions stay in Python services."""

import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, JsonValue
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sentinel.application import Application
from sentinel.config import AS_OF
from sentinel.nlq.registry import ENTITIES
from sentinel.state import Conflict, IntegrityFailure
from sentinel.tasks import StrictModel, Task


class Recommendation(StrictModel):
    id: str
    label: str
    text: str
    target_row_ids: list[str]


class ForecastPoint(StrictModel):
    date: str
    forecast: float
    baseline: float


class Forecast(StrictModel):
    method: str
    history_days: int
    points: list[ForecastPoint]


class Analysis(StrictModel):
    analysis_id: str
    state: Literal["success", "empty", "uncertain", "error"]
    task: Task | None
    interpretation: str
    plan: dict[str, JsonValue]
    reference_date: str
    dataset_fingerprint: str
    evidence_id: str | None
    row_count: int
    message: str
    warnings: list[str]
    rows: list[dict[str, JsonValue]]
    recommendations: list[Recommendation]
    forecast: Forecast | None
    elapsed_ms: float


class ProposalRequest(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    analysis_id: str
    recommendation_id: str
    reviewer: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


class DecisionRequest(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    decision: Literal["edit", "approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=2000)


class Action(StrictModel):
    id: str
    analysis_id: str
    recommendation_id: str
    evidence_id: str
    target_row_ids: list[str]
    state: Literal["pending", "approved_simulation", "rejected"]
    revision: int
    text: str
    reviewer: str
    simulated_action_only: Literal[True]
    external_action_executed: Literal[False]


class History(StrictModel):
    verified: bool
    event_count: int
    head_hash: str
    events: list[dict[str, JsonValue]]
    actions: list[Action]


class Evidence(StrictModel):
    id: str
    snapshot_id: str
    sql: str
    parameters: dict[str, JsonValue]
    rows: list[dict[str, JsonValue]]
    row_ids: list[str]
    dataset_fingerprint: str
    reference_date: str
    versions: dict[str, JsonValue]
    truncated: bool
    included_count: int
    excluded_count: int
    offset: int
    total: int
    verified: bool


class Catalog(StrictModel):
    reference_date: str
    dataset_fingerprint: str
    entities: dict[str, dict[str, list[str]]]
    synthetic: Literal[True]
    application_version: str


def create_app(database, state, static_dir=None):
    service = Application(database, state)
    app = FastAPI(title="Sentinel", version="1.0", docs_url=None, redoc_url=None)
    app.state.service = service
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if origin and origin != expected:
                return JSONResponse(
                    {"detail": "Use the local Sentinel application origin."}, status_code=403
                )
            if request.headers.get("x-sentinel-client") != "workspace":
                return JSONResponse(
                    {"detail": "A local workspace request is required."}, status_code=403
                )
            if len(await request.body()) > 16384:
                return JSONResponse(
                    {"detail": "Request exceeds the local input limit."}, status_code=413
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "detail": "Check the selected task fields. "
                + "; ".join(e["msg"] for e in exc.errors())
            },
            status_code=422,
        )

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(IntegrityFailure)
    async def integrity(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(sqlite3.Error)
    async def storage(request, exc):
        return JSONResponse(
            {
                "detail": "Storage is unavailable. Keep your draft and retry the same operation; no success is claimed until commit."
            },
            status_code=503,
        )

    @app.exception_handler(KeyError)
    async def unknown(request, exc):
        return JSONResponse({"detail": "The saved record was not found."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/catalog", response_model=Catalog)
    def catalog():
        return {
            "reference_date": AS_OF,
            "dataset_fingerprint": service.dataset_fingerprint,
            "entities": ENTITIES,
            "synthetic": True,
            "application_version": "1.0",
        }

    @app.post("/api/analyses", response_model=Analysis)
    def analyze(task: Task):
        return service.analyze(task)

    @app.get("/api/analyses/{analysis_id}", response_model=Analysis)
    def analysis(analysis_id: str):
        return service.store.analysis(analysis_id)

    @app.get("/api/evidence/{evidence_id}", response_model=Evidence)
    def evidence(
        evidence_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return service.store.evidence(evidence_id, offset, limit)

    @app.post("/api/proposals", response_model=Action)
    def propose(request: ProposalRequest):
        return service.store.propose(**request.model_dump())

    @app.post("/api/proposals/{action_id}/decisions", response_model=Action)
    def decide(action_id: str, request: DecisionRequest):
        return service.store.decide(action_id=action_id, **request.model_dump())

    @app.get("/api/history", response_model=History)
    def history():
        # A read transaction would offer a point-in-time listing; each snapshot is independently verified.
        events = service.store.history()
        from sentinel.audit.logger import verify_events

        return {
            "verified": True,
            **verify_events(events),
            "events": events,
            "actions": service.store.actions(),
        }

    @app.get("/api/legacy/{identity}", response_model=list[dict[str, JsonValue]])
    def legacy(identity: str):
        return service.store.legacy(identity)

    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="workspace")
    return app
