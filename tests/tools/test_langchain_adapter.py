from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any

import pytest
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel
from typing_extensions import TypedDict

from periscope.tools import (
    DuplicateToolNameError,
    EvidenceRef,
    InvalidToolDefinitionError,
    ToolCallRecord,
    ToolContext,
    ToolMetadata,
    ToolResult,
    build_langchain_tool,
    build_langchain_tools,
    invoke_langchain_tool,
    periscope_tool_result_from_message,
)


class RunnerInput(BaseModel):
    message: str


class RunnerOutput(BaseModel):
    echoed: str


class BinaryOutput(BaseModel):
    payload: bytes


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]


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
async def test_langchain_adapter_validates_arguments_and_executes_tool() -> None:
    tool = RecordingTool()
    result = await _invoke_result(
        build_langchain_tool(tool),
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


def test_langchain_tools_reject_duplicate_names() -> None:
    with pytest.raises(DuplicateToolNameError, match=r"tool already registered: test\.record"):
        build_langchain_tools([RecordingTool(), RecordingTool()])


def test_langchain_tool_rejects_tool_names_without_domain() -> None:
    tool = RecordingTool()
    tool.name = "record"

    with pytest.raises(InvalidToolDefinitionError, match="invalid tool name: record"):
        build_langchain_tool(tool)


def test_langchain_tool_rejects_invalid_timeout_contract() -> None:
    tool = RecordingTool()
    tool.default_timeout_s = 10.0

    with pytest.raises(
        InvalidToolDefinitionError,
        match="default timeout cannot exceed max timeout",
    ):
        build_langchain_tool(tool)


@pytest.mark.asyncio
async def test_langchain_adapter_rejects_non_idempotent_tool_without_idempotency_key() -> None:
    tool = MutatingTool()

    result = await _invoke_result(
        build_langchain_tool(tool),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "idempotency_key_required"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_langchain_adapter_allows_non_idempotent_tool_with_idempotency_key() -> None:
    tool = MutatingTool()

    result = await _invoke_result(
        build_langchain_tool(tool),
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
async def test_langchain_adapter_converts_timeout_to_retryable_tool_error() -> None:
    result = await _invoke_result(
        build_langchain_tool(SlowTool()),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.error.retryable is True
    assert result.metadata.timeout_s == 0.01


@pytest.mark.asyncio
async def test_langchain_adapter_converts_unexpected_exception_to_tool_error() -> None:
    result = await _invoke_result(
        build_langchain_tool(FailingTool()),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "execution_error"
    assert result.error.retryable is False
    assert result.error.detail == {"exception_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_langchain_adapter_propagates_cancellation() -> None:
    with pytest.raises(asyncio.CancelledError):
        await invoke_langchain_tool(
            build_langchain_tool(CancellingTool()),
            arguments={"message": "hello"},
            context=ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
        )


@pytest.mark.asyncio
async def test_langchain_adapter_records_compact_success_record() -> None:
    recorder = ListRecorder()

    result = await _invoke_result(
        build_langchain_tool(EvidenceTool(), recorder=recorder),
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
async def test_langchain_adapter_bounds_recorded_output_preview() -> None:
    recorder = ListRecorder()

    await _invoke_result(
        build_langchain_tool(LargeOutputTool(), recorder=recorder),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    record = recorder.records[0]
    assert record.output_preview is not None
    assert len(record.output_preview) == 1000
    assert record.output_preview.endswith("...")
    assert record.output_preview_truncated is True


@pytest.mark.asyncio
async def test_langchain_adapter_omits_unserializable_output_preview_without_failing() -> None:
    recorder = ListRecorder()

    result = await _invoke_result(
        build_langchain_tool(BinaryOutputTool(), recorder=recorder),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
        BinaryOutput,
    )

    assert result.status == "ok"
    assert result.data == BinaryOutput(payload=b"\xff")
    assert len(recorder.records) == 1
    assert recorder.records[0].output_preview is None
    assert recorder.records[0].output_preview_truncated is False


@pytest.mark.asyncio
async def test_langchain_adapter_ignores_recorder_failures() -> None:
    result = await _invoke_result(
        build_langchain_tool(RecordingTool(), recorder=FailingRecorder()),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    assert result.status == "ok"
    assert result.data == RunnerOutput(echoed="hello")


@pytest.mark.asyncio
async def test_langchain_adapter_emits_tool_span_attributes() -> None:
    tracer_provider = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    await _invoke_result(
        build_langchain_tool(
            EvidenceTool(),
            tracer=tracer_provider.get_tracer("periscope-test"),
        ),
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
async def test_langchain_adapter_emits_error_span_attributes() -> None:
    tracer_provider = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    await _invoke_result(
        build_langchain_tool(
            FailingTool(),
            tracer=tracer_provider.get_tracer("periscope-test"),
        ),
        {"message": "hello"},
        ToolContext(investigation_id="inv-1", tool_call_id="call-1"),
    )

    span = exporter.get_finished_spans()[0]
    attributes = span.attributes
    assert attributes["periscope.tool.status"] == "error"
    assert attributes["periscope.tool.error_code"] == "execution_error"
    assert attributes["periscope.tool.error_retryable"] is False
    assert span.status.status_code == StatusCode.ERROR


@pytest.mark.asyncio
async def test_tool_node_preserves_periscope_tool_result_as_artifact() -> None:
    graph = _compile_tool_graph(build_langchain_tool(RecordingTool()))

    result = await graph.ainvoke(
        _tool_call(message="hello"),
        config={
            "configurable": {
                "periscope_tool_context": {
                    "investigation_id": "inv-1",
                    "tool_call_id": "call-1",
                }
            }
        },
    )

    message = result["messages"][-1]
    artifact = message.artifact

    assert message.content == "test.record ok"
    assert message.tool_call_id == "call-1"
    assert artifact["status"] == "ok"
    assert artifact["data"]["echoed"] == "hello"
    assert artifact["metadata"]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_tool_node_schema_validation_happens_before_periscope_contract() -> None:
    graph = _compile_tool_graph(build_langchain_tool(RecordingTool()))

    result = await graph.ainvoke(
        _tool_call(arguments={"wrong": "shape"}),
        config={
            "configurable": {
                "periscope_tool_context": {
                    "investigation_id": "inv-1",
                    "tool_call_id": "call-1",
                }
            }
        },
    )

    message = result["messages"][-1]

    assert message.status == "error"
    assert "Error invoking tool 'test_record'" in message.content
    assert not hasattr(message, "artifact") or message.artifact is None


async def _invoke_result[DataT: BaseModel](
    tool: StructuredTool,
    arguments: dict[str, object],
    context: ToolContext,
    data_model: type[DataT] = RunnerOutput,
) -> ToolResult[DataT]:
    message = await invoke_langchain_tool(tool, arguments=arguments, context=context)
    return periscope_tool_result_from_message(message, data_model)


def _compile_tool_graph(tool: StructuredTool) -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


def _tool_call(
    *,
    message: str = "hello",
    arguments: dict[str, object] | None = None,
) -> dict[str, list[AnyMessage]]:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "test_record",
                        "args": arguments or {"message": message},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }
