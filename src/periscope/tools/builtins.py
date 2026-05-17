from __future__ import annotations

from langchain_core.tools import StructuredTool

from periscope.tools.clickhouse import (
    ClickHouseConnectClient,
    ClickHouseConnectConfig,
    ClickHouseConnectQueryExecutor,
    ClickHouseQueryTool,
)
from periscope.tools.langchain import build_langchain_tools
from periscope.tools.recording import ToolCallRecorder


def build_builtin_tools(
    *,
    clickhouse_config: ClickHouseConnectConfig | None = None,
    clickhouse_client: ClickHouseConnectClient | None = None,
) -> tuple[ClickHouseQueryTool, ...]:
    clickhouse_executor = ClickHouseConnectQueryExecutor(
        clickhouse_config,
        client=clickhouse_client,
    )
    return (ClickHouseQueryTool(clickhouse_executor),)


def build_builtin_langchain_tools(
    *,
    clickhouse_config: ClickHouseConnectConfig | None = None,
    clickhouse_client: ClickHouseConnectClient | None = None,
    recorder: ToolCallRecorder | None = None,
) -> tuple[StructuredTool, ...]:
    return build_langchain_tools(
        build_builtin_tools(
            clickhouse_config=clickhouse_config,
            clickhouse_client=clickhouse_client,
        ),
        recorder=recorder,
    )
