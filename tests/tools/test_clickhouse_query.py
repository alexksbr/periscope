from __future__ import annotations

import pytest
from clickhouse_connect.driver import exceptions as clickhouse_exceptions

from periscope.tools import ToolContext, ToolRegistry, ToolRunner
from periscope.tools.clickhouse import (
    ClickHouseColumn,
    ClickHouseConnectClient,
    ClickHouseConnectConfig,
    ClickHouseConnectQueryExecutor,
    ClickHouseExecutionError,
    ClickHouseQueryExecution,
    ClickHouseQueryTool,
    InvalidClickHouseQuery,
    prepare_clickhouse_select,
)


class FakeClickHouseExecutor:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.executed_sql: list[str] = []
        self.rows = rows or [{"value": 1}]

    async def execute(self, sql: str) -> ClickHouseQueryExecution:
        self.executed_sql.append(sql)
        return ClickHouseQueryExecution(
            query_id="query-1",
            columns=[ClickHouseColumn(name="value", type="UInt8")],
            rows=self.rows,
            elapsed_ms=12.5,
        )


class FailingClickHouseExecutor:
    def __init__(self) -> None:
        self.executed_sql: list[str] = []

    async def execute(self, sql: str) -> ClickHouseQueryExecution:
        self.executed_sql.append(sql)
        raise ClickHouseExecutionError(
            "ClickHouse is unavailable",
            code="clickhouse_unavailable",
            retryable=True,
            detail={"host": "clickhouse"},
        )


class FakeConnectResult:
    def __init__(
        self,
        *,
        column_names: tuple[str, ...] = ("value",),
        column_types: tuple[object, ...] = ("UInt8",),
        rows: list[dict[str, object]] | None = None,
        query_id: str = "query-1",
        summary: dict[str, object] | None = None,
    ) -> None:
        self.column_names = column_names
        self.column_types = column_types
        self._rows = rows or [{"value": 1}]
        self.query_id = query_id
        self.summary = summary or {"elapsed_ns": 12_500_000}

    def named_results(self) -> list[dict[str, object]]:
        return self._rows


