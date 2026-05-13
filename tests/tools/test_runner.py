from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from periscope.tools import (
    EvidenceRef,
    ToolCallRecord,
    ToolContext,
    ToolMetadata,
    ToolRegistry,
    ToolResult,
    ToolRunner,
)


class RunnerInput(BaseModel):
    message: str


class RunnerOutput(BaseModel):
    echoed: str


class BinaryOutput(BaseModel):
    payload: bytes


class RecordingTool:
    name = "test.record"
    schema_version = "1"
    input_model = RunnerInput
    output_model = RunnerOutput
    idempotent = True
    default_timeout_s = 1.0
    max_timeout_s = 2.0

    def __init__(self) -> None:
        self.calls: list[RunnerInput] = []

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        self.calls.append(arguments)
        return ToolResult[RunnerOutput](
            status="ok",
            data=RunnerOutput(echoed=arguments.message),
            metadata=ToolMetadata(
                tool_name="placeholder",
                schema_version="placeholder",
                tool_call_id="placeholder",
            ),
        )


class MutatingTool(RecordingTool):
    name = "test.mutate"
    idempotent = False


class SlowTool(RecordingTool):
    name = "test.slow"
    default_timeout_s = 0.01
    max_timeout_s = 0.01

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        await asyncio.sleep(1)
        return await super().execute(arguments, context)


class FailingTool(RecordingTool):
    name = "test.fail"

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        raise RuntimeError("boom")


class CancellingTool(RecordingTool):
    name = "test.cancel"

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        raise asyncio.CancelledError


class EvidenceTool(RecordingTool):
    name = "test.evidence"

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        return ToolResult[RunnerOutput](
            status="ok",
            data=RunnerOutput(echoed=arguments.message),
            evidence=[
                EvidenceRef(
                    evidence_id="ev-1",
                    source="test",
                    title="test evidence",
                )
            ],
            metadata=ToolMetadata(
                tool_name="placeholder",
                schema_version="placeholder",
                tool_call_id="placeholder",
            ),
        )


class LargeOutputTool(RecordingTool):
    name = "test.large"

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[RunnerOutput]:
        return ToolResult[RunnerOutput](
            status="ok",
            data=RunnerOutput(echoed="x" * 1200),
            metadata=ToolMetadata(
                tool_name="placeholder",
                schema_version="placeholder",
                tool_call_id="placeholder",
            ),
        )


class BinaryOutputTool(RecordingTool):
    name = "test.binary"
    output_model = BinaryOutput

    async def execute(
        self,
        arguments: RunnerInput,
        context: ToolContext,
    ) -> ToolResult[BinaryOutput]:
        return ToolResult[BinaryOutput](
            status="ok",
            data=BinaryOutput(payload=b"\xff"),
            metadata=ToolMetadata(
                tool_name="placeholder",
                schema_version="placeholder",
                tool_call_id="placeholder",
            ),
        )


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    async def record(self, record: ToolCallRecord) -> None:
        self.records.append(record)


class FailingRecorder:
    async def record(self, record: ToolCallRecord) -> None:
        raise RuntimeError("record failed")


@pytest.mark.asyncio
async def test_runner_validates_arguments_and_executes_tool() -> None:
    tool = RecordingTool()
    runner = ToolRunner(ToolRegistry([tool]))

    result = await runner.run(
        "test.record",
        {"message": "hello"},
        ToolContext(
            investigation_id="inv-1",
            tool_call_id="call-1",
            requested_timeout_s=10.0,
        ),
    )

    assert result.status == "ok"
    assert result.data == RunnerOutput(echoed="hello")
    assert tool.calls == [RunnerInput(message="hello")]
    assert result.metadata.tool_name == "test.record"
    assert result.metadata.schema_version == "1"
    assert result.metadata.tool_call_id == "call-1"
    assert result.metadata.timeout_s == 2.0
    assert result.metadata.duration_ms is not None


