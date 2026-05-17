# ADR-0012: Use LangChain and LangGraph for agentic runtime

**Status:** Accepted
**Date:** 2026-05-17
**Deciders:** Alex Kaserbacher

## Summary

Periscope uses LangGraph for agentic workflow orchestration and LangChain for model-facing prompt, structured-output, and tool-adapter plumbing. Periscope keeps its own typed domain contracts for tools, evidence, storage access, replay, and evaluation.

## Context

Periscope needs agentic workflows that can call tools, gather evidence, maintain typed state, retry bounded steps, and produce inspectable results. Prompt composition, structured model output, tool adapters, and graph/state-machine orchestration are common agent-system concerns with maintained open-source support in LangChain and LangGraph.

Periscope also has product-specific boundaries that should remain explicit and typed: `ToolResult`, `EvidenceRef`, workflow result models, guarded storage execution, tool-call recording, replay, and eval outputs.

## Decision

- LangGraph is the default orchestration runtime for Periscope agentic workflows.
- LangChain is the default layer for chat-model invocation, prompt composition, structured output parsing, and framework tool adapters.
- LangChain tools that expose Periscope tools are adapters, not the canonical tool contract.
- Public Periscope APIs should expose Periscope models, not LangChain or LangGraph runtime objects.
- Runtime code should preserve testability with fake chat models and fake tool adapters.

## Consequences

**Positive:**

- Periscope avoids rebuilding generic prompt, structured-output, tool-adapter, and graph orchestration machinery.
- Agentic workflows have a shared runtime approach.
- LangGraph gives Periscope a natural place for validation, repair, bounded retries, and multi-step investigation state machines.
- LangChain components can be used while still returning Periscope-owned typed models.
- Storage safety, typed tool errors, evidence, and recording remain centralized in Periscope.

**Negative / accepted costs:**

- LangChain and LangGraph become runtime dependencies.
- Adapter code is required between dynamic framework objects and Periscope's stricter Pydantic contracts.
- Some LangChain convenience APIs may be less useful because Periscope keeps its own tool and execution boundaries.

**Risks to monitor:**

- Framework updates may change APIs around tools, structured output, or graph execution.
- If LangChain/LangGraph concepts leak into public Periscope APIs, replacing them later becomes harder.
- Evidence and citation tracking may become awkward if future graph nodes treat tool outputs as unstructured messages.

## Alternatives considered

### Custom agentic runtime

This keeps dependencies smaller and all control in Periscope, but risks rebuilding common agent infrastructure before the domain-specific parts are proven.

### Replace Periscope tools with LangChain tools

This could reduce adapter code, but it would move safety policy, typed errors, evidence references, and replay behavior into a generic tool lifecycle. Periscope keeps those as product-owned boundaries.

### Adopt prebuilt LangChain agents wholesale

This gives fast access to working agent loops and tool orchestration. It was not chosen as the core contract because Periscope needs its own typed tool results, evidence references, storage safety, replay, and evaluation boundaries. Prebuilt LangChain agents may still be evaluated as workflow internals later.

## References

- ADR-0006: Agent tool API
