from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from periscope.qx.langchain import (
    LangChainQxCompiler,
    render_schema_context,
)
from periscope.qx.models import (
    QxColumnSchema,
    QxQuestion,
    QxSchemaSnapshot,
    QxTableSchema,
)


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
