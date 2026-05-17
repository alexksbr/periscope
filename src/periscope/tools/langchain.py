# LangChain inspects Annotated metadata at runtime for InjectedToolCallId, so this
# module intentionally does not enable postponed annotation evaluation.
import re
from collections.abc import Iterable
from typing import Annotated, Any, cast

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.prebuilt import ToolNode
from opentelemetry.trace import Tracer
from pydantic import BaseModel

from periscope.tools.base import Tool
from periscope.tools.models import ToolContext, ToolResult
from periscope.tools.policy import execute_tool_with_policy
from periscope.tools.recording import ToolCallRecorder

PERISCOPE_CONTEXT_CONFIG_KEY = "periscope_tool_context"
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class ToolAdapterError(ValueError):
    pass


class DuplicateToolNameError(ToolAdapterError):
    pass


class InvalidToolDefinitionError(ToolAdapterError):
    pass


def build_langchain_tool(
    tool: Tool[Any, Any],
    *,
    recorder: ToolCallRecorder | None = None,
    tracer: Tracer | None = None,
) -> StructuredTool:
    _validate_tool_definition(tool)

    async def _run_tool(
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        **arguments: object,
    ) -> tuple[str, dict[str, object]]:
        context = _context_from_config(config, tool_call_id)
        result = await execute_tool_with_policy(
            tool=tool,
            arguments=arguments,
            context=context,
            recorder=recorder,
            tracer=tracer,
        )
        return _tool_message_content(result), cast(
            dict[str, object], result.model_dump(mode="python")
        )

    return StructuredTool.from_function(
        coroutine=_run_tool,
        name=langchain_tool_name(tool.name),
        description=f"Execute Periscope tool {tool.name}.",
        args_schema=tool.input_model,
        response_format="content_and_artifact",
    )


def build_langchain_tools(
    tools: Iterable[Tool[Any, Any]],
    *,
    recorder: ToolCallRecorder | None = None,
    tracer: Tracer | None = None,
) -> tuple[StructuredTool, ...]:
    seen: set[str] = set()
    langchain_tools: list[StructuredTool] = []
    for tool in tools:
        if tool.name in seen:
            raise DuplicateToolNameError(f"tool already registered: {tool.name}")
        seen.add(tool.name)
        langchain_tools.append(build_langchain_tool(tool, recorder=recorder, tracer=tracer))
    return tuple(langchain_tools)


def build_tool_node(tools: Iterable[Tool[Any, Any] | StructuredTool]) -> ToolNode:
    langchain_tools = [
        tool if isinstance(tool, StructuredTool) else build_langchain_tool(tool) for tool in tools
    ]
    return ToolNode(langchain_tools)


async def invoke_langchain_tool(
    tool: StructuredTool,
    *,
    arguments: object,
    context: ToolContext,
) -> ToolMessage:
    result = await tool.ainvoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "args": arguments,
            "id": context.tool_call_id,
        },
        config=_config_from_context(context),
    )
    if not isinstance(result, ToolMessage):
        raise ToolAdapterError("LangChain tool returned an unexpected message type")
    return result


def langchain_tool_name(periscope_tool_name: str) -> str:
    return periscope_tool_name.replace(".", "_")


def periscope_tool_result_from_message[DataT: BaseModel](
    message: ToolMessage,
    data_model: type[DataT],
) -> ToolResult[DataT]:
    artifact = message.artifact
    if not isinstance(artifact, dict):
        raise ToolAdapterError("LangChain tool message did not include a Periscope result artifact")
    result = ToolResult[Any].model_validate(artifact)
    if result.data is not None:
        result = result.model_copy(update={"data": data_model.model_validate(result.data)})
    return cast(ToolResult[DataT], result)


def _config_from_context(context: ToolContext) -> RunnableConfig:
    return {"configurable": {PERISCOPE_CONTEXT_CONFIG_KEY: context.model_dump(mode="json")}}


def _context_from_config(config: RunnableConfig, tool_call_id: str) -> ToolContext:
    raw_context = config.get("configurable", {}).get(PERISCOPE_CONTEXT_CONFIG_KEY)
    if isinstance(raw_context, ToolContext):
        return raw_context.model_copy(update={"tool_call_id": tool_call_id})
    if isinstance(raw_context, dict):
        return ToolContext.model_validate(raw_context).model_copy(
            update={"tool_call_id": tool_call_id}
        )
    return ToolContext(investigation_id="", tool_call_id=tool_call_id)


def _tool_message_content(result: ToolResult[Any]) -> str:
    if result.status == "ok":
        return f"{result.metadata.tool_name} ok"
    if result.error is None:
        return f"{result.metadata.tool_name} error"
    return f"{result.metadata.tool_name} error: {result.error.code}"


def _validate_tool_definition(tool: Tool[Any, Any]) -> None:
    if not TOOL_NAME_PATTERN.fullmatch(tool.name):
        raise InvalidToolDefinitionError(f"invalid tool name: {tool.name}")
    if not tool.schema_version:
        raise InvalidToolDefinitionError(f"tool {tool.name} has an empty schema version")
    if not issubclass(tool.input_model, BaseModel):
        raise InvalidToolDefinitionError(f"tool {tool.name} input model must extend BaseModel")
    if not issubclass(tool.output_model, BaseModel):
        raise InvalidToolDefinitionError(f"tool {tool.name} output model must extend BaseModel")
    if tool.default_timeout_s <= 0:
        raise InvalidToolDefinitionError(f"tool {tool.name} default timeout must be positive")
    if tool.max_timeout_s <= 0:
        raise InvalidToolDefinitionError(f"tool {tool.name} max timeout must be positive")
    if tool.default_timeout_s > tool.max_timeout_s:
        raise InvalidToolDefinitionError(
            f"tool {tool.name} default timeout cannot exceed max timeout"
        )
