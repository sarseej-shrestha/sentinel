from sentinel.data.build_duckdb import build_database
from sentinel.data.scenarios import scenario_records
from sentinel.nlq.service import ask


class Unavailable:
    name = "test_unavailable"

    def generate(self, question, retrieved):
        raise TimeoutError("simulated model timeout")


class Malformed:
    name = "test_malformed"

    def generate(self, question, retrieved):
        return '{"sql": "DELETE FROM orders"}'


def test_unavailable_and_malformed_models(tmp_path):
    for planner in (Unavailable(), Malformed()):
        record = ask(tmp_path / "unused.duckdb", "Show products", planner=planner)
        assert record["status"] == "clarification" and record["abstained"]
        assert record["model_output"] is None and not record["fallback_used"]
        assert record["query_result"] is None


def test_missing_field_abstains(tmp_path):
    path = build_database(
        tmp_path / "missing.duckdb", records=scenario_records("missing_promised_date")
    )
    record = ask(path, "Show shipments missing promised delivery dates")
    assert record["status"] == "missing_information"
    assert record["abstained"] and record["query_result"]["rows"][0]["promised_date"] is None


def test_unsupported_and_unsafe_questions(tmp_path):
    for question, status in (
        ("", "clarification"),
        ("write a poem", "clarification"),
        ("Delete all delayed orders.", "blocked"),
    ):
        record = ask(tmp_path / "unused.duckdb", question)
        assert record["status"] == status and record["abstained"]
        assert record["query_result"] is None
