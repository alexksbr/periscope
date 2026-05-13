from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from periscope.tools.models import ToolContext, ToolResult


@runtime_checkable
class Tool[InputT: BaseModel, OutputT: BaseModel](Protocol):
    name: str
    schema_version: str
    input_model: type[InputT]
    output_model: type[OutputT]
    idempotent: bool
    default_timeout_s: float
    max_timeout_s: float

    async def execute(
        self,
        arguments: InputT,
        context: ToolContext,
    ) -> ToolResult[OutputT]: ...
