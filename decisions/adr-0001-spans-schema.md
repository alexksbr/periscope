# ADR-0001: Spans schema

**Status:** Accepted
**Date:** 2026-05-09
**Deciders:** Alex Kaserbacher

## Summary

Single ClickHouse `spans` table holding both service spans and LLM-generation spans, with hot OTel attributes flattened to typed columns and the long tail of attributes kept in a catch-all `Map`. Partitioned daily by `start_time`, sorted by `(tenant_id, start_time, service_name, trace_id)`, with 30-day TTL for MVP and a documented future path to tiered (hot → cold → delete) storage.

## Context

Periscope's substrate has to ingest OpenTelemetry traces from both regular service code and LLM application code, and serve an investigator agent that issues 10–30 ad-hoc SQL queries per investigation. The schema is the foundation of the substrate — every subsequent decision (ingest pipeline, query layer, materialized-view rollups, TTL/tiering, multi-tenancy) is constrained by it.

Constraints:

- **OTel data model is fixed** — span shape is defined by the OTel API spec; we don't get to redesign it, only model it.
- **GenAI semantic conventions stable since early 2026** — LLM spans are just spans with conventional `gen_ai.*` attributes; the unifying bet of this project depends on storing them in the same substrate.
- **Agent loop performance is non-negotiable** — sub-second iterative SQL queries are required; query latency dominates investigation latency.
- **Scale at MVP is dogfood (low GB/day)**; design must remain defensible at growth scale (~100 GB/day) without rebuilding the schema.
- **Multi-tenancy is on the roadmap but not in MVP** — schema must accept it without a future table rebuild.

## Decision

### Schema

```sql
CREATE TABLE spans (
    -- Core identity
    trace_id          FixedString(32),
    span_id           FixedString(16),
    parent_span_id    Nullable(FixedString(16)),

    -- Tenancy
    tenant_id         LowCardinality(String) DEFAULT 'default',

    -- Timing
    start_time        DateTime64(9, 'UTC'),
    end_time          DateTime64(9, 'UTC'),
    duration_ns       UInt64,

    -- Span properties
    name              LowCardinality(String),
    span_kind         Enum8(
                          'Unspecified' = 0,
                          'Internal'    = 1,
                          'Server'      = 2,
                          'Client'      = 3,
                          'Producer'    = 4,
                          'Consumer'    = 5
                      ),
    status_code       Enum8(
                          'Unset' = 0,
                          'Ok'    = 1,
                          'Error' = 2
                      ),
    status_message    Nullable(String),

    -- Resource (hot, flattened)
    service_name             LowCardinality(String),
    service_version          LowCardinality(Nullable(String)),
    deployment_environment   LowCardinality(Nullable(String)),

    -- HTTP semconv (hot, flattened)
    http_method              LowCardinality(Nullable(String)),
    http_route               LowCardinality(Nullable(String)),
    http_status_code         Nullable(UInt16),

    -- DB semconv (hot, flattened)
    db_system                LowCardinality(Nullable(String)),
    db_operation             LowCardinality(Nullable(String)),

    -- Messaging semconv (hot, flattened)
    messaging_system         LowCardinality(Nullable(String)),
    messaging_destination    LowCardinality(Nullable(String)),

    -- Error semconv (hot, flattened)
    error_type               LowCardinality(Nullable(String)),

    -- GenAI semconv (hot, flattened)
    gen_ai_system            LowCardinality(Nullable(String)),
    gen_ai_request_model     LowCardinality(Nullable(String)),
    gen_ai_input_tokens      Nullable(UInt32),
    gen_ai_output_tokens     Nullable(UInt32),

    -- Periscope-specific
    user_id                  Nullable(String),

    -- Catch-all maps
    attributes               Map(LowCardinality(String), String),
    resource_attributes      Map(LowCardinality(String), String),

    -- Events (timestamped sub-records inside the span)
    events Nested(
        name       LowCardinality(String),
        timestamp  DateTime64(9, 'UTC'),
        attributes Map(LowCardinality(String), String)
    ),

    -- Links (cross-trace correlations and batch-consumer fan-in)
    links Nested(
        trace_id   FixedString(32),
        span_id    FixedString(16),
        attributes Map(LowCardinality(String), String)
    )
)
ENGINE = MergeTree
PARTITION BY toDate(start_time)
ORDER BY (tenant_id, start_time, service_name, trace_id)
TTL toDateTime(start_time) + INTERVAL 30 DAY DELETE;
```

### Decision-by-decision rationale

**Identifiers.** `trace_id FixedString(32)` and `span_id FixedString(16)` — hex-encoded form, fixed-length to enforce the OTel spec's invariant (32 and 16 hex chars respectively) at the schema level and avoid the length-prefix overhead of `String`. `parent_span_id` is `Nullable(FixedString(16))` to express "root span has no parent" cleanly.

