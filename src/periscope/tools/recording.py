from __future__ import annotations

from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from periscope.tools.models import EvidenceRef, ToolError


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: str
    tool_call_id: str
    tool_name: str
    schema_version: str
    normalized_input: dict[str, object] | None = None
    status: Literal["ok", "error"]
    error: ToolError | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    output_preview: str | None = None
    output_preview_truncated: bool = False
    duration_ms: float = Field(ge=0)
    timeout_s: float | None = Field(default=None, gt=0)
    attempt_count: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _status_matches_payload(self) -> Self:
        if self.status == "ok" and self.error is not None:
            raise ValueError("ok tool call records cannot include an error")
        if self.status == "error" and self.error is None:
            raise ValueError("error tool call records must include an error")
        return self


class ToolCallRecorder(Protocol):
    async def record(self, record: ToolCallRecord) -> None: ...


class NoopToolCallRecorder:
    async def record(self, record: ToolCallRecord) -> None:
        return None
