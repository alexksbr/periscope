from __future__ import annotations

import time

import pytest

from periscope.qx import ClickHouseSchemaProvider, QxSchemaRequest
from periscope.tools import ToolContext, build_builtin_langchain_tools
from periscope.tools.clickhouse import ClickHouseConnectQueryExecutor, ClickHouseExecutionError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_clickhouse_schema_provider_reads_real_system_columns() -> None:
    await _require_clickhouse()
    provider = ClickHouseSchemaProvider(build_builtin_langchain_tools()[0])

    snapshot = await provider.snapshot(
        QxSchemaRequest(
            database="system",
            include_tables=["columns"],
            max_columns=100,
        ),
        _context(),
    )

    assert snapshot.database == "system"
    assert snapshot.column_count > 0
    assert [table.name for table in snapshot.tables] == ["columns"]
    column_names = {column.name for column in snapshot.tables[0].columns}
    assert {"database", "table", "name", "type"}.issubset(column_names)


async def _require_clickhouse() -> None:
    try:
        await ClickHouseConnectQueryExecutor().execute("SELECT 1")
    except ClickHouseExecutionError as exc:
        raise AssertionError(
            "ClickHouse is not reachable on 127.0.0.1:8123. "
            "Run `docker compose up -d clickhouse` before "
            "`pytest -m integration tests/integration/test_qx_schema.py`."
        ) from exc


def _context() -> ToolContext:
    return ToolContext(
        investigation_id="qx-schema-integration",
        tool_call_id=f"schema-{time.time_ns()}",
        requested_timeout_s=10.0,
    )
