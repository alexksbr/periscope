from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from periscope.tools.clickhouse import MAX_CLICKHOUSE_QUERY_LIMIT


class QxError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    detail: dict[str, object] = Field(default_factory=dict)


class QxQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=20_000)

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question cannot be blank")
        return stripped


class QxColumnRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    table: str
    column: str


class QxColumnSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    position: int = Field(ge=1)


class QxTableSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    name: str
    columns: list[QxColumnSchema] = Field(default_factory=list)


class QxSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str = Field(default="periscope", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    include_tables: list[str] = Field(default_factory=list)
    max_columns: int = Field(
        default=MAX_CLICKHOUSE_QUERY_LIMIT,
        ge=1,
        le=MAX_CLICKHOUSE_QUERY_LIMIT,
    )

    @field_validator("include_tables")
    @classmethod
    def _validate_include_tables(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        tables: list[str] = []
        for table in value:
            if not table:
                raise ValueError("table names cannot be blank")
            if table in seen:
                continue
            seen.add(table)
            tables.append(table)
        return tables


class QxSchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    tables: list[QxTableSchema] = Field(default_factory=list)
    column_count: int = Field(ge=0)
    truncated: bool = False
    source: Literal["clickhouse.system.columns"] = "clickhouse.system.columns"


class QxSqlCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)
    dialect: Literal["clickhouse"] = "clickhouse"
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[QxColumnRef] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class QxCompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    question: QxQuestion
    candidate: QxSqlCandidate | None = None
    error: QxError | None = None

    @model_validator(mode="after")
    def _status_matches_payload(self) -> Self:
        if self.status == "ok":
            if self.candidate is None:
                raise ValueError("ok QX compile results must include a candidate")
            if self.error is not None:
                raise ValueError("ok QX compile results cannot include an error")
        if self.status == "error":
            if self.error is None:
                raise ValueError("error QX compile results must include an error")
            if self.candidate is not None:
                raise ValueError("error QX compile results cannot include a candidate")
        return self
