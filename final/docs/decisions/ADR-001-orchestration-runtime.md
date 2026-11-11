# ADR-001: Plain Python orchestration runtime

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R4, R6, R17, R19, R21, R22

## Context

The system needs orchestrator-worker and parallel per-game execution with
typed handoffs, but it does not need autonomous tool selection loops. Agent
frameworks would add dependencies, hidden control flow, and harder debugging
without solving a requirement that plain Python cannot.

## Decision

Use plain Python orchestration with `ThreadPoolExecutor` for I/O-bound parallel
work, `concurrent.futures` primitives for cancellation and per-game isolation,
and Pydantic models at every agent boundary. Do not use LangChain, CrewAI,
AutoGen, or a similar agent framework.

## Consequences

- Explicit, auditable control flow that is easy to explain at the showcase.
- Fewer dependencies and less hidden behavior.
- The team owns batching, retries, progress, and cancellation directly.
- Provider and agent code must be well structured to avoid duplication.
