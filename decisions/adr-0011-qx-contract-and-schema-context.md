# ADR-0011: Define QX contract and schema context

**Status:** Accepted
**Date:** 2026-05-14
**Deciders:** Alex Kaserbacher

## Summary

QX starts as a typed NL-to-SQL contract plus a ClickHouse schema-context provider. It does not execute user analytical SQL directly; execution remains centralized in `clickhouse.query`.

## Context

ADR-0006 defines `qx.generate_sql` as a stateless SQL-generation tool and `clickhouse.query` as the execution boundary. The tool framework and ClickHouse query tool now exist, so QX needs its own public data shapes before prompt design, SQL repair, or CLI behavior can be implemented.

Natural-language SQL generation needs schema grounding. Without table and column context, the compiler can hallucinate names that do not exist in ClickHouse. The first QX slice should make schema context explicit without committing to an LLM provider or reflection loop.

## Decision

- QX exposes typed Pydantic models for questions, schema snapshots, SQL candidates, compile results, and errors.
- QX schema context is represented as database, table, and column models derived from ClickHouse `system.columns`.
- The first schema provider uses the LangChain adapter for `clickhouse.query` to introspect schema. It does not open a separate ClickHouse client.
- QX compile output is SQL plus metadata such as referenced tables, referenced columns, assumptions, warnings, and confidence.
- QX does not produce final evidence from generated SQL. Evidence remains tied to executed data from `clickhouse.query`.
- This ADR does not add an LLM compiler, repair loop, CLI, or model-facing `qx.generate_sql` tool implementation.

## Consequences

**Positive:**

- QX has a stable typed boundary before prompt and repair behavior are introduced.
- Schema access reuses the existing ClickHouse tool policy, timeout, validation, error handling, and recording path.
- Generated SQL can be inspected independently from execution.

**Negative / accepted costs:**

- Schema introspection is a tool call, so it has the overhead and failure modes of the LangChain tool adapter.
- The initial schema context is structural only; it does not include cardinality, examples, indexes, or semantic aliases.
- Future QX compile and repair code must convert schema-provider failures into QX errors.

**Risks to monitor:**

- Large schemas may need table filtering or pagination before being passed to an LLM.
- System-table schemas may expose tables QX should not use unless allowlists are added.
- The schema context can drift from prompt expectations if QX tests only use mocked schema.

## Alternatives considered

### Let QX query ClickHouse directly

This would keep schema introspection independent from tools, but it would duplicate client configuration, timeout policy, error mapping, and observability. It was rejected for the first slice because the ClickHouse tool already owns the execution boundary.

### Make QX only return raw SQL strings

This is simpler, but it loses assumptions, warnings, confidence, and referenced schema metadata that are needed for debugging, evals, and later reflection. It was rejected in favor of a structured candidate model.

### Start with the LLM compiler

Starting with prompts would produce a visible demo sooner, but it would lock behavior around unstable contracts. It was rejected until the QX data model and schema-context boundary exist.

## References

- ADR-0006: Agent tool API
