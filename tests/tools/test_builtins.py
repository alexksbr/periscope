from __future__ import annotations

from periscope.tools import build_builtin_langchain_tools, build_builtin_tools
from periscope.tools.clickhouse import ClickHouseConnectConfig, ClickHouseQueryTool


def test_build_builtin_tools_registers_clickhouse_query() -> None:
    tools = build_builtin_tools()

    assert [tool.name for tool in tools] == ["clickhouse.query"]
    assert tools[0].name == ClickHouseQueryTool.name


def test_build_builtin_langchain_tools_exposes_clickhouse_input_schema() -> None:
    tools = build_builtin_langchain_tools()

    schema = tools[0].args

    assert schema["sql"]["type"] == "string"
    assert schema["limit"]["default"] == 100
    assert schema["limit"]["maximum"] == 1000


def test_build_builtin_tools_accepts_clickhouse_config() -> None:
    tools = build_builtin_tools(
        clickhouse_config=ClickHouseConnectConfig(
            host="clickhouse",
            database="periscope",
            username="periscope",
            password="periscope",
        )
    )

    assert [tool.name for tool in tools] == ["clickhouse.query"]
