from __future__ import annotations

from periscope.tools import build_tool_registry
from periscope.tools.clickhouse import ClickHouseConnectConfig, ClickHouseQueryTool


def test_build_tool_registry_registers_clickhouse_query() -> None:
    registry = build_tool_registry()

    assert registry.names() == ("clickhouse.query",)
    assert registry.get("clickhouse.query").name == ClickHouseQueryTool.name


def test_build_tool_registry_exposes_clickhouse_input_schema() -> None:
    registry = build_tool_registry()

    schema = registry.input_schema("clickhouse.query")

    assert schema["properties"]["sql"]["type"] == "string"
    assert schema["properties"]["limit"]["default"] == 100
    assert schema["properties"]["limit"]["maximum"] == 1000


def test_build_tool_registry_accepts_clickhouse_config() -> None:
    registry = build_tool_registry(
        clickhouse_config=ClickHouseConnectConfig(
            host="clickhouse",
            database="periscope",
            username="periscope",
            password="periscope",
        )
    )

    assert registry.names() == ("clickhouse.query",)
