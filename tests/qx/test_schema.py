from __future__ import annotations

import pytest

from periscope.qx import ClickHouseSchemaProvider, QxSchemaProviderError, QxSchemaRequest
from periscope.tools import build_langchain_tool
from periscope.tools.clickhouse import ClickHouseQueryData, ClickHouseQueryInput
from periscope.tools.models import ToolContext, ToolError, ToolMetadata, ToolResult


class FakeClickHouseTool:
    name = "clickhouse.query"
    schema_version = "1"
    input_model = ClickHouseQueryInput
    output_model = ClickHouseQueryData
    idempotent = True
    default_timeout_s = 1.0
    max_timeout_s = 2.0

    def __init__(self, result: ToolResult[ClickHouseQueryData]) -> None:
        self.result = result
        self.calls: list[tuple[ClickHouseQueryInput, ToolContext]] = []

    async def execute(
        self,
        arguments: ClickHouseQueryInput,
        context: ToolContext,
    ) -> ToolResult[ClickHouseQueryData]:
        self.calls.append((arguments, context))
        return self.result


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_loads_tables_from_system_columns() -> None:
    tool = FakeClickHouseTool(
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

    snapshot = await ClickHouseSchemaProvider(build_langchain_tool(tool)).snapshot(
        QxSchemaRequest(include_tables=["otel_traces"]),
        _context(),
    )

    assert tool.calls[0][0].sql == (
        "\n        SELECT\n            database,\n            table AS table_name,\n"
        "            name AS column_name,\n            type AS column_type,\n"
        "            position\n        FROM system.columns\n"
        "        WHERE database = 'periscope' AND table IN ('otel_traces')\n"
        "        ORDER BY table_name, position\n    "
    )
    assert tool.calls[0][0].limit == 1000
    assert snapshot.database == "periscope"
    assert snapshot.column_count == 2
    assert [table.name for table in snapshot.tables] == ["otel_traces"]
    assert [column.name for column in snapshot.tables[0].columns] == ["Timestamp", "ServiceName"]


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_preserves_truncation() -> None:
    tool = FakeClickHouseTool(_ok_result(rows=[], truncated=True))

    snapshot = await ClickHouseSchemaProvider(build_langchain_tool(tool)).snapshot(
        QxSchemaRequest(), _context()
    )

    assert snapshot.truncated is True


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_maps_tool_errors() -> None:
    tool = FakeClickHouseTool(
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
        await ClickHouseSchemaProvider(build_langchain_tool(tool)).snapshot(
            QxSchemaRequest(), _context()
        )

    assert exc_info.value.error.code == "schema_provider_clickhouse_transport_error"
    assert exc_info.value.error.retryable is True


@pytest.mark.asyncio
async def test_clickhouse_schema_provider_rejects_unexpected_payload() -> None:
    tool = FakeClickHouseTool(
        ToolResult[ClickHouseQueryData](
            status="ok",
            data=None,
            metadata=_metadata(),
        )
    )

    with pytest.raises(QxSchemaProviderError) as exc_info:
        await ClickHouseSchemaProvider(build_langchain_tool(tool)).snapshot(
            QxSchemaRequest(), _context()
        )

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
