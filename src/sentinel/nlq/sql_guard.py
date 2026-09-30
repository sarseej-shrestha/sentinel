"""Fail closed on SQL outside a deliberately small read-only language."""
from dataclasses import dataclass
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sentinel.config import ROW_LIMIT
from sentinel.nlq.schema import SCHEMA, JOINS


class SQLBlocked(ValueError):
    pass


@dataclass(frozen=True)
class GuardedSQL:
    sql: str
    parameters: dict
    limit: int


def guard_sql(sql, parameters=None, row_limit=ROW_LIMIT):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 12000:
        raise SQLBlocked("Provide one SELECT query of at most 12,000 characters.")
    if not 1 <= row_limit <= ROW_LIMIT:
        raise SQLBlocked(f"Row limit must be between 1 and {ROW_LIMIT}.")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SQLBlocked("Only one read-only SELECT statement is allowed; writes and DDL are blocked.")
        tree = statements[0]
        if len(list(tree.find_all(exp.Select))) != 1 or any(tree.find_all(exp.With, exp.Subquery, exp.Into, exp.Lock, exp.Union, exp.Intersect, exp.Except)):
            raise SQLBlocked("CTEs, nested queries, set operations and SELECT INTO are outside the supported query language.")
        tables = list(tree.find_all(exp.Table))
        if not tables:
            raise SQLBlocked("Queries must read an approved semantic view.")
        for table in tables:
            if not isinstance(table.this, exp.Identifier) or table.db or table.catalog or table.name.lower() not in SCHEMA:
                raise SQLBlocked("Only the four approved semantic views may be read; external functions and tables are blocked.")
        allowed_functions = {"SUM", "AVG", "COUNT", "MIN", "MAX", "COALESCE", "NULLIF", "CAST", "TRY_CAST", "CASE", "IF", "ROUND", "ABS", "AND", "OR"}
        for fn in tree.find_all(exp.Func):
            if fn.sql_name() not in allowed_functions or isinstance(fn, exp.Anonymous):
                raise SQLBlocked(f"Function {fn.sql_name()} is not approved.")
        if len(tables) > 3:
            raise SQLBlocked("At most three semantic views may be joined.")
        qualified = qualify(tree, dialect="duckdb", schema=SCHEMA, infer_schema=False, validate_qualify_columns=True)
        aliases = {t.alias_or_name: t.name.lower() for t in qualified.find_all(exp.Table)}
        allowed_pairs = {frozenset((join["left"], join["right"])) for join in JOINS}
        for join in qualified.find_all(exp.Join):
            condition = join.args.get("on")
            if not isinstance(condition, exp.EQ) or not all(isinstance(v, exp.Column) for v in (condition.left, condition.right)):
                raise SQLBlocked("Joins must use a documented equality key; cross joins are blocked.")
            pair = frozenset(f"{aliases.get(c.table)}.{c.name}" for c in (condition.left, condition.right))
            if pair not in allowed_pairs:
                raise SQLBlocked("This join is not in the semantic catalog.")
        params = parameters or {}
        placeholders = {p.name for p in qualified.find_all(exp.Placeholder)}
        if placeholders != set(params):
            raise SQLBlocked("Named SQL parameters must match the supplied parameter object exactly.")
        if qualified.args.get("offset"):
            raise SQLBlocked("OFFSET is not supported.")
        existing = qualified.args.get("limit")
        if existing:
            value = existing.expression
            if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
                raise SQLBlocked("LIMIT must be a nonnegative integer literal.")
            row_limit = min(row_limit, int(value.this))
        qualified = qualified.limit(row_limit)
        return GuardedSQL(qualified.sql(dialect="duckdb"), params, row_limit)
    except SQLBlocked:
        raise
    except Exception as exc:
        raise SQLBlocked(f"SQL could not be validated: {exc}") from exc
