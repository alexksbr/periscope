from __future__ import annotations

from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import Tokenizer

from periscope.tools.clickhouse.models import InvalidClickHouseQuery


def prepare_clickhouse_select(sql: str, limit: int) -> str:
    statement = _parse_single_clickhouse_expression(sql)
    if statement is None:
        raise InvalidClickHouseQuery("empty SQL")
    if not isinstance(statement, exp.Select):
        raise InvalidClickHouseQuery("statement must start with SELECT")
    if statement.args.get("format") is not None:
        raise InvalidClickHouseQuery("FORMAT clauses are controlled by the executor")
    statement_sql = statement.sql(dialect="clickhouse")
    return f"SELECT * FROM ({statement_sql}) LIMIT {limit}"


def _parse_single_clickhouse_expression(sql: str) -> exp.Expression | None:
    try:
        statements = [
            cast(exp.Expression, statement)
            for statement in sqlglot.parse(sql, read="clickhouse")
            if statement is not None
        ]
    except (ParseError, TokenError) as exc:
        if _contains_unquoted_token(sql, "outfile"):
            raise InvalidClickHouseQuery("OUTFILE is not allowed") from exc
        raise InvalidClickHouseQuery("invalid SQL syntax") from exc
    if len(statements) > 1:
        raise InvalidClickHouseQuery("expected exactly one statement")
    return statements[0] if statements else None


def _contains_unquoted_token(sql: str, token: str) -> bool:
    try:
        tokens = Tokenizer(dialect="clickhouse").tokenize(sql)
    except TokenError:
        return False
    return any(item.text.lower() == token for item in tokens)
