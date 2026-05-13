from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from periscope.tools import (
    DuplicateToolError,
    InvalidToolDefinitionError,
    Tool,
    ToolContext,
    ToolError,
    ToolMetadata,
    ToolRegistry,
    ToolResult,
)


class EchoInput(BaseModel):
    message: str


class EchoOutput(BaseModel):
    echoed: str


class EchoTool:
    name = "test.echo"
    schema_version = "1"
    input_model = EchoInput
    output_model = EchoOutput
    idempotent = True
    default_timeout_s = 1.0
    max_timeout_s = 5.0

    async def execute(
        self,
        arguments: EchoInput,
        context: ToolContext,
    ) -> ToolResult[EchoOutput]:
        return ToolResult[EchoOutput](
            status="ok",
            data=EchoOutput(echoed=arguments.message),
            metadata=ToolMetadata(
                tool_name=self.name,
                schema_version=self.schema_version,
                tool_call_id=context.tool_call_id,
            ),
        )


def test_tool_context_rejects_non_positive_requested_timeout() -> None:
    with pytest.raises(ValidationError):
        ToolContext(
            investigation_id="inv-1",
            tool_call_id="call-1",
            requested_timeout_s=0,
        )


def test_tool_result_rejects_ok_result_with_error() -> None:
    with pytest.raises(ValidationError, match="ok tool results cannot include an error"):
        ToolResult[EchoOutput](
            status="ok",
            error=ToolError(code="bad", message="bad", retryable=False),
            metadata=ToolMetadata(
                tool_name="test.echo",
                schema_version="1",
                tool_call_id="call-1",
            ),
        )


def test_tool_result_requires_error_payload_for_error_status() -> None:
    with pytest.raises(ValidationError, match="error tool results must include an error"):
        ToolResult[EchoOutput](
            status="error",
            metadata=ToolMetadata(
                tool_name="test.echo",
                schema_version="1",
                tool_call_id="call-1",
            ),
        )


def test_registry_registers_tool_and_returns_json_schema() -> None:
    registry = ToolRegistry([EchoTool()])

    schema = registry.input_schema("test.echo")

    assert registry.names() == ("test.echo",)
    assert schema["properties"]["message"]["type"] == "string"
    assert registry.output_schema("test.echo")["properties"]["echoed"]["type"] == "string"


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry([EchoTool()])

    with pytest.raises(DuplicateToolError, match=r"tool already registered: test\.echo"):
        registry.register(EchoTool())


def test_registry_rejects_tool_names_without_domain() -> None:
    tool = EchoTool()
    tool.name = "echo"

    with pytest.raises(InvalidToolDefinitionError, match="invalid tool name: echo"):
        ToolRegistry([tool])


def test_registry_rejects_invalid_timeout_contract() -> None:
    tool = EchoTool()
    tool.default_timeout_s = 10.0

    with pytest.raises(
        InvalidToolDefinitionError,
        match="default timeout cannot exceed max timeout",
    ):
        ToolRegistry([tool])


@pytest.mark.asyncio
async def test_tool_protocol_can_execute_typed_tool() -> None:
    tool = EchoTool()
    context = ToolContext(investigation_id="inv-1", tool_call_id="call-1")

    result = await tool.execute(EchoInput(message="hello"), context)

    assert isinstance(tool, Tool)
    assert result.status == "ok"
    assert result.data == EchoOutput(echoed="hello")
