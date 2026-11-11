# ADR-003: Reproducibility and caching policy

- **Status:** Accepted
- **Decision date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R19, R22, R23, R24

## Context

Live web evidence and LLM sampling are nondeterministic. Requirement R19
demands relatively consistent results for repeated analysis of the same
matchups, and reruns should not repeat expensive work.

## Decision

- Store an evidence snapshot per analysis run.
- A rerun of the same analysis reuses that snapshot; a new run may fetch fresh
  live evidence.
- Use deterministic matching and stable sorting with explicit tie-breaking.
- Use temperature `0` where supported and fixed model parameters.
- Key cache entries by normalized game identifiers, evidence snapshot, and
  precondition version.
- Log model names, versions, and run IDs with results.

## Consequences

- Repeated runs are stable and explainable.
- Cached results can become stale by design; invalidation must be explicit.
- Live evidence must be captured and stored with provenance for auditability.
