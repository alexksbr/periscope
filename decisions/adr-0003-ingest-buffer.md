# ADR-0003: Ingest buffer

**Status:** Accepted
**Date:** 2026-05-09
**Deciders:** Alex Kaserbacher

## Summary

No streaming buffer (Kafka/Redpanda) at MVP. The OpenTelemetry Collector exports directly to ClickHouse via the official `clickhouseexporter`, with a file-backed persistent send queue handling durability across collector restarts and downstream outages. A streaming buffer (Redpanda) is a deliberately deferred future state, with explicit trigger conditions documented below.

## Context

ADR-0001 established the exporter-managed ClickHouse OTel tables as the telemetry substrate. The next architectural question is what sits between the application code emitting OTLP and ClickHouse accepting writes.

Two shapes are common:

1. **Direct**: OTel Collector → ClickHouse exporter.
2. **Buffered**: OTel Collector → Kafka/Redpanda → separate consumer → ClickHouse.

The buffered shape is canonical for high-volume telemetry ingest and is the textbook answer for "how do you handle fleet-scale streaming data" — relevant to Innio's product domain. Built-in OTel Collector facilities (persistent send queue, exponential-backoff retry, batching, dead-letter handling) cover most of what a buffer provides in single-consumer deployments. The choice is therefore not "do we want streaming behavior" but "do we want the *infrastructure* for replay, multi-consumer fan-out, and decoupled scaling now or later."

Constraints:

- **MVP traffic is dogfood-scale** — well under what the collector's queue can handle without a buffer.
- **One consumer** — ClickHouse — for the foreseeable future. The investigator agent reads from ClickHouse tables, not from a stream.
- **No replay-against-new-prompt feature** in MVP scope; that's a stretch goal that, if pursued, would push toward a buffer.
- **Operational simplicity matters** — every additional service in the stack is something to monitor, debug, and document.

## Decision

### MVP shape

```
[App + LLM SDKs]
    │ OTLP (gRPC, zstd)
    ▼
[OTel Collector]
    │  pipeline:
    │    receivers: [otlp]
    │    processors: [batch, memory_limiter]
    │    exporters: [clickhouse]
    │
    │  exporter config:
    │    sending_queue:
    │      enabled: true
    │      storage: file_storage    # survive restarts
    │      queue_size: 10000
    │    retry_on_failure:
    │      enabled: true
    │      initial_interval: 5s
    │      max_interval: 30s
    │      max_elapsed_time: 300s
    ▼
[ClickHouse]
```

Concrete decisions:

- **Direct export.** OTel Collector → ClickHouse, no streaming buffer in the path.
- **File-backed persistent send queue** (`storage: file_storage`). The collector survives its own restarts and ClickHouse downtime up to the queue's capacity (~10K span batches). Spans that arrive while the queue is full are dropped with a metric incremented, surfacing the condition.
- **gRPC OTLP, zstd compression** on the wire from SDKs to Collector and from Collector to ClickHouse where the exporter supports it. Default in modern collectors; cheap CPU for substantial bandwidth and storage savings.
- **Retry with bounded backoff**: 5s → 30s, capped at 5 minutes total elapsed, then DLQ-equivalent (drop with metric).

### Trigger conditions for adding a buffer

When any of these conditions becomes true, write a successor ADR introducing Redpanda:

1. **A second consumer becomes a requirement** — analytics pipeline, archival, replay-against-new-prompt feature, or an external system that needs the same span stream.
2. **Replay becomes a product feature** — re-processing past spans through new logic is required for normal operation, not just disaster recovery.
3. **Sustained volume exceeds the collector's persistent-queue capacity** during routine ClickHouse maintenance windows. Concretely: if the collector regularly drops spans during planned ClickHouse downtime > a few minutes.
4. **Stream processing layer is needed** — windowed aggregations, real-time anomaly detection, or other consumers operating on the live stream rather than the warehouse.

None of these conditions apply at MVP. Each is concrete enough that the trigger is observable, not aspirational.

### Future state (when a trigger fires)

The deferred design, ready to ship when a trigger condition activates:

```
[App + LLM SDKs]
    │ OTLP
    ▼
[OTel Collector — producer pipeline]
    │ exporter: kafka   (zstd, idempotent)
    │ topic: spans
    │ partition_key: trace_id
    ▼
[Redpanda]
    │ topic: spans (16 partitions, replication=1 dev / 3 prod, 7-day retention)
    ▼
[OTel Collector — consumer pipeline]    [Future: analytics consumer]
    │ receiver: kafka                   [Future: archival consumer]
    │ exporter: clickhouse
    ▼
[ClickHouse]
```

Decisions baked into the future-state design:

