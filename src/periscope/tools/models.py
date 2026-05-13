from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source: str
    title: str
    detail: dict[str, object] = Field(default_factory=dict)


class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: str
    tool_call_id: str
    idempotency_key: str | None = None
    requested_timeout_s: float | None = Field(default=None, gt=0)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    detail: dict[str, object] = Field(default_factory=dict)


class ToolMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    schema_version: str
    tool_call_id: str
    duration_ms: float | None = Field(default=None, ge=0)
    timeout_s: float | None = Field(default=None, gt=0)
    attempt_count: int = Field(default=1, ge=1)
    extra: dict[str, object] = Field(default_factory=dict)


class ToolResult[DataT: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    data: DataT | None = None
    error: ToolError | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    metadata: ToolMetadata

    @model_validator(mode="after")
    def _status_matches_payload(self) -> Self:
        if self.status == "ok" and self.error is not None:
            raise ValueError("ok tool results cannot include an error")
        if self.status == "error":
            if self.error is None:
                raise ValueError("error tool results must include an error")
            if self.data is not None:
                raise ValueError("error tool results cannot include data")
        return self
