from __future__ import annotations

from langchain_core.tools import StructuredTool

from periscope.qx.models import (
    QxColumnSchema,
    QxError,
    QxSchemaRequest,
    QxSchemaSnapshot,
    QxTableSchema,
)
from periscope.tools.clickhouse import ClickHouseQueryData
from periscope.tools.langchain import invoke_langchain_tool, periscope_tool_result_from_message
from periscope.tools.models import ToolContext, ToolError


class QxSchemaProviderError(Exception):
    def __init__(self, error: QxError) -> None:
        super().__init__(error.message)
        self.error = error


class ClickHouseSchemaProvider:
    def __init__(self, clickhouse_tool: StructuredTool) -> None:
        self._clickhouse_tool = clickhouse_tool

    async def snapshot(
        self,
        request: QxSchemaRequest,
        context: ToolContext,
    ) -> QxSchemaSnapshot:
        sql = _system_columns_sql(request)
        message = await invoke_langchain_tool(
            self._clickhouse_tool,
            arguments={"sql": sql, "limit": request.max_columns},
            context=context,
        )
        result = periscope_tool_result_from_message(message, ClickHouseQueryData)
        if result.status == "error":
            raise QxSchemaProviderError(_schema_error_from_tool_error(result.error))
        if not isinstance(result.data, ClickHouseQueryData):
            raise QxSchemaProviderError(
                QxError(
                    code="schema_provider_protocol_error",
                    message="clickhouse.query returned an unexpected schema payload",
                    retryable=False,
                )
            )
        return _schema_snapshot_from_rows(
            database=request.database,
            rows=result.data.rows,
            truncated=result.data.truncated,
        )


def _schema_error_from_tool_error(error: ToolError | None) -> QxError:
    if error is None:
        return QxError(
            code="schema_provider_error",
            message="schema provider failed without a typed tool error",
            retryable=False,
        )
    return QxError(
        code=f"schema_provider_{error.code}",
        message=error.message,
        retryable=error.retryable,
        detail=error.detail,
    )


def _system_columns_sql(request: QxSchemaRequest) -> str:
    filters = [f"database = {_clickhouse_string(request.database)}"]
    if request.include_tables:
        tables = ", ".join(_clickhouse_string(table) for table in request.include_tables)
        filters.append(f"table IN ({tables})")
    where = " AND ".join(filters)
    return f"""
        SELECT
            database,
            table AS table_name,
            name AS column_name,
            type AS column_type,
            position
        FROM system.columns
        WHERE {where}
        ORDER BY table_name, position
    """


def _schema_snapshot_from_rows(
    *,
    database: str,
    rows: list[dict[str, object]],
    truncated: bool,
) -> QxSchemaSnapshot:
    tables_by_name: dict[str, list[QxColumnSchema]] = {}
    for row in rows:
        table_name = _row_str(row, "table_name")
        tables_by_name.setdefault(table_name, []).append(
            QxColumnSchema(
                name=_row_str(row, "column_name"),
                type=_row_str(row, "column_type"),
                position=_row_int(row, "position"),
            )
        )
    tables = [
        QxTableSchema(
            database=database,
            name=table_name,
            columns=sorted(columns, key=lambda column: column.position),
        )
        for table_name, columns in sorted(tables_by_name.items())
    ]
    return QxSchemaSnapshot(
        database=database,
        tables=tables,
        column_count=sum(len(table.columns) for table in tables),
        truncated=truncated,
    )


def _row_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise QxSchemaProviderError(
            QxError(
                code="schema_provider_protocol_error",
                message=f"schema row field {key} must be a non-empty string",
                retryable=False,
            )
        )
    return value


def _row_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool):
        value = None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise QxSchemaProviderError(
        QxError(
            code="schema_provider_protocol_error",
            message=f"schema row field {key} must be an integer",
            retryable=False,
        )
    )


def _clickhouse_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
