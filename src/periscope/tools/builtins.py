from __future__ import annotations

from periscope.tools.clickhouse import (
    ClickHouseConnectClient,
    ClickHouseConnectConfig,
    ClickHouseConnectQueryExecutor,
    ClickHouseQueryTool,
)
from periscope.tools.registry import ToolRegistry


def build_tool_registry(
    *,
    clickhouse_config: ClickHouseConnectConfig | None = None,
    clickhouse_client: ClickHouseConnectClient | None = None,
) -> ToolRegistry:
    clickhouse_executor = ClickHouseConnectQueryExecutor(
        clickhouse_config,
        client=clickhouse_client,
    )
    return ToolRegistry(
        [
            ClickHouseQueryTool(clickhouse_executor),
        ]
    )
