# ADR-0006: Agent tool API

**Status:** Accepted
**Date:** 2026-05-12
**Deciders:** Alex Kaserbacher

## Summary

Periscope tools use a Python-first, async, Pydantic-typed API internally. The tool runner adapts LLM JSON tool calls into typed inputs, enforces execution policy, and returns a standard result envelope with typed data, typed errors, evidence references, and compact metadata.

## Context

The investigator agent needs to call tools for ClickHouse queries, trace fetches, log search, runbook retrieval, deploy lookup, code search, and `qx` SQL generation. These calls sit on the hot path of root-cause analysis, citation collection, evaluation, and observability.

The API has to serve two different boundaries:

- **Model-facing boundary:** LLMs and future MCP clients speak tool names plus JSON arguments.
- **Code-facing boundary:** Periscope is a Python 3.12 codebase with strict typing, async I/O, and Pydantic v2 for external I/O.

The decision cannot be deferred because tool call shape determines how the agent loop handles validation, retries, timeouts, evidence, citations, and replay logs.

## Decision

### Internal tool contract

Tools are implemented as async Python objects with Pydantic input and output models. JSON Schema is generated from those models for LLM and future MCP boundaries.

Illustrative shape:

```python
class ToolContext(BaseModel):
    investigation_id: str
    tool_call_id: str
    idempotency_key: str | None = None
    requested_timeout_s: float | None = None


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool
    detail: dict[str, object] = Field(default_factory=dict)


class ToolResult[T](BaseModel):
    status: Literal["ok", "error"]
    data: T | None = None
    error: ToolError | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    metadata: ToolMetadata
```

Each concrete tool declares:

- stable `name`, using `domain.verb` naming such as `clickhouse.query`, `trace.fetch`, `logs.search`, `runbook.search`, `deploy.lookup`, `code.search`, and `qx.generate_sql`
- `schema_version`
- Pydantic `input_model`
- Pydantic typed success `data` model
- `idempotent`
- `default_timeout_s`
- `max_timeout_s`

The tool runner is the adapter between the model-facing JSON protocol and internal Python code:

1. receive tool name and JSON arguments from the LLM loop
2. find the tool in the static registry
3. validate arguments into the tool's Pydantic input model
4. enforce timeout, retry, idempotency, and observability policy
5. call the tool's async Python implementation
6. return a `ToolResult` serialized back to JSON for the LLM loop

The runner is not a remote RPC layer in MVP. It is an in-process dispatcher and policy boundary.

### Result and error model

Every tool execution returns a standard `ToolResult[T]` envelope, where `T` is the tool-specific success data model.

Expected tool failures are represented as typed `ToolError` values. Tool implementations may raise specific internal exceptions for unexpected failures, but the tool runner converts validation failures, execution exceptions, per-call timeouts, and transient infrastructure failures into `ToolError` results before returning to the agent loop.

Caller-initiated cancellation, such as shutdown or an abandoned investigation, propagates so work can stop promptly. Per-call timeout is reported as `ToolError(code="timeout", retryable=True)`.

### Evidence and citations

Tools return structured `EvidenceRef` values for factual data they produce. Tools do not write final prose citations and do not decide which final answer claim an evidence item supports.

The citation tracker owns claim-to-evidence mapping. Executed data, not generated SQL alone, is what produces evidence.

### Idempotency

MVP tools are read-only by default. Every tool declares whether it is idempotent.

Every tool call gets a stable `tool_call_id`. `idempotency_key` is supported in `ToolContext` but is required only for future non-idempotent, mutating tools such as creating an incident note, posting a message, opening an issue, or acknowledging an alert.

The tool runner rejects non-idempotent calls without an idempotency key once mutating tools exist.

### Timeouts, retries, and streaming

The tool runner enforces timeouts. Each tool declares `default_timeout_s` and `max_timeout_s`. The agent may request a timeout, but the runner caps it at the tool maximum.

The runner may perform one automatic retry for clearly transient infrastructure errors. SQL validation errors, permission errors, empty results, semantic failures, and non-retryable tool errors are returned to the agent loop without automatic retry.

MVP tool calls are non-streaming. A tool execution returns exactly one final `ToolResult`. Progress events are internal telemetry and are not part of the tool output contract.

### Observability and persistence

The tool runner wraps every tool execution in an OpenTelemetry span. The span records compact metadata such as tool name, schema version, tool call id, investigation id, status, duration, timeout, error code, retryability, and evidence count.

Full SQL result rows, logs, prompts, and raw tool outputs are not recorded as span attributes by default.

Periscope persists compact tool-call records for replay and debugging:

- tool name and schema version
- normalized input
- status
- typed error, if any
- evidence references
- bounded output preview or summary
- timing and retry metadata

Periscope does not persist unbounded raw rows, logs, prompts, or full tool outputs by default.

