from __future__ import annotations

import time

import pytest
from langchain_core.tools import StructuredTool

from periscope.tools import (
    ToolContext,
    ToolResult,
    build_builtin_langchain_tools,
    invoke_langchain_tool,
    periscope_tool_result_from_message,
)
from periscope.tools.clickhouse import (
    ClickHouseConnectQueryExecutor,
    ClickHouseExecutionError,
    ClickHouseQueryData,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_clickhouse_tool_executes_real_query_and_reports_truncation() -> None:
    tool = await _clickhouse_tool()

    result = await _invoke_clickhouse_tool(
        tool,
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
    tool = await _clickhouse_tool()

    result = await _invoke_clickhouse_tool(
        tool,
        {"sql": "WITH recent AS (SELECT 1 AS value) SELECT value FROM recent"},
        _context("real-cte"),
    )

    assert result.status == "ok"
    assert isinstance(result.data, ClickHouseQueryData)
    assert result.data.rows == [{"value": 1}]
    assert result.data.truncated is False


async def test_clickhouse_tool_maps_real_query_errors() -> None:
    tool = await _clickhouse_tool()

    result = await _invoke_clickhouse_tool(
        tool,
        {"sql": "SELECT * FROM periscope.__periscope_missing_tool_integration_table__"},
        _context("real-query-error"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "clickhouse_query_error"
    assert result.error.retryable is False
    assert result.error.detail["exception_type"] == "DatabaseError"


async def _clickhouse_tool() -> StructuredTool:
    await _require_clickhouse()
    return build_builtin_langchain_tools()[0]


async def _invoke_clickhouse_tool(
    tool: StructuredTool,
    arguments: dict[str, object],
    context: ToolContext,
) -> ToolResult[ClickHouseQueryData]:
    message = await invoke_langchain_tool(tool, arguments=arguments, context=context)
    return periscope_tool_result_from_message(message, ClickHouseQueryData)


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