- **Redpanda over Apache Kafka.** Single C++ binary, no JVM, no ZooKeeper. Kafka-protocol compatible — clients work unchanged. Operational simplicity wins at our scale; switching to Kafka later is mechanical (same client code) if the team and ops capacity grow.
- **One topic for all spans** (`spans`). One signal type, no need for per-tenant or per-service topics — partitioning handles distribution, and ClickHouse remains the query substrate.
- **Partition key: `trace_id`.** Keeps every span in a trace in the same partition, so any consumer that wants per-trace ordering gets it for free. Distribution is uniform because trace IDs are random.
- **Partition count: 16** as a starting default — enough for parallel consumer scaling, low enough for low-volume operations. Adjustable.
- **Replication factor**: 1 in dev, 3 in production-grade deployments.
- **Retention: 7 days.** Long enough for replay after a bug fix that needs a few days; short enough to keep storage modest.
- **DLQ topic: `spans.dlq`** for malformed messages the consumer can't process.
- **Producer-side compression: zstd.**

This section exists so that, when a trigger fires, the ADR is mostly write-the-config-and-go rather than redo-the-design.

## Consequences

**Positive:**

- One fewer service to operate, monitor, and document at MVP. Stack is genuinely simpler.
- Persistent send queue handles the realistic failure modes (collector restart, transient ClickHouse downtime) without operational overhead.
- The "buffered shape is over-engineered for one consumer" position is honestly defensible — restraint is a senior-architect signal.
- Future state is documented in advance, so adding a buffer later is config + ops work, not architectural rediscovery.

**Negative / accepted costs:**

- No replay capability — once a span is past the collector's queue, the only copy is in ClickHouse. Bug fixes that depend on re-processing past spans are not possible.
- Collector and ClickHouse are coupled — they have to scale together. At growth scale this becomes awkward.
- Sustained ClickHouse downtime exceeding the queue's capacity (~10K batches) drops spans. Observable via metric, but a real loss.
- The architectural narrative of "OTLP → buffer → ClickHouse" — present in many reference docs and Periscope-adjacent essays — is documented in the ADR but not embodied in the running system. Discussing it in writing or interviews requires the explicit framing of "the buffer is a deferred future state with these trigger conditions."

**Risks to monitor:**

- Drop metric on the collector's queue. If non-zero in routine operation, that's a trigger fire.
- Per-second span throughput at the collector. If it climbs toward queue exhaustion, bias toward the buffer earlier than the trigger conditions suggest.
- ClickHouse maintenance window length. If real downtime starts to exceed a few minutes regularly, the queue is no longer the right substitute.

## Alternatives considered

### Buffer-from-day-1 (Redpanda in MVP)
The buffered shape from the start. Rejected for MVP: at one-consumer dogfood scale, the buffer is pure operational overhead. Adds a service to maintain, a topology to document, and a topic/partition strategy to defend — without addressing any current need. The scenarios that justify it (replay, fan-out, decoupled scaling) all live in deferred or speculative roadmap territory. Including it would be cargo-culted complexity rather than designed restraint.

### Direct export with in-memory send queue
Same shape as the chosen design but without persistent queue (`sending_queue.storage` not set). Rejected because the storage cost of a file-backed queue is trivial and the protection (surviving collector restart, surviving short ClickHouse downtime) is the load-bearing reason direct-export is acceptable in the first place. Without it, every restart drops in-flight spans.

### Apache Kafka instead of Redpanda for the future state
Considered for the deferred design. Rejected as the default future choice for the same reasons Redpanda wins at MVP scale: simpler operationally for a project that doesn't have a dedicated platform team. The future-state ADR can revisit if production deployment context shifts.

### A second OTel Collector instance as a poor-man's buffer
Two collectors chained, where the first acts as a queue feeding the second. Rejected — duplicates queue logic without adding any of a real buffer's actual capabilities (replay, fan-out, retention). Strictly worse than the file-backed queue on a single collector.

## References

- [OpenTelemetry Collector ClickHouse exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/clickhouseexporter)
- [OpenTelemetry Collector `exporterhelper` send queue](https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/exporterhelper/README.md)
- [OpenTelemetry Collector `file_storage` extension](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/extension/storage/filestorage)
- [Redpanda architecture](https://docs.redpanda.com/current/get-started/architecture/)
- [Apache Kafka design](https://kafka.apache.org/documentation/#design)
- ADR-0001 (ClickHouse telemetry schema) — uses exporter-managed OTel tables for MVP storage.

## Notes

- The "buffer is documented future state" framing is more than diplomatic ADR-language — it's the actual senior-architect answer to "why didn't you put Kafka in?" The trigger conditions are concrete and observable, which is what distinguishes deferred design from absent design.
- `file_storage` extension config requires a path on a persistent volume. In a Kubernetes deployment that's a `PersistentVolumeClaim`; in Compose it's a host-mount. Future deployment ADRs need to remember this.
- The future-state Kafka topic configuration (`16 partitions`, `trace_id` key, 7-day retention) is a starting point, not a final answer. The ADR that activates the buffer should re-defend the partition count against measured volume.
