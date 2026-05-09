# Architecture Decision Records

This directory holds ADRs for Periscope. An ADR captures a single significant architectural decision: the context that forced the choice, the decision itself, the consequences, and the alternatives considered.

## When to write one

Write an ADR before code if a change touches:

- Storage schema, partition/sort keys, materialized views, indexes
- Public API surface (URLs, request/response shapes, span attributes)
- Tool API (the agent's tool contract)
- Multi-tenancy or authorization boundaries
- Eval rubric or scoring
- Buffer / queue / streaming substrate
- Anything in the [design constraints list](../../AGENTS.md#design-constraints-do-not-auto-modify-without-discussion)

If you're not sure, write one anyway. Cheaper than re-litigating the call later.

## Process

1. Copy [`0000-adr-template.md`](0000-adr-template.md) to `NNNN-short-title.md` (next free number, kebab-case title).
2. Fill in Context, Decision, Consequences, Alternatives.
3. Open a discussion / PR. Status starts as **Proposed**.
4. Once accepted, set Status to **Accepted** with the date.
5. If a later ADR overrides this one, set Status to **Superseded by ADR-NNNN**.

ADR numbers are append-only. Don't reorder.

## Index

| #     | Title                                                         | Status   |
| ----- | ------------------------------------------------------------- | -------- |
| 0001  | Spans schema (column types, partition key, sort key, TTL)     | Planned  |
| 0002  | Multi-tenancy approach (RLS / row policy / schema-per-tenant) | Planned  |
| 0003  | Ingest buffer (Redpanda vs Kafka)                             | Planned  |
| 0004  | Materialized-view rollups (cost / latency / errors)           | Planned  |
| 0005  | ClickHouse indexes, skip indexes, projections                 | Planned  |
| 0006  | Agent tool API (inputs, outputs, idempotency, timeouts)       | Planned  |
| 0007  | Citation model (linking agent claims to evidence)             | Planned  |
| 0008  | Eval format (golden cases, scoring rubric, EFCB compat)       | Planned  |
| 0009  | Hybrid retrieval (BM25 + dense merge, rerank threshold)       | Planned  |
| 0010  | Recursive instrumentation (agent traces itself, no loops)     | Planned  |

`Planned` means the ADR is on the roadmap but not yet written. `Proposed` means a draft exists and is open for discussion. `Accepted` means it's the active decision. `Superseded` means a newer ADR replaces it.

## Style

- Short. One page if possible. Decision docs that nobody reads do nothing.
- Concrete. "We use ClickHouse partition by `toYYYYMM(timestamp)` because…" beats "We use a time-based partition strategy."
- Honest about tradeoffs. Every decision has costs; name them.
- One decision per ADR. If you find yourself making two, split.
