from __future__ import annotations

import pytest
from pydantic import ValidationError

from periscope.qx import QxCompileResult, QxError, QxQuestion, QxSchemaRequest, QxSqlCandidate


def test_qx_question_strips_whitespace() -> None:
    question = QxQuestion(question="  p99 latency by route  ")

    assert question.question == "p99 latency by route"


def test_qx_question_rejects_blank_text() -> None:
    with pytest.raises(ValidationError, match="question cannot be blank"):
        QxQuestion(question="   ")


def test_qx_schema_request_deduplicates_table_filters() -> None:
    request = QxSchemaRequest(include_tables=["otel_traces", "otel_traces", "otel_logs"])

    assert request.include_tables == ["otel_traces", "otel_logs"]


def test_qx_compile_result_requires_candidate_for_ok_status() -> None:
    with pytest.raises(ValidationError, match="ok QX compile results must include a candidate"):
        QxCompileResult(status="ok", question=QxQuestion(question="errors by service"))


def test_qx_compile_result_requires_error_for_error_status() -> None:
    with pytest.raises(ValidationError, match="error QX compile results must include an error"):
        QxCompileResult(status="error", question=QxQuestion(question="errors by service"))


def test_qx_compile_result_accepts_sql_candidate() -> None:
    result = QxCompileResult(
        status="ok",
        question=QxQuestion(question="errors by service"),
        candidate=QxSqlCandidate(
            sql="SELECT ServiceName, count() FROM periscope.otel_traces GROUP BY ServiceName",
            referenced_tables=["otel_traces"],
            confidence=0.7,
        ),
    )

    assert result.candidate is not None
    assert result.candidate.dialect == "clickhouse"


def test_qx_compile_result_accepts_error() -> None:
    result = QxCompileResult(
        status="error",
        question=QxQuestion(question="errors by service"),
        error=QxError(
            code="schema_unavailable",
            message="schema unavailable",
            retryable=True,
        ),
    )

    assert result.error is not None
    assert result.error.code == "schema_unavailable"
