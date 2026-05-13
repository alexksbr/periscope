from __future__ import annotations

from periscope.tools.clickhouse.models import (
    ClickHouseExecutionError,
    ClickHouseQueryData,
    ClickHouseQueryExecutor,
    ClickHouseQueryInput,
    InvalidClickHouseQuery,
)
from periscope.tools.clickhouse.sql import prepare_clickhouse_select
from periscope.tools.models import EvidenceRef, ToolContext, ToolError, ToolMetadata, ToolResult


class ClickHouseQueryTool:
    name = "clickhouse.query"
    schema_version = "1"
    input_model = ClickHouseQueryInput
    output_model = ClickHouseQueryData
    idempotent = True
    default_timeout_s = 10.0
    max_timeout_s = 30.0

    def __init__(self, executor: ClickHouseQueryExecutor) -> None:
        self._executor = executor

    async def execute(
        self,
        arguments: ClickHouseQueryInput,
        context: ToolContext,
    ) -> ToolResult[ClickHouseQueryData]:
        fetch_limit = arguments.limit + 1
        try:
            executed_sql = prepare_clickhouse_select(arguments.sql, fetch_limit)
        except InvalidClickHouseQuery as exc:
            return _error_result(
                context=context,
                code="invalid_sql",
                message="clickhouse.query only accepts a single SELECT statement",
                retryable=False,
                detail={"reason": exc.reason},
            )

        try:
            execution = await self._executor.execute(executed_sql)
        except ClickHouseExecutionError as exc:
            return _error_result(
                context=context,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                detail=exc.detail,
            )

        rows = execution.rows[: arguments.limit]
        truncated = len(execution.rows) > arguments.limit
        data = ClickHouseQueryData(
            query_id=execution.query_id,
            columns=execution.columns,
            rows=rows,
            row_count=len(rows),
            limit=arguments.limit,
            truncated=truncated,
            elapsed_ms=execution.elapsed_ms,
        )
        return ToolResult[ClickHouseQueryData](
            status="ok",
            data=data,
            evidence=[
                EvidenceRef(
                    evidence_id=f"{context.tool_call_id}:clickhouse.query",
                    source="clickhouse.query",
                    title=f"ClickHouse query {execution.query_id}",
                    detail={
                        "query_id": execution.query_id,
                        "row_count": len(rows),
                        "limit": arguments.limit,
                        "truncated": truncated,
                    },
                )
            ],
            metadata=_metadata(context),
        )


def _error_result(
    *,
    context: ToolContext,
    code: str,
    message: str,
    retryable: bool,
    detail: dict[str, object] | None = None,
) -> ToolResult[ClickHouseQueryData]:
    return ToolResult[ClickHouseQueryData](
        status="error",
        error=ToolError(
            code=code,
            message=message,
            retryable=retryable,
            detail=detail or {},
        ),
        metadata=_metadata(context),
    )


def _metadata(context: ToolContext) -> ToolMetadata:
    return ToolMetadata(
        tool_name=ClickHouseQueryTool.name,
        schema_version=ClickHouseQueryTool.schema_version,
        tool_call_id=context.tool_call_id,
    )
