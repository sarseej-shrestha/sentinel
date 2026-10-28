"""Trust regressions: validate before execution; do not drop request constraints."""

import pytest

from sentinel.application import Application
from sentinel.data.build_duckdb import build_database
from sentinel.nlq.authority import resolve
from sentinel.tasks import Task


@pytest.fixture
def app(tmp_path):
    return Application(build_database(tmp_path / "a.duckdb"), tmp_path / "state.sqlite3")


def test_guided_request_does_not_reparse_text(app, monkeypatch):
    import sentinel.nlq.service as service

    def reject(*args):
        raise AssertionError("Guided inputs must not use language parsing")

    monkeypatch.setattr(service, "resolve", reject)
    result = app.analyze(Task(kind="what_if", warehouse_id="W3", increase_percent=15.123456))
    assert result["state"] == "success"
    assert result["plan"]["scenario"]["demand_increase"] == 15.123456 / 100


def test_constructed_invalid_task_never_executes(app, monkeypatch):
    import sentinel.nlq.service as service

    calls = []
    monkeypatch.setattr(service, "execute", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError):
        app.analyze(Task.model_construct(kind="stockout", horizon_days=-1))
    assert not calls


@pytest.mark.parametrize(
    "question",
    [
        "Show products below 14 days of cover with low daily demand",
        "Show products with 14 days of stock remaining",
    ],
)
def test_unconsumed_constraints_require_clarification(question):
    assert resolve(question)["state"] != "supported"