class FakeConnectClient:
    def __init__(
        self,
        result: FakeConnectResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or FakeConnectResult()
        self.error = error
        self.queries: list[str | None] = []
        self.settings: list[dict[str, object] | None] = []
        self.transport_settings: list[dict[str, str] | None] = []
        self.closed = False

    async def query(
        self,
        query: str | None = None,
        *,
        settings: dict[str, object] | None = None,
        transport_settings: dict[str, str] | None = None,
        column_oriented: bool | None = None,
        use_none: bool | None = None,
    ) -> FakeConnectResult:
        self.queries.append(query)
        self.settings.append(settings)
        self.transport_settings.append(transport_settings)
        if self.error is not None:
            raise self.error
        query_id = transport_settings["query_id"] if transport_settings is not None else "query-1"
        return FakeConnectResult(
            column_names=self.result.column_names,
            column_types=self.result.column_types,
            rows=self.result.named_results(),
            query_id=query_id,
            summary=self.result.summary,
        )

    async def close(self) -> None:
        self.closed = True


class ClientCreationFailingExecutor(ClickHouseConnectQueryExecutor):
    async def _create_client(self) -> ClickHouseConnectClient:
        raise clickhouse_exceptions.OperationalError("connection refused")


@pytest.mark.asyncio
async def test_clickhouse_query_executes_guarded_select_and_returns_evidence() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1 AS value", "limit": 50},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert executor.executed_sql == ["SELECT * FROM (SELECT 1 AS value) LIMIT 51"]
    assert result.data is not None
    assert result.data.query_id == "query-1"
    assert result.data.rows == [{"value": 1}]
    assert result.data.row_count == 1
    assert result.data.limit == 50
    assert result.data.truncated is False
    assert result.evidence[0].source == "clickhouse.query"
    assert result.evidence[0].detail == {
        "query_id": "query-1",
        "row_count": 1,
        "limit": 50,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_clickhouse_query_rejects_non_select_sql() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "DROP TABLE periscope.otel_traces"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "invalid_sql"
    assert result.error.detail == {"reason": "statement must start with SELECT"}
    assert executor.executed_sql == []


@pytest.mark.asyncio
async def test_clickhouse_query_rejects_multiple_statements() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1; SELECT 2"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "invalid_sql"
    assert result.error.detail == {"reason": "expected exactly one statement"}
    assert executor.executed_sql == []


@pytest.mark.asyncio
async def test_clickhouse_query_rejects_tokenizer_errors_as_invalid_sql() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 'unterminated"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "invalid_sql"
    assert result.error.detail == {"reason": "invalid SQL syntax"}
    assert executor.executed_sql == []


@pytest.mark.asyncio
async def test_clickhouse_query_rejects_format_clause() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1 FORMAT JSONEachRow"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "invalid_sql"
    assert result.error.detail == {"reason": "FORMAT clauses are controlled by the executor"}
    assert executor.executed_sql == []


@pytest.mark.asyncio
async def test_clickhouse_query_rejects_limit_above_maximum() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1", "limit": 1001},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "validation_error"
    assert executor.executed_sql == []


@pytest.mark.asyncio
async def test_clickhouse_query_caps_rows_returned_by_executor() -> None:
    executor = FakeClickHouseExecutor(rows=[{"value": 1}, {"value": 2}])
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT number AS value FROM numbers(2)", "limit": 1},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert result.data is not None
    assert result.data.rows == [{"value": 1}]
    assert result.data.row_count == 1
    assert result.data.truncated is True


@pytest.mark.asyncio
async def test_clickhouse_query_fetches_extra_row_to_detect_truncation() -> None:
    executor = FakeClickHouseExecutor(rows=[{"value": 1}, {"value": 2}])
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT number AS value FROM numbers(100)", "limit": 1},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert executor.executed_sql == [
        "SELECT * FROM (SELECT number AS value FROM numbers(100)) LIMIT 2"
    ]
    assert result.status == "ok"
    assert result.data is not None
    assert result.data.rows == [{"value": 1}]
    assert result.data.truncated is True
    assert result.evidence[0].detail["truncated"] is True


@pytest.mark.asyncio
async def test_clickhouse_query_maps_expected_executor_errors() -> None:
    executor = FailingClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "clickhouse_unavailable"
    assert result.error.retryable is True
    assert result.error.detail == {"host": "clickhouse"}


def test_prepare_clickhouse_select_allows_semicolon_inside_string_and_final_semicolon() -> None:
    assert (
        prepare_clickhouse_select("SELECT ';' AS value;", 100)
        == "SELECT * FROM (SELECT ';' AS value) LIMIT 100"
    )


def test_prepare_clickhouse_select_ignores_comments_around_statement() -> None:
    assert (
        prepare_clickhouse_select("-- comment\nSELECT 1 /* ok */;", 25)
        == "SELECT * FROM (/* comment */ SELECT 1 /* ok */) LIMIT 25"
    )


def test_prepare_clickhouse_select_allows_format_identifier() -> None:
    assert (
        prepare_clickhouse_select("SELECT format FROM events", 10)
        == "SELECT * FROM (SELECT format FROM events) LIMIT 10"
    )


@pytest.mark.asyncio
async def test_clickhouse_query_accepts_with_cte_select() -> None:
    executor = FakeClickHouseExecutor()
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(executor)]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": ("WITH recent AS (SELECT 1 AS value) SELECT value FROM recent")},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert executor.executed_sql == [
        "SELECT * FROM (WITH recent AS (SELECT 1 AS value) SELECT value FROM recent) LIMIT 101"
    ]


def test_prepare_clickhouse_select_accepts_scalar_with_select() -> None:
    assert (
        prepare_clickhouse_select("WITH 'checkout' AS service SELECT service", 10)
        == "SELECT * FROM (WITH 'checkout' AS service SELECT service) LIMIT 10"
    )


