from __future__ import annotations

import time

import pytest

from periscope.tools import ToolContext, ToolRunner, build_tool_registry
from periscope.tools.clickhouse import (
    ClickHouseConnectQueryExecutor,
    ClickHouseExecutionError,
    ClickHouseQueryData,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_clickhouse_tool_executes_real_query_and_reports_truncation() -> None:
    runner = await _clickhouse_runner()

    result = await runner.run(
        "clickhouse.query",
        {
            "sql": "SELECT number AS value FROM numbers(3) ORDER BY number",
            "limit": 2,
        },
        _context("real-select"),
    )

    assert result.status == "ok"
    assert isinstance(result.data, ClickHouseQueryData)
    assert result.data.rows == [{"value": 0}, {"value": 1}]
    assert result.data.row_count == 2
    assert result.data.limit == 2
    assert result.data.truncated is True
    assert result.data.elapsed_ms is not None
    assert result.evidence[0].source == "clickhouse.query"
    assert result.evidence[0].detail["row_count"] == 2
    assert result.evidence[0].detail["limit"] == 2
    assert result.evidence[0].detail["truncated"] is True


async def test_clickhouse_tool_executes_real_cte_query() -> None:
    runner = await _clickhouse_runner()

    result = await runner.run(
        "clickhouse.query",
        {"sql": "WITH recent AS (SELECT 1 AS value) SELECT value FROM recent"},
        _context("real-cte"),
    )

    assert result.status == "ok"
    assert isinstance(result.data, ClickHouseQueryData)
    assert result.data.rows == [{"value": 1}]
    assert result.data.truncated is False


async def test_clickhouse_tool_maps_real_query_errors() -> None:
    runner = await _clickhouse_runner()

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT * FROM periscope.__periscope_missing_tool_integration_table__"},
        _context("real-query-error"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "clickhouse_query_error"
    assert result.error.retryable is False
    assert result.error.detail["exception_type"] == "DatabaseError"


async def _clickhouse_runner() -> ToolRunner:
    await _require_clickhouse()
    return ToolRunner(build_tool_registry())


async def _require_clickhouse() -> None:
    try:
        await ClickHouseConnectQueryExecutor().execute("SELECT 1")
    except ClickHouseExecutionError as exc:
        raise AssertionError(
            "ClickHouse is not reachable on 127.0.0.1:8123. "
            "Run `docker compose up -d clickhouse` before "
            "`pytest -m integration tests/integration/test_clickhouse_tool.py`."
        ) from exc


def _context(name: str) -> ToolContext:
    return ToolContext(
        investigation_id="clickhouse-tool-integration",
        tool_call_id=f"{name}-{time.time_ns()}",
        requested_timeout_s=10.0,
    )
