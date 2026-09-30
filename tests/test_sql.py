import pytest
from jsonschema import ValidationError

from sentinel.data.build_duckdb import build_database
from sentinel.nlq.executor import execute
from sentinel.nlq.planner import RulePlanner, validate_plan
from sentinel.nlq.retrieval import SchemaRetriever
from sentinel.nlq.sql_guard import SQLBlocked, guard_sql


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("sql") / "demo.duckdb")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT * FROM supplier_view; DROP TABLE orders",
        "SELECT * FROM orders",
        "SELECT secret FROM supplier_view",
        "SELECT read_blob('/etc/passwd') FROM supplier_view",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM main.supplier_view",
        "COPY supplier_view TO 'x.csv'",
        "INSTALL httpfs",
        "SELECT * INTO out FROM supplier_view",
        "SELECT * FROM supplier_view CROSS JOIN demand_view",
        "SELECT * FROM supplier_view LIMIT -1",
        "WITH x AS (SELECT * FROM supplier_view) SELECT * FROM x",
        "SELECT getenv('HOME') FROM supplier_view",
    ],
)
def test_unsafe_or_unknown_sql_blocked(sql):
    with pytest.raises(SQLBlocked):
        guard_sql(sql)


def test_json_validation():
    with pytest.raises(ValidationError):
        validate_plan({"sql": "SELECT * FROM supplier_view"})
    plan = RulePlanner().generate("Delete all delayed orders.", {})
    assert plan["abstain"] and plan["sql"] is None


def test_read_only_execution_and_limit(database):
    result = execute(database, "SELECT * FROM demand_view LIMIT 9999")
    assert result.status == "ok" and len(result.rows) == 200
    assert result.evidence_id and result.truncated


def test_empty_result(database):
    result = execute(
        database, "SELECT * FROM supplier_view WHERE supplier_name = $name", {"name": "Supplier Z"}
    )
    assert result.status == "empty" and result.rows == []


def test_timeout(database):
    result = execute(database, "SELECT * FROM supplier_view", timeout=0.000001)
    assert result.status == "timeout" and not result.rows


def test_named_parameters_must_match():
    with pytest.raises(SQLBlocked):
        guard_sql("SELECT * FROM supplier_view WHERE supplier_id = $id", {})


def test_retrieval_and_required_plans(database):
    retriever = SchemaRetriever()
    questions = [
        "Which suppliers had the highest late-delivery rate last month?",
        "Show products likely to stock out within the next 14 days.",
        "What happens if demand increases by 15% at Warehouse 3?",
        "Why is Supplier A considered high risk?",
    ]
    for question in questions:
        retrieved = retriever.retrieve(question)
        assert retrieved["views"] and retrieved["metrics"] and retrieved["allowed_joins"]
        plan = RulePlanner().generate(question, retrieved)
        assert not plan["abstain"]
        assert execute(database, plan["sql"], plan["parameters"]).status == "ok"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT s.unknown FROM supplier_view s",
        "SELECT * FROM supplier_view WHERE EXISTS (SELECT * FROM orders)",
        "SELECT * FROM supplier_view UNION SELECT * FROM orders",
        "SELECT current_setting('access_mode') FROM supplier_view",
        "SELECT * FROM glob('/tmp/*')",
        "PRAGMA database_list",
        "ATTACH 'other.duckdb' AS other",
        "SELECT * FROM supplier_view LIMIT $limit",
        "SELECT * FROM supplier_view OFFSET 1",
        "SELECT * FROM supplier_view JOIN risk_view ON supplier_view.supplier_id = risk_view.product_id",
    ],
)
def test_additional_sql_bypasses_are_blocked(sql):
    with pytest.raises(SQLBlocked):
        guard_sql(sql)


def test_documented_join_and_parameter_injection(database):
    joined = execute(
        database,
        "SELECT r.product_id, s.supplier_name FROM risk_view r JOIN supplier_view s ON r.supplier_id=s.supplier_id",
    )
    assert joined.status == "ok" and len(joined.rows) == 18
    injection = execute(
        database,
        "SELECT * FROM supplier_view WHERE supplier_name = $name",
        {"name": "x'; DROP TABLE orders; --"},
    )
    assert injection.status == "empty"
    assert execute(database, "SELECT * FROM supplier_view").status == "ok"


def test_nonfinite_json_parameters_rejected():
    plan = RulePlanner().generate("What happens if demand increases by 15% at Warehouse 3?", {})
    plan["parameters"]["increase"] = float("nan")
    with pytest.raises(ValueError):
        validate_plan(plan)
