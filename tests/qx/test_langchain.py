from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from periscope.qx.langchain import (
    LangChainQxCompiler,
    build_clickhouse_query_langchain_tool,
    render_schema_context,
)
from periscope.qx.models import (
    QxColumnSchema,
    QxQuestion,
    QxSchemaSnapshot,
    QxTableSchema,
)
from periscope.tools.clickhouse import ClickHouseQueryData
from periscope.tools.models import EvidenceRef, ToolContext, ToolMetadata, ToolResult


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
async def test_langchain_qx_compiler_returns_qx_sql_candidate() -> None:
    compiler = LangChainQxCompiler(
        FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=json.dumps(
                        {
                            "sql": "SELECT ServiceName, count() FROM periscope.otel_traces "
                            "GROUP BY ServiceName",
                            "referenced_tables": ["otel_traces"],
                            "assumptions": ["Use ServiceName as the service dimension."],
                            "warnings": [],
                            "confidence": 0.8,
                        }
                    )
                )
            ]
        )
    )

    result = await compiler.compile(QxQuestion(question="errors by service"), _schema())

    assert result.status == "ok"
    assert result.candidate is not None
    assert result.candidate.sql.startswith("SELECT ServiceName")
    assert result.candidate.referenced_tables == ["otel_traces"]
    assert result.candidate.confidence == 0.8


@pytest.mark.asyncio
async def test_langchain_qx_compiler_maps_parse_errors() -> None:
    compiler = LangChainQxCompiler(FakeMessagesListChatModel(responses=[AIMessage(content="nope")]))

    result = await compiler.compile(QxQuestion(question="errors by service"), _schema())

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "langchain_output_parse_error"


@pytest.mark.asyncio
async def test_clickhouse_langchain_tool_wraps_periscope_tool_runner() -> None:
    runner = FakeToolRunner(_tool_result())
    tool = build_clickhouse_query_langchain_tool(runner, context_factory=_context)

    result = await tool.ainvoke({"sql": "SELECT 1 AS value", "limit": 1})

    assert runner.calls[0][0] == "clickhouse.query"
    assert runner.calls[0][1] == {"sql": "SELECT 1 AS value", "limit": 1}
    assert result["status"] == "ok"
    assert result["data"]["rows"] == [{"value": 1}]
    assert result["evidence"][0]["source"] == "clickhouse.query"


def test_render_schema_context_lists_tables_and_columns() -> None:
    assert render_schema_context(_schema()) == (
        "database: periscope\n"
        "tables:\n"
        "- periscope.otel_traces: ServiceName String, StatusCode String"
    )


def _schema() -> QxSchemaSnapshot:
    return QxSchemaSnapshot(
        database="periscope",
        tables=[
            QxTableSchema(
                database="periscope",
                name="otel_traces",
                columns=[
                    QxColumnSchema(name="ServiceName", type="String", position=1),
                    QxColumnSchema(name="StatusCode", type="String", position=2),
                ],
            )
        ],
        column_count=2,
    )


def _tool_result() -> ToolResult[ClickHouseQueryData]:
    return ToolResult[ClickHouseQueryData](
        status="ok",
        data=ClickHouseQueryData(
            query_id="query-1",
            rows=[{"value": 1}],
            row_count=1,
            limit=1,
            truncated=False,
        ),
        evidence=[
            EvidenceRef(
                evidence_id="call-1:clickhouse.query",
                source="clickhouse.query",
                title="ClickHouse query query-1",
            )
        ],
        metadata=ToolMetadata(
            tool_name="clickhouse.query",
            schema_version="1",
            tool_call_id="call-1",
        ),
    )


def _context() -> ToolContext:
    return ToolContext(
        investigation_id="inv-1",
        tool_call_id="call-1",
    )