def test_prepare_clickhouse_select_rejects_with_mutating_statement() -> None:
    with pytest.raises(InvalidClickHouseQuery, match="statement must start with SELECT"):
        prepare_clickhouse_select(
            "WITH recent AS (SELECT 1) INSERT INTO target SELECT * FROM recent",
            10,
        )


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_queries_with_readonly_and_query_id() -> None:
    client = FakeConnectClient()
    executor = ClickHouseConnectQueryExecutor(
        ClickHouseConnectConfig(
            host="clickhouse",
            port=8123,
            database="periscope",
            username="periscope",
            password="periscope",
        ),
        client=client,
    )

    execution = await executor.execute("SELECT 1 AS value")

    assert client.queries == ["SELECT 1 AS value"]
    assert client.settings == [{"readonly": 1}]
    assert client.transport_settings[0] is not None
    assert client.transport_settings[0]["query_id"] == execution.query_id
    assert execution.columns == [ClickHouseColumn(name="value", type="UInt8")]
    assert execution.rows == [{"value": 1}]
    assert execution.elapsed_ms == 12.5


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_can_disable_readonly_setting() -> None:
    client = FakeConnectClient()
    executor = ClickHouseConnectQueryExecutor(
        ClickHouseConnectConfig(readonly=False),
        client=client,
    )

    await executor.execute("SELECT 1")

    assert client.settings == [{}]


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_maps_transport_errors() -> None:
    client = FakeConnectClient(error=clickhouse_exceptions.OperationalError("connection failed"))
    executor = ClickHouseConnectQueryExecutor(client=client)

    with pytest.raises(ClickHouseExecutionError) as exc_info:
        await executor.execute("SELECT 1")

    assert exc_info.value.code == "clickhouse_transport_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.detail == {
        "exception_type": "OperationalError",
        "message": "connection failed",
    }


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_maps_client_creation_transport_errors() -> None:
    executor = ClientCreationFailingExecutor()

    with pytest.raises(ClickHouseExecutionError) as exc_info:
        await executor.execute("SELECT 1")

    assert exc_info.value.code == "clickhouse_transport_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.detail == {
        "exception_type": "OperationalError",
        "message": "connection refused",
    }


@pytest.mark.asyncio
async def test_clickhouse_query_reports_client_creation_failure_as_clickhouse_error() -> None:
    runner = ToolRunner(ToolRegistry([ClickHouseQueryTool(ClientCreationFailingExecutor())]))

    result = await runner.run(
        "clickhouse.query",
        {"sql": "SELECT 1"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "clickhouse_transport_error"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_maps_retryable_query_errors() -> None:
    client = FakeConnectClient(error=clickhouse_exceptions.DatabaseError("HTTP status 503"))
    executor = ClickHouseConnectQueryExecutor(client=client)

    with pytest.raises(ClickHouseExecutionError) as exc_info:
        await executor.execute("SELECT 1")

    assert exc_info.value.code == "clickhouse_query_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.detail == {
        "exception_type": "DatabaseError",
        "message": "HTTP status 503",
    }


@pytest.mark.asyncio
async def test_clickhouse_connect_executor_rejects_unexpected_response_shape() -> None:
    class BadResult:
        column_names = ("value",)
        column_types = ("UInt8",)
        query_id = "query-1"

        def __init__(self) -> None:
            self.summary: dict[str, object] = {}

    class BadClient(FakeConnectClient):
        async def query(
            self,
            query: str | None = None,
            *,
            settings: dict[str, object] | None = None,
            transport_settings: dict[str, str] | None = None,
            column_oriented: bool | None = None,
            use_none: bool | None = None,
        ) -> object:
            return BadResult()

    executor = ClickHouseConnectQueryExecutor(client=BadClient())

    with pytest.raises(ClickHouseExecutionError) as exc_info:
        await executor.execute("SELECT 1")

    assert exc_info.value.code == "clickhouse_protocol_error"
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "result must expose named_results"