**One unified spans table** (not split between service and GenAI spans). The unifying bet of the project is that an LLM call is just a span; splitting the table contradicts the architectural premise. ClickHouse's null bitmap and `Sparse` column encoding handle the unused-LLM-columns case efficiently. Cross-cutting queries (the agent's bread and butter) become single-`SELECT` instead of `UNION`s.

**Attributes representation: flatten hot + catch-all Map.** Hot OTel attributes (HTTP semconv, DB semconv, GenAI semconv, error semconv) live in typed columns. Everything else lives in `attributes Map(LowCardinality(String), String)`. The agent's filters and aggregates run native typed comparisons rather than per-row Map lookups + casts. The Map preserves OTel's open extensibility for unknown attributes.

**Span kind and status: `Enum8`.** Both have spec-fixed value sets; integer codes match the OTLP wire encoding directly, no string conversion at ingest. `Enum8` is cheaper than `LowCardinality(String)` (no per-part dictionary file) and rejects malformed values at insert time.

**Events and links: `Nested` columns.** Both are conceptually arrays of records attached to a span; ClickHouse's `Nested` desugars to parallel arrays sharing offsets. Most spans have zero events/links, and empty arrays compress to near-nothing. Co-locating with the span row eliminates JOINs for the dominant access pattern (fetch a trace and walk it). Reverse-lookup queries (`find spans linking to X`) can be accelerated later with a Bloom-filter skip index.

**Resource attributes: flatten universal three + catch-all.** `service_name`, `service_version`, `deployment_environment` are queried as primary dimensions in nearly every observability query and earn typed columns. `service_name` is the single most-filtered-on column in the entire system and lives in the sort key. `host.*`, `k8s.*`, `cloud.*` go in `resource_attributes` Map at MVP — flatten later via migration if needed.

**`tenant_id` from day 1**, defaulted to `'default'`. Sort-key migrations rebuild the entire table, so the cost of bolting on multi-tenancy later would be hours of rebuild work. The cost of carrying a single-value `LowCardinality(String)` column today is essentially zero (compresses to nothing). Keeps multi-tenancy as a pure ADR-and-RLS-policy work later, not a schema migration.

**Partition key: `toDate(start_time)`.** Daily partitions match observability's dominant query shapes (last hour, last day, since-deploy), give partition pruning that does most of the work, and align with TTL/tiering operations. Monthly is too coarse (can't drop a bad day's data cheaply); hourly is overkill at this scale. `service_name` and `tenant_id` belong in the sort key, not the partition key — partitioning by them blows up partition count without helping pruning for typical queries.

**Sort key: `(tenant_id, start_time, service_name, trace_id)`.** Time-first follows the dominant OSS pattern (SigNoz, HyperDX, ClickStack) and matches the most predictable query shape ("last N minutes"). `service_name` second narrows within a time window; `trace_id` last clusters spans for the same trace on disk for trace-fetch queries that come *with* a time hint. `tenant_id` first to future-proof multi-tenancy without a later rebuild. Pure `WHERE trace_id = X` queries (no time hint) will be slow until a Bloom-filter skip index on `trace_id` is added — accepted MVP cost.

**TTL: 30 days, simple delete.** Adequate for dogfood scale; trivial to reason about; no S3 setup required. The "your logs only remember 14 days" critique is a real concern at scale and the project's blog thesis depends on the answer — but the answer (tiered storage) is a separate ADR, not the foundation. See *Future state* below.

## Consequences

**Positive:**
- Schema mirrors the OTel data model directly — onboarding readers can map spec → table without translation layer.
- Hot attribute access is fast and typed; agent's filters and aggregates run native comparisons.
- Cross-cutting investigator queries are single-SELECT.
- Multi-tenancy can be turned on without rebuilding the spans table.
- Compression-friendly column choices (`LowCardinality`, `Enum8`, `FixedString`) keep storage modest without specialized tuning.
- Partition + sort-key design supports the dominant observability query shape with minimal tuning.

**Negative / accepted costs:**
- Schema width: ~25 typed columns in addition to two Maps. More upfront design work; new "hot" attributes require migration.
- Sparse columns on non-LLM rows (gen_ai_*) and non-HTTP rows (http_*). Storage overhead is mitigated by ClickHouse's null bitmap, but is non-zero.
- `WHERE trace_id = X` without a time hint is a partition-wide scan until a skip index is added.
- 30-day TTL means data older than 30 days is gone forever (until tiered storage ships).

