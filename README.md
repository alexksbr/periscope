# Periscope

> Unified, agentic observability for systems that include LLMs.

<!--
[![CI](https://github.com/<org>/periscope/actions/workflows/ci.yml/badge.svg)](https://github.com/<org>/periscope/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
-->

Periscope ingests OpenTelemetry data from your services *and* your LLM application code and runs an agentic investigator that produces citation-grounded root-cause analyses.

> **Status:** alpha. Actively built. Schemas and APIs will break. Not for production yet.

---

## Why

Today's stack splits LLM observability (Langfuse, Helicone, Arize Phoenix, LangSmith) from service observability (Datadog, Grafana stack, SigNoz, HyperDX). When something misbehaves, you mentally join two stores: was the agent slow because the LLM was slow, or because the database call inside its tool was slow? Were costs high because traffic doubled or because a prompt change made every response 3× longer?

## What's inside

- **OTLP → Redpanda → ClickHouse** ingest pipeline with materialized-view rollups for cost, latency, and error rates.
- **`qx`** — natural-language to ClickHouse SQL with a self-healing reflection loop. Ships as a CLI and as an MCP server.
- **Investigator agent** — multi-hypothesis ReAct loop over typed tools (CH SQL, trace fetch, log query, deploy lookup, code search, runbook RAG, LLM-trace inspect). Every claim in its RCA cites the query that produced its evidence.
- **Eval harness** — golden cases for `qx`, synthetic incidents for the investigator, EFCB-compatible scoring.
- **Self-hosted by default** — Docker Compose locally, Helm chart for Kubernetes.

## Quickstart

```bash
git clone https://github.com/<org>/periscope
cd periscope
uv sync
docker compose up -d
uv run periscope-api
```

Ask a question with `qx`:

```bash
uv run qx "p99 latency for /api/agent in the last 30m by status_code"
```

Trigger a synthetic incident and let the investigator run:

```bash
uv run periscope-chaos latency-spike --endpoint /api/checkout
uv run periscope investigate "checkout latency spiked 12 minutes ago"
```

## Roadmap

- [ ] OTLP → ClickHouse ingest with MV rollups
- [ ] `qx` NL→SQL with self-healing reflection
- [ ] `qx` MCP server
- [ ] Investigator agent loop with citation tracking
- [ ] Toy chaos backend for synthetic incidents
- [ ] Eval harness (qx benchmark + investigator scenarios)
- [ ] Helm chart
- [ ] Multi-tenancy (Postgres RLS + ClickHouse row policies)
- [ ] PII scrubbing OTel processor (OpenAI Privacy Filter)
- [ ] Eval-as-CI gate (Promptfoo integration)
- [ ] Long-retention tiered storage (S3)

## Contributing

Issues and discussions welcome. Project conventions for human contributors and AI agents are in [AGENTS.md](AGENTS.md). Architectural decisions live in [docs/decisions/](docs/decisions/) — open a discussion before proposing changes to existing ADRs.

## License

Apache-2.0. See [LICENSE](LICENSE).
