# ADR-002: Data providers and abstractions

- **Status:** Proposed
- **Date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R4, R5, R10, R11

## Context

The daily slate must come from the ESPN API, but the historical sports API
provider is unspecified. Paid providers may be unavailable or expensive, and
tests and demos must run offline.

## Decision

- Use ESPN for the daily D1-vs-D1 slate, odds, and matchup statistics.
- Hide the historical provider behind a `CollegeBasketballProvider` interface.
- Keep raw provider responses cacheable and replayable.
- Provide fixture adapters for every integration so tests and demos run
  without network access.

## Consequences

- The historical provider can change without rewriting agents or services.
- Live calls are isolated to `integrations/*`.
- Fixture maintenance becomes part of the test harness.
- The exact historical provider remains an open decision (Section 15.1).