**Risks to monitor:**
- **Map column performance** for catch-all attributes if some workload depends heavily on filtering on uncommon keys. Promotion of those keys to typed columns is the response.
- **`Nested(events)` storage** if a class of spans starts emitting hundreds of events each. Watch part sizes.
- **Sort-key churn** if a future workload heavily filters on a column not in the sort key (e.g., `user_id`). Bloom-filter skip indexes are the first-line response; sort-key change is the last resort.
- **30-day retention** may be insufficient when the agent investigates incidents that started outside the window. Tiered storage ADR is the answer.

## Alternatives considered

### Storage type for IDs — `String` and `UUID/UInt64`
`String` is the dominant OSS choice (Langfuse, SigNoz, HyperDX) for forgiveness in dev/test data and ecosystem-wide consistency. `FixedString` is the more spec-faithful choice (every OTel ID is exactly 32 or 16 hex chars) and saves a byte per row. `UUID`/`UInt64` (binary) saves more storage but loses debuggability — the OTLP wire format is bytes but every UI, log, header, and LLM response is hex; binary IDs require conversion at every boundary. Picked `FixedString` for spec faithfulness and mild storage win; the loss vs `String` is "test data with malformed IDs gets rejected" — acceptable.

### Two tables: `spans` and `gen_ai_spans`
Tempting if LLM workloads dominate volume by 100×. Rejected because (a) the unifying bet of the project requires a single substrate, (b) cross-cutting queries become `UNION`s, and (c) sparse LLM columns aren't expensive in ClickHouse. Revisit if growth-stage volume creates asymmetric retention/partitioning needs.

### `JSON` column for attributes
ClickHouse's JSON type is GA and would preserve OTel's typed values directly. Rejected for MVP because: production maturity in this version is uncertain in projects we want to model on; the `Map(LowCardinality(String), String)` + flattened-hot pattern is what every reference schema uses; "boring works" beats "newer is sexier" at the substrate layer.

### Service-first sort key `(tenant_id, service_name, start_time, trace_id)`
Strong alternative. Service-scoped time-range queries become granule-co-located, faster than the chosen layout. Rejected because cross-service correlation queries (the investigator's "what else happened at 14:23") are common enough that time-first wins on average; service-first hurts those. The OSS pattern leans time-first. Revisit if dogfood reveals service-scoped queries dominate.

### Hourly partitioning
Considered for tighter partition pruning. Rejected: 24× partition count for the same retention; pruning benefit doesn't materialize for the typical "last hour"/"last day" queries; operational overhead of more partitions outweighs the marginal win.

### Status-differentiated TTL (errors longer than successes)
Sentry-style retention. Rejected for MVP — added complexity not warranted. Reasonable post-MVP refinement.

## Future state

The TTL decision is provisional. The project's "long-retention tiered storage" roadmap item maps to a successor ADR that ships:

```sql
TTL
  toDateTime(start_time) + INTERVAL 7 DAY  RECOMPRESS CODEC(ZSTD(9)),
  toDateTime(start_time) + INTERVAL 30 DAY TO VOLUME 'cold',
  toDateTime(start_time) + INTERVAL 365 DAY DELETE
```

with a `storage_policy` defining a hot SSD volume and a cold S3-backed volume. This unblocks the "your logs only remember 14 days" thesis post and brings retention to a year at modest cost. Not implemented in MVP because it requires S3 configuration, multi-volume `storage_policy`, and a specific operational commitment that doesn't pay off at dogfood scale.

## References

- [OpenTelemetry trace API spec](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [ClickHouse MergeTree engine](https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/mergetree)
- [ClickHouse `Nested` data type](https://clickhouse.com/docs/en/sql-reference/data-types/nested-data-structures/nested)
- [ClickHouse `LowCardinality` and `Enum`](https://clickhouse.com/docs/en/sql-reference/data-types/lowcardinality)
- [ClickHouse: Your AI SRE needs better observability, not bigger models](https://clickhouse.com/blog/ai-sre-observability-architecture)
- Langfuse public ClickHouse schema (reference) — `github.com/langfuse/langfuse`
- SigNoz spans schema (reference) — `github.com/SigNoz/signoz`
- HyperDX / ClickStack OTel-to-CH exporter (reference) — `github.com/hyperdxio/hyperdx`

## Notes

- `duration_ns` is stored as a `UInt64` populated at ingest time, not as a `MATERIALIZED` expression. Materialized expressions on `DateTime64(9)` arithmetic introduce subtle numeric edge cases at extreme durations; ingest-time computation in the OTel exporter is simpler and explicit.
- The `gen_ai.finish_reasons`, `gen_ai.request.temperature`, and prompt/completion content remain in `attributes` for MVP. Promotion to typed columns is reasonable when the investigator's query patterns demand it (e.g., "filter spans where finish_reason = 'tool_use'" becomes hot).
- All schema migrations are append-only — never edit historical migrations or rewrite history. New ADRs supersede old ones rather than rewriting them.
