# ADR-0001: ClickHouse telemetry schema

**Status:** Accepted
**Date:** 2026-05-09
**Deciders:** Alex Kaserbacher

## Summary

Periscope uses the OpenTelemetry Collector ClickHouse exporter's default schema for MVP telemetry storage. The collector creates and writes to exporter-managed tables in the `periscope` database, including `otel_traces`, `otel_logs`, metric tables, and trace-id helper tables. No custom `spans` table exists in MVP.

## Context

Periscope needs to ingest OpenTelemetry traces from service code and LLM application code, then support investigator queries over that telemetry. The first schema decision is whether to own a Periscope-specific ClickHouse span schema immediately or use the schema already owned by the Collector's ClickHouse exporter.

Constraints:

- **The OTel data model is fixed.** Span shape is defined by OpenTelemetry; Periscope does not need a custom shape just to ingest telemetry.
- **MVP ingest should be boring.** A custom schema requires either a custom Collector exporter, a custom OTLP ingest service, or a mapping layer before ClickHouse.
- **The ClickHouse exporter is already the ingest component.** Using its schema avoids schema drift between collector releases and Periscope DDL.
- **GenAI attributes must be preserved.** GenAI semantic convention fields can live in the exporter's span attribute map until query evidence justifies promotion.
- **The investigator query model is not proven yet.** Typed derived columns should earn their keep from real query patterns, not from premature schema design.

## Decision

The ClickHouse exporter owns the physical telemetry schema for MVP.

The local ClickHouse bootstrap SQL creates only the `periscope` database. The collector runs with `create_schema: true`, so the exporter creates the tables it needs:

- `otel_traces`
- `otel_traces_trace_id_ts`
- `otel_traces_trace_id_ts_mv`
- `otel_logs`
- `otel_metrics_gauge`
- `otel_metrics_sum`
- `otel_metrics_summary`
- `otel_metrics_histogram`
- `otel_metrics_exponential_histogram`

Traces are queried from `periscope.otel_traces`. Standard and GenAI span attributes are preserved in the exporter's `SpanAttributes` map. For example:

```sql
SELECT
    Timestamp,
    TraceId,
    SpanName,
    ServiceName,
    SpanAttributes['gen_ai.request.model'] AS model,
    toUInt32OrNull(SpanAttributes['gen_ai.usage.input_tokens']) AS input_tokens,
    toUInt32OrNull(SpanAttributes['gen_ai.usage.output_tokens']) AS output_tokens
FROM periscope.otel_traces
WHERE SpanAttributes['gen_ai.request.model'] != ''
ORDER BY Timestamp DESC
LIMIT 100;
```

If investigator queries later show repeated expensive map lookups, Periscope can add derived views, materialized views, projections, or a curated query table as a successor decision.

## Consequences

**Positive:**

- Ingest uses the stock Collector ClickHouse exporter without custom mapping code.
- Schema creation and insert compatibility stay aligned with the collector version.
- Grafana and ClickHouse OTel examples map directly to the stored tables.
- Raw OTel telemetry, including GenAI attributes, is preserved without lossy promotion decisions.
- The custom query model can be designed from observed query pain instead of speculation.

**Negative / accepted costs:**

- Hot attributes, including GenAI fields, are queried through `SpanAttributes` map lookups.
- Query names use exporter column names such as `Timestamp`, `TraceId`, `SpanName`, and `Duration`.
- Multi-tenancy is not a first-class physical column in the raw trace table.
- Changes in exporter-managed schema need attention during collector upgrades.
- A future Periscope-specific query model would require an additional view, materialized view, or migration.

**Risks to monitor:**

- Query latency for repeated filters on `SpanAttributes`, especially GenAI token/model fields.
- Collector upgrade notes for ClickHouse exporter schema changes.
- Trace lookup performance for investigator workflows that start with only a trace id.
- Whether tenant isolation becomes a product requirement before a derived query model exists.

## Alternatives considered

### Custom `spans` table from day one

Rejected for MVP. A curated table with promoted columns for HTTP, DB, messaging, errors, GenAI, tenancy, and user identity is attractive for investigator queries, but it creates immediate ingest complexity. The stock exporter cannot write arbitrary column names and types, so this would require custom mapping before insert.

### Raw exporter tables plus derived query model

Deferred. This is the likely next step if real queries show that map lookups are too slow or awkward. The derived model should be based on observed investigator query patterns.

### Custom OTLP ingest service

Rejected for MVP. It keeps the schema under Periscope control but makes Periscope responsible for receiving OTLP, mapping spans, batching inserts, retrying, and preserving collector durability semantics.

### Custom Collector exporter

Rejected for MVP. It is the most native place to map OTLP to a custom ClickHouse schema, but it pulls the project into a custom Collector build and Go-based exporter maintenance.

## Future state

Potential successor decisions:

- Add a view or materialized view that exposes common query columns with Periscope-friendly names.
- Promote hot GenAI fields such as model and token counts when query volume justifies it.
- Add tenant-aware derived tables or policies if multi-tenancy becomes active scope.
- Revisit retention and tiered storage once real volume and investigation windows are known.

## References

- [OpenTelemetry Collector ClickHouse exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/clickhouseexporter)
- [OpenTelemetry trace API spec](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [ClickHouse MergeTree engine](https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/mergetree)

## Notes

- The table names above are exporter defaults configured under the `periscope` database.
- The exporter stores attribute values in maps; numeric attributes may need casts at query time.
