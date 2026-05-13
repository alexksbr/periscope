from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from periscope.tools.base import Tool
from periscope.tools.models import ToolContext, ToolError, ToolMetadata, ToolResult
from periscope.tools.recording import NoopToolCallRecorder, ToolCallRecord, ToolCallRecorder
from periscope.tools.registry import ToolRegistry, UnknownToolError

MAX_OUTPUT_PREVIEW_CHARS = 1000


class ToolRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        recorder: ToolCallRecorder | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._registry = registry
        self._recorder = recorder or NoopToolCallRecorder()
        self._tracer = tracer or trace.get_tracer(__name__)

    async def run(
        self,
        tool_name: str,
        arguments: object,
        context: ToolContext,
    ) -> ToolResult[Any]:
        started_at = time.monotonic()

        with self._tracer.start_as_current_span("periscope.tool.execute") as span:
            _set_initial_span_attributes(span, tool_name, context)
            try:
                result, normalized_input = await self._run_once(
                    tool_name=tool_name,
                    arguments=arguments,
                    context=context,
                    started_at=started_at,
                )
            except asyncio.CancelledError:
                _set_cancelled_span_attributes(span, context)
                raise

            _set_result_span_attributes(span, result)
            await self._record_result(context, result, normalized_input)
            return result

    async def _run_once(
        self,
        *,
        tool_name: str,
        arguments: object,
        context: ToolContext,
        started_at: float,
    ) -> tuple[ToolResult[Any], dict[str, object] | None]:
        normalized_input: dict[str, object] | None = None

        try:
            tool = self._registry.get(tool_name)
        except UnknownToolError:
            return (
                _error_result(
                    tool_name=tool_name,
                    schema_version="unknown",
                    context=context,
                    started_at=started_at,
                    timeout_s=None,
                    error=ToolError(
                        code="unknown_tool",
                        message=f"unknown tool: {tool_name}",
                        retryable=False,
                    ),
                ),
                normalized_input,
            )

        timeout_s = _resolve_timeout(tool, context)

        if not tool.idempotent and context.idempotency_key is None:
            return (
                _error_result(
                    tool_name=tool.name,
                    schema_version=tool.schema_version,
                    context=context,
                    started_at=started_at,
                    timeout_s=timeout_s,
                    error=ToolError(
                        code="idempotency_key_required",
                        message=f"tool {tool.name} requires an idempotency key",
                        retryable=False,
                    ),
                ),
                normalized_input,
            )

        try:
            parsed_arguments = tool.input_model.model_validate(arguments)
            normalized_input = _normalized_input(parsed_arguments)
        except ValidationError as exc:
            return (
                _error_result(
                    tool_name=tool.name,
                    schema_version=tool.schema_version,
                    context=context,
                    started_at=started_at,
                    timeout_s=timeout_s,
                    error=ToolError(
                        code="validation_error",
                        message=f"invalid arguments for tool {tool.name}",
                        retryable=False,
                        detail={
                            "errors": exc.errors(
                                include_context=False,
                                include_input=False,
                                include_url=False,
                            )
                        },
                    ),
                ),
                normalized_input,
            )

        try:
            async with asyncio.timeout(timeout_s):
                result = await tool.execute(parsed_arguments, context)
        except TimeoutError:
            return (
                _error_result(
                    tool_name=tool.name,
                    schema_version=tool.schema_version,
                    context=context,
                    started_at=started_at,
                    timeout_s=timeout_s,
                    error=ToolError(
                        code="timeout",
                        message=f"tool {tool.name} timed out after {timeout_s:.3f}s",
                        retryable=True,
                    ),
                ),
                normalized_input,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return (
                _error_result(
                    tool_name=tool.name,
                    schema_version=tool.schema_version,
                    context=context,
                    started_at=started_at,
                    timeout_s=timeout_s,
                    error=ToolError(
                        code="execution_error",
                        message=f"tool {tool.name} failed during execution",
                        retryable=False,
                        detail={"exception_type": type(exc).__name__},
                    ),
                ),
                normalized_input,
            )

        return (
            result.model_copy(
                update={
                    "metadata": result.metadata.model_copy(
                        update={
                            "tool_name": tool.name,
                            "schema_version": tool.schema_version,
                            "tool_call_id": context.tool_call_id,
                            "duration_ms": _duration_ms(started_at),
                            "timeout_s": timeout_s,
                        }
                    )
                }
            ),
            normalized_input,
        )

    async def _record_result(
        self,
        context: ToolContext,
        result: ToolResult[Any],
        normalized_input: dict[str, object] | None,
    ) -> None:
        record = _tool_call_record(context, result, normalized_input)
        try:
            await self._recorder.record(record)
        except Exception:
            return


def _resolve_timeout(tool: Tool[Any, Any], context: ToolContext) -> float:
    if context.requested_timeout_s is None:
        return tool.default_timeout_s
    return min(context.requested_timeout_s, tool.max_timeout_s)


def _error_result(
    *,
    tool_name: str,
    schema_version: str,
    context: ToolContext,
    started_at: float,
    timeout_s: float | None,
    error: ToolError,
) -> ToolResult[Any]:
    return ToolResult[Any](
        status="error",
        error=error,
        metadata=ToolMetadata(
            tool_name=tool_name,
            schema_version=schema_version,
            tool_call_id=context.tool_call_id,
            duration_ms=_duration_ms(started_at),
            timeout_s=timeout_s,
        ),
    )


def _duration_ms(started_at: float) -> float:
    return (time.monotonic() - started_at) * 1000


def _normalized_input(arguments: BaseModel) -> dict[str, object]:
    return cast(dict[str, object], arguments.model_dump(mode="json"))


def _tool_call_record(
    context: ToolContext,
    result: ToolResult[Any],
    normalized_input: dict[str, object] | None,
) -> ToolCallRecord:
    output_preview, output_preview_truncated = _output_preview(result)
    return ToolCallRecord(
        investigation_id=context.investigation_id,
        tool_call_id=context.tool_call_id,
        tool_name=result.metadata.tool_name,
        schema_version=result.metadata.schema_version,
        normalized_input=normalized_input,
        status=result.status,
        error=result.error,
        evidence=result.evidence,
        output_preview=output_preview,
        output_preview_truncated=output_preview_truncated,
        duration_ms=result.metadata.duration_ms or 0,
        timeout_s=result.metadata.timeout_s,
        attempt_count=result.metadata.attempt_count,
    )


def _output_preview(result: ToolResult[Any]) -> tuple[str | None, bool]:
    if result.data is None:
        return None, False
    try:
        preview = result.data.model_dump_json()
    except PydanticSerializationError:
        return None, False
    if len(preview) <= MAX_OUTPUT_PREVIEW_CHARS:
        return preview, False
    return preview[: MAX_OUTPUT_PREVIEW_CHARS - 3] + "...", True


def _set_initial_span_attributes(span: Span, tool_name: str, context: ToolContext) -> None:
    span.set_attribute("periscope.tool.name", tool_name)
    span.set_attribute("periscope.tool.call_id", context.tool_call_id)
    span.set_attribute("periscope.investigation.id", context.investigation_id)
    if context.requested_timeout_s is not None:
        span.set_attribute("periscope.tool.requested_timeout_s", context.requested_timeout_s)
    span.set_attribute(
        "periscope.tool.idempotency_key_present",
        context.idempotency_key is not None,
    )


def _set_result_span_attributes(span: Span, result: ToolResult[Any]) -> None:
    span.set_attribute("periscope.tool.name", result.metadata.tool_name)
    span.set_attribute("periscope.tool.schema_version", result.metadata.schema_version)
    span.set_attribute("periscope.tool.status", result.status)
    span.set_attribute("periscope.tool.duration_ms", result.metadata.duration_ms or 0)
    span.set_attribute("periscope.tool.evidence_count", len(result.evidence))
    span.set_attribute("periscope.tool.attempt_count", result.metadata.attempt_count)
    if result.metadata.timeout_s is not None:
        span.set_attribute("periscope.tool.timeout_s", result.metadata.timeout_s)

    if result.error is None:
        span.set_status(Status(StatusCode.OK))
        return

    span.set_attribute("periscope.tool.error_code", result.error.code)
    span.set_attribute("periscope.tool.error_retryable", result.error.retryable)
    span.set_status(Status(StatusCode.ERROR, result.error.code))


def _set_cancelled_span_attributes(span: Span, context: ToolContext) -> None:
    span.set_attribute("periscope.tool.call_id", context.tool_call_id)
    span.set_attribute("periscope.tool.status", "cancelled")
    span.set_status(Status(StatusCode.ERROR, "cancelled"))
