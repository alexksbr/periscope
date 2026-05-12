# AGENTS.md

Guidance for AI coding agents (Claude Code, OpenAI Codex, Cursor, Aider, Continue, etc.) working in this repository. Human contributors should read [docs/architecture.md](docs/architecture.md) and [docs/decisions/](docs/decisions/) for the same context in long form.

## Project

**Periscope** — a self-hostable, OSS, agentic observability investigator that ingests both LLM application traces and service telemetry over OpenTelemetry, answers "what's wrong and why" with a citation-grounded RCA loop over ClickHouse, and ships with a public eval harness.

Status: alpha, actively built, breaking changes likely.

## Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI (async-first), Pydantic v2 for all I/O
- **Storage:** ClickHouse (telemetry), Postgres + pgvector (op data + vectors), Redis (cache)
- **Bus:** Redpanda (Kafka-compatible)
- **Telemetry:** OpenTelemetry (GenAI semconv for LLM spans)
- **LLM gateway:** Bifrost (sidecar; OpenAI-compatible endpoint)
- **Orchestration:** Docker Compose locally, Helm for Kubernetes
- **Tooling:** uv (env + deps), ruff (lint + format), mypy --strict (types), pytest

## Repository layout

```
src/periscope/
  agent/          Core agent loop, tool dispatch, citation tracking
  qx/             NL→ClickHouse SQL subsystem (self-healing reflection loop)
  api/            FastAPI routes, SSE streaming, MCP server
  ingest/         OTel collector configs, processors
  storage/        ClickHouse, Postgres, Redis adapters
  tools/          Agent tools (CH query, trace fetch, code search, runbook RAG, etc.)
  eval/           Eval harness, scoring, golden cases
  schemas/        ClickHouse DDL, Postgres migrations
  models/         Shared Pydantic models
docs/
  architecture.md         Architecture overview
  decisions/              Architecture decision records (ADRs)
  diagrams/
chaos/             Toy chaos backend for synthetic incidents
tests/
helm/
docker/
```

## Build & test commands

```bash
# Setup
uv sync

# Run the stack locally
docker compose up -d

# Lint, format, type-check
ruff check .
ruff format .
mypy src

# Tests
pytest                          # unit tests
pytest -m integration           # integration (requires `docker compose up`)
make eval                       # qx + investigator eval suites

# Run components
uv run periscope-api            # FastAPI control plane
uv run qx "<question>"          # qx CLI
```

## Conventions

### Code style
- **Async-first.** Network I/O, DB calls, LLM calls — all `async def`. Use `asyncio.TaskGroup` for fan-out, `asyncio.Semaphore` for rate limits. No blocking I/O in async paths.
- **Type-checked.** `mypy --strict` is enforced in CI. Annotate everything. Use `from __future__ import annotations`.
- **Pydantic v2** for all external I/O — request/response bodies, LLM structured output, tool inputs/outputs, config.
- **Dataclasses** (frozen, slotted) for internal value types where Pydantic would be overkill.
- **No comments** except where the *why* is non-obvious — a hidden constraint, a workaround, a subtle invariant. Don't explain *what* code does; well-named identifiers do that. Don't reference current tasks or PRs in comments.
- **Errors:** specific exception types, no bare `except`. Errors at boundaries are typed (Pydantic models). Internal code trusts its callers.

### Naming
- Module names: `snake_case`, single-word where possible.
- Public functions: imperative verbs (`embed_chunks`, not `chunks_embedder`).
- Classes: `PascalCase`, no `I`-prefix interfaces.
- Tests: `test_<function>_<behavior>.py`, one assertion focus per test.

### Commits
- **Conventional commits**: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Small, atomic. One logical change per commit.
- No co-author trailers from AI agents unless the human author explicitly asks.

### File scope
- One module = one responsibility. If a file exceeds ~300 lines, it's probably two modules.
- Tests live next to the code: `src/periscope/agent/loop.py` → `tests/agent/test_loop.py`.

## Design constraints (do not auto-modify without discussion)

These directories carry intentional design decisions. Each maps to one or more ADRs in [`docs/decisions/`](docs/decisions/); see the [index](docs/decisions/README.md) for the planned and accepted set.

- `src/periscope/agent/` — Agent loop semantics, citation tracking, tool dispatch invariants.
- `src/periscope/qx/` — NL→SQL reflection loop, schema-RAG retrieval.
- `src/periscope/schemas/` — ClickHouse DDL, partition keys, sort keys, MV definitions. Schema migrations are append-only; never edit historical migrations.
- `src/periscope/eval/scorer.py` — Eval scoring rubric.

If asked to change anything in these paths: check the decision log first. If the governing ADR exists, propose the change as a successor ADR. If no ADR exists yet, draft the missing one before changing the code.

## What agents are explicitly invited to help with

- Boilerplate: Dockerfiles, Compose, Helm, GitHub Actions, table DDL *from a designed schema*, test scaffolding, type stubs.
- Pydantic models from a defined schema.
- FastAPI route handlers, OpenAPI docs, error mapping.
- Postgres migrations.
- README structure (not narrative).
- Test fixtures and parametrization.
- Conventional commit message drafting.
- Refactors that preserve behavior, with passing tests as evidence.

## Where to ask before changing

- Public API surface (URLs, request/response shapes) — breaking changes need an ADR.
- Span schema columns or partition keys.
- Tool input/output schemas (the agent's tool contract).
- Eval rubric or golden-case format.
- Build/test commands and tooling versions.

## Pointers

- Project overview, architecture diagram, comparison: [README.md](README.md)
- Decision log (ADRs): [docs/decisions/](docs/decisions/) — start with the index in [docs/decisions/README.md](docs/decisions/README.md)
- Roadmap: [README.md](README.md#roadmap)