@pytest.mark.asyncio
async def test_runner_returns_error_for_unknown_tool() -> None:
    runner = ToolRunner(ToolRegistry())

    result = await runner.run(
        "test.missing",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "unknown_tool"
    assert result.error.retryable is False
    assert result.metadata.tool_name == "test.missing"
    assert result.metadata.schema_version == "unknown"


@pytest.mark.asyncio
async def test_runner_returns_validation_error_without_executing_tool() -> None:
    tool = RecordingTool()
    runner = ToolRunner(ToolRegistry([tool]))

    result = await runner.run(
        "test.record",
        {"wrong": "shape"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "validation_error"
    assert result.error.retryable is False
    assert tool.calls == []


@pytest.mark.asyncio
async def test_runner_rejects_non_idempotent_tool_without_idempotency_key() -> None:
    tool = MutatingTool()
    runner = ToolRunner(ToolRegistry([tool]))

    result = await runner.run(
        "test.mutate",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "idempotency_key_required"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_runner_allows_non_idempotent_tool_with_idempotency_key() -> None:
    tool = MutatingTool()
    runner = ToolRunner(ToolRegistry([tool]))

    result = await runner.run(
        "test.mutate",
        {"message": "hello"},
        ToolContext(
            investigation_id="inv-1",
            tool_call_id="call-1",
            idempotency_key="idem-1",
        ),
    )

    assert result.status == "ok"
    assert result.data == RunnerOutput(echoed="hello")
    assert tool.calls == [RunnerInput(message="hello")]


@pytest.mark.asyncio
async def test_runner_converts_timeout_to_retryable_tool_error() -> None:
    runner = ToolRunner(ToolRegistry([SlowTool()]))

    result = await runner.run(
        "test.slow",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.error.retryable is True
    assert result.metadata.timeout_s == 0.01


@pytest.mark.asyncio
async def test_runner_converts_unexpected_exception_to_tool_error() -> None:
    runner = ToolRunner(ToolRegistry([FailingTool()]))

    result = await runner.run(
        "test.fail",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "execution_error"
    assert result.error.retryable is False
    assert result.error.detail == {"exception_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_runner_propagates_cancellation() -> None:
    runner = ToolRunner(ToolRegistry([CancellingTool()]))

    with pytest.raises(asyncio.CancelledError):
        await runner.run(
            "test.cancel",
            {"message": "hello"},
            ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
        )


@pytest.mark.asyncio
async def test_runner_records_compact_success_record() -> None:
    recorder = ListRecorder()
    runner = ToolRunner(ToolRegistry([EvidenceTool()]), recorder=recorder)

    result = await runner.run(
        "test.evidence",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.investigation_id == "inv-1"
    assert record.tool_call_id == "call-1"
    assert record.tool_name == "test.evidence"
    assert record.schema_version == "1"
    assert record.normalized_input == {"message": "hello"}
    assert record.status == "ok"
    assert record.error is None
    assert record.evidence == result.evidence
    assert record.output_preview == '{"echoed":"hello"}'
    assert record.output_preview_truncated is False
    assert record.duration_ms >= 0


@pytest.mark.asyncio
async def test_runner_records_compact_error_record() -> None:
    recorder = ListRecorder()
    runner = ToolRunner(ToolRegistry([RecordingTool()]), recorder=recorder)

    result = await runner.run(
        "test.record",
        {"wrong": "shape"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.normalized_input is None
    assert record.error is not None
    assert record.error.code == "validation_error"
    assert record.output_preview is None


@pytest.mark.asyncio
async def test_runner_bounds_recorded_output_preview() -> None:
    recorder = ListRecorder()
    runner = ToolRunner(ToolRegistry([LargeOutputTool()]), recorder=recorder)

    await runner.run(
        "test.large",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    record = recorder.records[0]
    assert record.output_preview is not None
    assert len(record.output_preview) == 1000
    assert record.output_preview.endswith("...")
    assert record.output_preview_truncated is True


@pytest.mark.asyncio
async def test_runner_omits_unserializable_output_preview_without_failing() -> None:
    recorder = ListRecorder()
    runner = ToolRunner(ToolRegistry([BinaryOutputTool()]), recorder=recorder)

    result = await runner.run(
        "test.binary",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert result.data == BinaryOutput(payload=b"\xff")
    assert len(recorder.records) == 1
    assert recorder.records[0].output_preview is None
    assert recorder.records[0].output_preview_truncated is False


@pytest.mark.asyncio
async def test_runner_ignores_recorder_failures() -> None:
    runner = ToolRunner(ToolRegistry([RecordingTool()]), recorder=FailingRecorder())

    result = await runner.run(
        "test.record",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert result.data == RunnerOutput(echoed="hello")


@pytest.mark.asyncio
async def test_runner_emits_tool_span_attributes() -> None:
    tracer_provider = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    runner = ToolRunner(
        ToolRegistry([EvidenceTool()]),
        tracer=tracer_provider.get_tracer("periscope-test"),
    )

    await runner.run(
        "test.evidence",
        {"message": "hello"},
        ToolContext(
            investigation_id="inv-1",
            tool_call_id="call-1",
            requested_timeout_s=10.0,
        ),
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attributes = span.attributes
    assert span.name == "periscope.tool.execute"
    assert attributes["periscope.tool.name"] == "test.evidence"
    assert attributes["periscope.tool.schema_version"] == "1"
    assert attributes["periscope.tool.call_id"] == "call-1"
    assert attributes["periscope.investigation.id"] == "inv-1"
    assert attributes["periscope.tool.status"] == "ok"
    assert attributes["periscope.tool.timeout_s"] == 2.0
    assert attributes["periscope.tool.evidence_count"] == 1
    assert span.status.status_code == StatusCode.OK


@pytest.mark.asyncio
async def test_runner_emits_error_span_attributes() -> None:
    tracer_provider = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    runner = ToolRunner(
        ToolRegistry([FailingTool()]),
        tracer=tracer_provider.get_tracer("periscope-test"),
    )

    await runner.run(
        "test.fail",
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    span = exporter.get_finished_spans()[0]
    attributes = span.attributes
    assert attributes["periscope.tool.status"] == "error"
    assert attributes["periscope.tool.error_code"] == "execution_error"
    assert attributes["periscope.tool.error_retryable"] is False
    assert span.status.status_code == StatusCode.ERROR
