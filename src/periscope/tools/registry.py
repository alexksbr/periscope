from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, cast

from pydantic import BaseModel

from periscope.tools.base import Tool

TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class ToolRegistryError(ValueError):
    pass


class DuplicateToolError(ToolRegistryError):
    pass


class InvalidToolDefinitionError(ToolRegistryError):
    pass


class UnknownToolError(LookupError):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool[Any, Any]] = ()) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any, Any]) -> None:
        _validate_tool_definition(tool)
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def input_schema(self, name: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.get(name).input_model.model_json_schema())

    def output_schema(self, name: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.get(name).output_model.model_json_schema())


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
