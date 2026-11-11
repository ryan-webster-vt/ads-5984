# ADR-005: Historical ingestion providers and storage

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R4–R8, R22, R24

## Context

Phase 4 must ingest historical games and cited pregame documents without
locking the project to an unspecified paid sports API. Tests and demos must run
offline, while the live path should use real providers when configured.

## Decision

- Keep the historical statistics provider behind `CollegeBasketballProvider`.
- Use ESPN's public game-summary endpoint as the first live implementation and
  a fixture provider as the default offline implementation.
- Keep MCP context research behind search/fetch/deep-research protocols, with a
  fixture provider and an optional live SDK client using configured transports.
- Apply source-domain allowlisting and UTC tip-off leakage filtering in the
  Research-Orchestrator, after workers return, so policy decisions do not depend
  on an LLM.
- Chunk documents deterministically within each document and upsert them into
  Chroma when available, otherwise into a deterministic in-memory store.

## Consequences

- The live historical provider can change without touching agents or services.
- The default `ingest` command remains offline and reproducible.
- Policy violations and invalid records are quarantined with structured errors.
- Chroma and MCP are optional extras; missing dependencies degrade to fixtures
  or a clear configuration error rather than crashing the whole application.
- The exact paid-provider choice remains open for later phases.

## Amendment (2026-11-10): live payload shapes

Two defects surfaced while ingesting the full 2021-2022 season from ESPN.

1. **Odds.** ESPN summary payloads put betting lines in a top-level `pickcenter`
   (or `odds`) list, not on `header.competitions[0]`. The parser only read the
   competition, so all 5,976 ingested games had `odds=None`, which makes them
   unusable for upset labelling (Assumptions 1–2). `parse_espn_summary` now
   attaches the first usable entry from those top-level lists, and a competition
   that already carries odds still wins, so scoreboard payloads and fixtures are
   unchanged. `_moneyline_close` also falls back to `<side>TeamOdds.moneyLine`,
   because summary moneylines live there instead of `moneyline.<side>.close`.

2. **MCP stdio transport.** `StdioServerParameters` was built with `args=[]` and
   `env=None`. That made an argument-bearing command
   (`npx -y @modelcontextprotocol/server-brave-search`) fail with
   `FileNotFoundError`, and the SDK's default environment allowlist
   (`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`) hid `BRAVE_API_KEY` from
   the server. The transport now splits the configured command with
   `shlex.split` and forwards the parent environment with `BRAVE_API_KEY`
   injected from settings. The key is never logged or persisted outside `.env`.

Neither change alters a Pydantic contract, so the checked-in JSON Schemas are
unaffected.
