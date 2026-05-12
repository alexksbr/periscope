from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

import httpx
import pytest

CLICKHOUSE_URL: Final[str] = "http://127.0.0.1:8123/"
OTLP_ENDPOINT: Final[str] = "http://localhost:4317"
SERVICE_NAME: Final[str] = "periscope-chaos"
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_chaos_app_exports_traces_to_clickhouse() -> None:
    run_id = f"otel-roundtrip-{time.time_ns()}"

    _require_clickhouse()
    gen_ai_count_before = _count_gen_ai_chat_spans()

    with _run_chaos_app() as base_url, httpx.Client(base_url=base_url, timeout=5.0) as client:
        health = client.get("/health")
        checkout = client.post(
            "/checkout",
            json={"item_id": "probe", "quantity": 1, "customer_id": run_id},
        )
        assistant = client.post(
            "/assistant/respond",
            json={"prompt": f"why did checkout slow down? {run_id}"},
        )
        failure_rule = client.put(
            "/admin/failures/checkout",
            json={
                "kind": "dependency_error",
                "probability": 1.0,
                "message": run_id,
            },
        )
        failed_checkout = client.post(
            "/checkout",
            json={"item_id": "probe", "quantity": 1, "customer_id": run_id},
        )
        clear_rule = client.delete("/admin/failures/checkout")

        assert health.status_code == 200
        assert checkout.status_code == 200
        assert assistant.status_code == 200
        assert failure_rule.status_code == 200
        assert failed_checkout.status_code == 502
        assert clear_rule.status_code == 204

        rows = _poll_clickhouse_for_roundtrip(run_id, gen_ai_count_before)

    assert any(
        row["SpanName"] == "payment.authorize" and row["payment_customer_id"] == run_id
        for row in rows
    )
    assert any(
        row["SpanName"] == "POST /checkout"
        and row["StatusCode"] == "Error"
        and row["failure_kind"] == "dependency_error"
        for row in rows
    )
    assert any(
        row["SpanName"] == "chaos.dependency.call" and row["StatusCode"] == "Error" for row in rows
    )


def _require_clickhouse() -> None:
    try:
        _query_clickhouse("SELECT 1")
    except httpx.HTTPError as exc:
        raise AssertionError(
            "ClickHouse is not reachable on 127.0.0.1:8123. "
            "Run `docker compose up -d` before `pytest -m integration`."
        ) from exc


@contextmanager
def _run_chaos_app() -> Iterator[str]:
    port = _free_port()
    env = os.environ.copy()
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = OTLP_ENDPOINT

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "chaos.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"

    try:
        _wait_for_app(base_url, process)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_for_app(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            pytest.fail(f"chaos app exited before becoming ready:\n{output}")

        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc

        time.sleep(0.1)

    raise AssertionError(f"chaos app did not become ready at {base_url}") from last_error


def _poll_clickhouse_for_roundtrip(
    run_id: str,
    gen_ai_count_before: int,
) -> list[dict[str, str]]:
    deadline = time.monotonic() + 30

    while time.monotonic() < deadline:
        rows = _query_roundtrip_spans(run_id)
        gen_ai_count = _count_gen_ai_chat_spans()
        if _has_roundtrip_evidence(rows, gen_ai_count, gen_ai_count_before):
            return rows
        time.sleep(1)

    rows = _query_roundtrip_spans(run_id)
    gen_ai_count = _count_gen_ai_chat_spans()
    pytest.fail(
        "timed out waiting for OTLP spans in ClickHouse for "
        f"{run_id}: gen_ai_count={gen_ai_count}, rows={rows}"
    )


def _query_roundtrip_spans(run_id: str) -> list[dict[str, str]]:
    query = f"""
        SELECT
            SpanName,
            StatusCode,
            StatusMessage,
            SpanAttributes['chaos.failure.kind'] AS failure_kind,
            SpanAttributes['chaos.failure.message'] AS failure_message,
            SpanAttributes['payment.customer_id'] AS payment_customer_id
        FROM periscope.otel_traces
        WHERE ServiceName = {clickhouse_quote(SERVICE_NAME)}
          AND (
            SpanAttributes['payment.customer_id'] = {clickhouse_quote(run_id)}
            OR SpanAttributes['chaos.failure.message'] = {clickhouse_quote(run_id)}
            OR StatusMessage = {clickhouse_quote(run_id)}
          )
        ORDER BY Timestamp DESC
        LIMIT 100
        FORMAT JSONEachRow
    """
    response = _query_clickhouse(query)
    return [json.loads(line) for line in response.text.splitlines() if line]


def _has_roundtrip_evidence(
    rows: list[dict[str, str]],
    gen_ai_count: int,
    gen_ai_count_before: int,
) -> bool:
    has_gen_ai = gen_ai_count > gen_ai_count_before
    has_payment = any(row["SpanName"] == "payment.authorize" for row in rows)
    has_checkout_failure = any(
        row["SpanName"] == "POST /checkout" and row["failure_kind"] == "dependency_error"
        for row in rows
    )
    has_dependency_failure = any(
        row["SpanName"] == "chaos.dependency.call" and row["StatusCode"] == "Error" for row in rows
    )
    return has_gen_ai and has_payment and has_checkout_failure and has_dependency_failure


def _count_gen_ai_chat_spans() -> int:
    query = f"""
        SELECT count()
        FROM periscope.otel_traces
        WHERE ServiceName = {clickhouse_quote(SERVICE_NAME)}
          AND SpanName = 'gen_ai.chat'
          AND SpanAttributes['gen_ai.request.model'] = 'periscope-sim-small'
    """
    return int(_query_clickhouse(query).text.strip())


def _query_clickhouse(query: str) -> httpx.Response:
    response = httpx.post(
        CLICKHOUSE_URL,
        params={"database": "periscope"},
        content=query,
        auth=("periscope", "periscope"),
        timeout=10.0,
    )
    response.raise_for_status()
    return response


def clickhouse_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])