### Query tools and `qx`

The MVP includes a read-only raw SQL tool, `clickhouse.query`, because autonomous investigation needs flexible ad hoc queries. The tool is constrained:

- ClickHouse user is read-only
- single statement only
- `SELECT` only
- no DDL or DML
- timeout enforced by the runner
- row limit enforced by the input model and runner
- query is logged and linked to evidence

`qx` is exposed to the investigator as a stateless SQL-generation tool, `qx.generate_sql`. It turns a natural-language telemetry question into SQL plus explanation and warnings. It does not execute SQL, own investigation state, or produce final evidence.

The intended flow is:

1. investigator chooses an analytical sub-question
2. `qx.generate_sql` proposes SQL
3. `clickhouse.query` validates and executes the SQL
4. executed query results produce evidence
5. investigator decides what the evidence means

The investigator is the planner, reasoner, and synthesizer. `qx` is a narrower NL-to-SQL subsystem used by the investigator.

### Registry

MVP uses a static in-process tool registry. Plugin discovery is deferred. The tool protocol should not block future plugins, MCP exposure, or remote execution, but ADR-0006 does not define a plugin system.

## Consequences

**Positive:**

- Internal tool code stays simple, async, typed, testable, and compatible with `mypy --strict`.
- LLM and future MCP boundaries still get JSON Schema generated from the same Pydantic models.
- The agent loop gets uniform handling for validation, errors, evidence, retries, idempotency, timeouts, observability, and replay metadata.
- Citation tracking is tied to executed evidence instead of model-generated claims.
- Raw SQL keeps the investigator powerful enough for open-ended diagnosis while `clickhouse.query` centralizes the safety guardrails.
- `qx` has a crisp responsibility: generate SQL, not run investigations.

**Negative / accepted costs:**

- Future remote tools or plugins need adapters around the Python-first contract.
- The standard result envelope adds structure to every tool implementation.
- Raw SQL requires careful validation, read-only credentials, limits, and monitoring.
- Persisted tool-call records create retention and privacy obligations, even with bounded previews.
- Deferring streaming means long-running tools only surface progress through telemetry, not as a public tool output stream.

**Risks to monitor:**

- The LLM may generate expensive SQL that passes basic read-only validation.
- Tool output previews may still contain sensitive data if redaction is insufficient.
- One automatic retry can amplify load during partial infrastructure outages if error classification is too broad.
- `qx` and `clickhouse.query` schemas may drift unless tests cover the generated-SQL execution path.
- Future mutating tools will require stronger idempotency storage and conflict handling than MVP read-only tools need.

## Alternatives considered

### JSON-first tool protocol

In this design, the internal tool API would be a generic `invoke(name, arguments, context) -> dict` protocol, with JSON Schema as the canonical contract. This is attractive for remote tools, MCP, multi-language implementations, and replay systems. It was rejected for MVP because it would push untyped dictionaries and runtime validation into the core Python codebase before remote execution is required.

### Tool-specific result shapes only

Each tool could return its own output model without a common envelope. This keeps individual tools small but forces the agent loop to learn every tool's error, evidence, retry, and metadata shape. It was rejected because the investigator needs uniform cross-tool handling.

### Tool-owned citations

Tools could emit final citation text or decide which claims their evidence supports. This was rejected because tools know data provenance, not final answer semantics. The citation tracker owns claim-to-evidence mapping.

### Typed query tools only

The investigator could be limited to tools such as `latency.by_service`, `errors.by_endpoint`, and `trace.fetch`. This is safer than raw SQL and easier to validate, but it constrains autonomous investigation and requires predicting every useful query shape up front. It was rejected for MVP in favor of guarded read-only SQL.

### `qx` executes SQL

`qx` could generate and execute SQL, returning rows directly. This was rejected because it blurs responsibilities and bypasses the central execution guardrails in `clickhouse.query`. Keeping `qx` stateless makes it easier to test, evaluate, and reuse.

### Plugin discovery in MVP

A plugin-style registry would make tools extensible earlier. It was rejected for MVP because it introduces security, versioning, loading, dependency, and support questions before the built-in tool contract has proven itself.

## References

- ADR-0001: ClickHouse telemetry schema
- ADR-0003: Ingest buffer
- [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)
- [OpenTelemetry trace semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [ClickHouse readonly setting](https://clickhouse.com/docs/operations/settings/permissions-for-queries#readonly)

## Notes

- Breaking tool input or output changes require a new `schema_version`. Additive fields may stay within the same version when existing callers remain valid.
- Tool names should stay stable. Do not add version numbers to names unless introducing a breaking replacement that must coexist with the old tool.
- Implementation should include tests for JSON Schema generation, Pydantic validation, timeout conversion, retry classification, SQL guardrails, and evidence creation.
