from __future__ import annotations

from typing import Any

import pytest

from periscope.qx import ClickHouseSchemaProvider, QxSchemaProviderError, QxSchemaRequest
from periscope.tools.clickhouse import ClickHouseQueryData
from periscope.tools.models import ToolContext, ToolError, ToolMetadata, ToolResult


class FakeToolRunner:
    def __init__(self, result: ToolResult[Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, object, ToolContext]] = []

    async def run(
        self,
        tool_name: str,
        arguments: object,
        context: ToolContext,
    ) -> ToolResult[Any]:
        self.calls.append((tool_name, arguments, context))
        return self.result


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_loads_tables_from_system_columns() -> None:
    runner = FakeToolRunner(
        _ok_result(
            rows=[
                {
                    "database": "periscope",
                    "table_name": "otel_traces",
                    "column_name": "Timestamp",
                    "column_type": "DateTime64(9)",
                    "position": 1,
                },
                {
                    "database": "periscope",
                    "table_name": "otel_traces",
                    "column_name": "ServiceName",
                    "column_type": "String",
                    "position": "2",
                },
            ]
        )
    )

    snapshot = await ClickHouseSchemaProvider(runner).snapshot(
        QxSchemaRequest(include_tables=["otel_traces"]),
        _context(),
    )

    assert runner.calls[0][0] == "clickhouse.query"
    assert runner.calls[0][1] == {
        "sql": (
            "\n        SELECT\n            database,\n            table AS table_name,\n"
            "            name AS column_name,\n            type AS column_type,\n"
            "            position\n        FROM system.columns\n"
            "        WHERE database = 'periscope' AND table IN ('otel_traces')\n"
            "        ORDER BY table_name, position\n    "
        ),
        "limit": 1000,
    }
    assert snapshot.database == "periscope"
    assert snapshot.column_count == 2
    assert [table.name for table in snapshot.tables] == ["otel_traces"]
    assert [column.name for column in snapshot.tables[0].columns] == ["Timestamp", "ServiceName"]


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_preserves_truncation() -> None:
    runner = FakeToolRunner(_ok_result(rows=[], truncated=True))

    snapshot = await ClickHouseSchemaProvider(runner).snapshot(QxSchemaRequest(), _context())

    assert snapshot.truncated is True


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_maps_tool_errors() -> None:
    runner = FakeToolRunner(
        ToolResult[ClickHouseQueryData](
            status="error",
            error=ToolError(
                code="clickhouse_transport_error",
                message="ClickHouse transport error",
                retryable=True,
            ),
            metadata=_metadata(),
        )
    )

    with pytest.raises(QxSchemaProviderError) as exc_info:
        await ClickHouseSchemaProvider(runner).snapshot(QxSchemaRequest(), _context())

    assert exc_info.value.error.code == "schema_provider_clickhouse_transport_error"
    assert exc_info.value.error.retryable is True


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_rejects_unexpected_payload() -> None:
    runner = FakeToolRunner(
        ToolResult[Any](
            status="ok",
            data=None,
            metadata=_metadata(),
        )
    )

    with pytest.raises(QxSchemaProviderError) as exc_info:
        await ClickHouseSchemaProvider(runner).snapshot(QxSchemaRequest(), _context())

    assert exc_info.value.error.code == "schema_provider_protocol_error"


def _ok_result(
    *,
    rows: list[dict[str, object]],
    truncated: bool = False,
) -> ToolResult[ClickHouseQueryData]:
    return ToolResult[ClickHouseQueryData](
        status="ok",
        data=ClickHouseQueryData(
            query_id="query-1",
            rows=rows,
            row_count=len(rows),
            limit=1000,
            truncated=truncated,
        ),
        metadata=_metadata(),
    )


def _metadata() -> ToolMetadata:
    return ToolMetadata(
        tool_name="clickhouse.query",
        schema_version="1",
        tool_call_id="call-1",
    )


def _context() -> ToolContext:
    return ToolContext(
        investigation_id="inv-1",
        tool_call_id="call-1",
    )
