from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

import clickhouse_connect  # type: ignore[import-untyped]
import clickhouse_connect.driver.exceptions as clickhouse_exceptions  # type: ignore[import-untyped]

from periscope.tools.clickhouse.models import (
    ClickHouseColumn,
    ClickHouseConnectClient,
    ClickHouseConnectConfig,
    ClickHouseExecutionError,
    ClickHouseQueryExecution,
)

CLICKHOUSE_ERROR_DETAIL_PREVIEW_CHARS = 1000


class ClickHouseConnectQueryExecutor:
    def __init__(
        self,
        config: ClickHouseConnectConfig | None = None,
        *,
        client: ClickHouseConnectClient | None = None,
    ) -> None:
        self._config = config or ClickHouseConnectConfig()
        self._client = client

    async def execute(self, sql: str) -> ClickHouseQueryExecution:
        query_id = str(uuid.uuid4())
        if self._client is not None:
            return await self._query(self._client, sql, query_id)

        try:
            client = await self._create_client()
        except clickhouse_exceptions.OperationalError as exc:
            raise _connect_error(
                "ClickHouse transport error",
                code="clickhouse_transport_error",
                retryable=True,
                exc=exc,
            ) from exc
        except clickhouse_exceptions.DatabaseError as exc:
            raise _connect_error(
                "ClickHouse client creation failed",
                code="clickhouse_query_error",
                retryable=_is_retryable_clickhouse_error(exc),
                exc=exc,
            ) from exc
        except clickhouse_exceptions.ClickHouseError as exc:
            raise _connect_error(
                "ClickHouse client creation failed",
                code="clickhouse_client_error",
                retryable=False,
                exc=exc,
            ) from exc
        try:
            return await self._query(client, sql, query_id)
        finally:
            await client.close()

    async def _create_client(self) -> ClickHouseConnectClient:
        return cast(
            ClickHouseConnectClient,
            await clickhouse_connect.get_async_client(
                host=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,
                database=self._config.database,
                secure=self._config.secure,
                connect_timeout=self._config.connect_timeout_s,
                send_receive_timeout=self._config.request_timeout_s,
                autogenerate_session_id=False,
            ),
        )

    async def _query(
        self,
        client: ClickHouseConnectClient,
        sql: str,
        query_id: str,
    ) -> ClickHouseQueryExecution:
        try:
            result = await client.query(
                sql,
                settings=_clickhouse_settings(self._config),
                transport_settings={"query_id": query_id},
                column_oriented=False,
                use_none=True,
            )
        except clickhouse_exceptions.OperationalError as exc:
            raise _connect_error(
                "ClickHouse transport error",
                code="clickhouse_transport_error",
                retryable=True,
                exc=exc,
            ) from exc
        except clickhouse_exceptions.DatabaseError as exc:
            raise _connect_error(
                "ClickHouse query failed",
                code="clickhouse_query_error",
                retryable=_is_retryable_clickhouse_error(exc),
                exc=exc,
            ) from exc
        except clickhouse_exceptions.ClickHouseError as exc:
            raise _connect_error(
                "ClickHouse client error",
                code="clickhouse_client_error",
                retryable=False,
                exc=exc,
            ) from exc

        return _parse_clickhouse_connect_result(result, fallback_query_id=query_id)


def _clickhouse_settings(config: ClickHouseConnectConfig) -> dict[str, Any]:
    if not config.readonly:
        return {}
    return {"readonly": 1}


def _connect_error(
    message: str,
    *,
    code: str,
    retryable: bool,
    exc: Exception,
) -> ClickHouseExecutionError:
    return ClickHouseExecutionError(
        message,
        code=code,
        retryable=retryable,
        detail={
            "exception_type": type(exc).__name__,
            "message": str(exc)[:CLICKHOUSE_ERROR_DETAIL_PREVIEW_CHARS],
        },
    )


def _is_retryable_clickhouse_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("429", "503", "504", "timeout", "too many"))


def _parse_clickhouse_connect_result(
    result: object,
    *,
    fallback_query_id: str,
) -> ClickHouseQueryExecution:
    columns = _parse_connect_columns(result)
    rows = _parse_connect_rows(result)
    elapsed_ms = _parse_connect_elapsed_ms(result)
    query_id = _parse_connect_query_id(result) or fallback_query_id
    return ClickHouseQueryExecution(
        query_id=query_id,
        columns=columns,
        rows=rows,
        elapsed_ms=elapsed_ms,
    )


def _parse_connect_columns(result: object) -> list[ClickHouseColumn]:
    column_names = getattr(result, "column_names", ())
    column_types = getattr(result, "column_types", ())
    if not isinstance(column_names, Sequence) or isinstance(column_names, str):
        raise _protocol_error("result column_names must be a sequence")
    if not isinstance(column_types, Sequence) or isinstance(column_types, str):
        raise _protocol_error("result column_types must be a sequence")
    columns: list[ClickHouseColumn] = []
    for index, name in enumerate(column_names):
        if not isinstance(name, str):
            raise _protocol_error("result column names must be strings")
        column_type = column_types[index] if index < len(column_types) else None
        columns.append(
            ClickHouseColumn(
                name=name,
                type=None if column_type is None else str(column_type),
            )
        )
    return columns


def _parse_connect_rows(result: object) -> list[dict[str, object]]:
    named_results = getattr(result, "named_results", None)
    if not callable(named_results):
        raise _protocol_error("result must expose named_results")
    rows: list[dict[str, object]] = []
    for item in named_results():
        if not isinstance(item, dict):
            raise _protocol_error("result row items must be objects")
        row: dict[str, object] = {}
        for key, row_value in item.items():
            if not isinstance(key, str):
                raise _protocol_error("result row keys must be strings")
            row[key] = row_value
        rows.append(row)
    return rows


def _parse_connect_elapsed_ms(result: object) -> float | None:
    summary = getattr(result, "summary", None)
    if not isinstance(summary, dict):
        return None
    elapsed = summary.get("elapsed")
    if not isinstance(elapsed, int | float) or isinstance(elapsed, bool):
        elapsed_ns = summary.get("elapsed_ns")
        if isinstance(elapsed_ns, int | float) and not isinstance(elapsed_ns, bool):
            return float(elapsed_ns) / 1_000_000
        return None
    return float(elapsed) * 1000


def _parse_connect_query_id(result: object) -> str | None:
    query_id = getattr(result, "query_id", None)
    if isinstance(query_id, str) and query_id:
        return query_id
    return None


def _protocol_error(message: str) -> ClickHouseExecutionError:
    return ClickHouseExecutionError(
        message,
        code="clickhouse_protocol_error",
        retryable=False,
    )
