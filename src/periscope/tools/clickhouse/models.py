from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

MAX_CLICKHOUSE_QUERY_LIMIT = 1000


class ClickHouseQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)
    limit: int = Field(default=100, ge=1, le=MAX_CLICKHOUSE_QUERY_LIMIT)


class ClickHouseColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str | None = None


class ClickHouseQueryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    columns: list[ClickHouseColumn] = Field(default_factory=list)
    rows: list[dict[str, object]]
    row_count: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_CLICKHOUSE_QUERY_LIMIT)
    truncated: bool
    elapsed_ms: float | None = Field(default=None, ge=0)


class ClickHouseQueryExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    columns: list[ClickHouseColumn] = Field(default_factory=list)
    rows: list[dict[str, object]]
    elapsed_ms: float | None = Field(default=None, ge=0)


class ClickHouseConnectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8123, ge=1, le=65535)
    database: str = "periscope"
    username: str | None = "periscope"
    password: str = "periscope"
    secure: bool = False
    connect_timeout_s: float = Field(default=10.0, gt=0)
    request_timeout_s: float = Field(default=10.0, gt=0)
    readonly: bool = True


class ClickHouseQueryExecutor(Protocol):
    async def execute(self, sql: str) -> ClickHouseQueryExecution: ...


class ClickHouseConnectClient(Protocol):
    async def query(
        self,
        query: str | None = None,
        *,
        settings: dict[str, Any] | None = None,
        transport_settings: dict[str, str] | None = None,
        column_oriented: bool | None = None,
        use_none: bool | None = None,
    ) -> Any: ...

    async def close(self) -> None: ...


class ClickHouseExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "clickhouse_error",
        retryable: bool = False,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.detail = detail or {}


class InvalidClickHouseQuery(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
