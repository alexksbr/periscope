from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import StructuredTool

from periscope.qx.models import (
    QxCompileResult,
    QxError,
    QxQuestion,
    QxSchemaSnapshot,
    QxSqlCandidate,
)
from periscope.tools.clickhouse import ClickHouseQueryInput
from periscope.tools.models import ToolContext

DEFAULT_QX_SQL_SYSTEM_PROMPT = """\
You generate ClickHouse SQL for observability questions.
Return only JSON matching the requested schema.
Use only tables and columns listed in the schema context.
Do not execute SQL.
"""


class ToolRunnerLike(Protocol):
    async def run(
        self,
        tool_name: str,
        arguments: object,
        context: ToolContext,
    ) -> object: ...


class LangChainQxCompiler:
    def __init__(
        self,
        model: BaseChatModel,
        *,
        system_prompt: str = DEFAULT_QX_SQL_SYSTEM_PROMPT,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._parser: PydanticOutputParser[QxSqlCandidate] = PydanticOutputParser(
            pydantic_object=QxSqlCandidate
        )
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._system_prompt),
                (
                    "human",
                    "Question:\n{question}\n\n"
                    "Schema:\n{schema_context}\n\n"
                    "Output instructions:\n{format_instructions}",
                ),
            ]
        )

    async def compile(
        self,
        question: QxQuestion,
        schema: QxSchemaSnapshot,
    ) -> QxCompileResult:
        chain = self._prompt | self._model | self._parser
        try:
            candidate = cast(
                QxSqlCandidate,
                await chain.ainvoke(
                    {
                        "question": question.question,
                        "schema_context": render_schema_context(schema),
                        "format_instructions": self._parser.get_format_instructions(),
                    }
                ),
            )
        except OutputParserException as exc:
            return QxCompileResult(
                status="error",
                question=question,
                error=QxError(
                    code="langchain_output_parse_error",
                    message="LangChain compiler returned invalid QX SQL candidate JSON",
                    retryable=False,
                    detail={"exception_type": type(exc).__name__},
                ),
            )
        return QxCompileResult(status="ok", question=question, candidate=candidate)


def build_clickhouse_query_langchain_tool(
    tool_runner: ToolRunnerLike,
    *,
    context_factory: Callable[[], ToolContext],
) -> StructuredTool:
    async def _run_clickhouse_query(sql: str, limit: int = 100) -> dict[str, object]:
        result = await tool_runner.run(
            "clickhouse.query",
            {"sql": sql, "limit": limit},
            context_factory(),
        )
        if not hasattr(result, "model_dump"):
            return {
                "status": "error",
                "error": {
                    "code": "langchain_tool_protocol_error",
                    "message": "Periscope tool runner returned an unexpected payload",
                    "retryable": False,
                },
            }
        return cast(dict[str, object], result.model_dump(mode="json"))

    return StructuredTool.from_function(
        coroutine=_run_clickhouse_query,
        name="clickhouse_query",
        description=(
            "Execute a guarded read-only ClickHouse SELECT through Periscope clickhouse.query."
        ),
        args_schema=ClickHouseQueryInput,
    )


def render_schema_context(schema: QxSchemaSnapshot) -> str:
    if not schema.tables:
        return f"database: {schema.database}\ntables: none"
    lines = [f"database: {schema.database}", "tables:"]
    for table in schema.tables:
        columns = ", ".join(f"{column.name} {column.type}" for column in table.columns)
        lines.append(f"- {table.database}.{table.name}: {columns}")
    return "\n".join(lines)
