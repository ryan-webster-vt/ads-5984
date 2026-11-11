# ADR-004: Upset label definition

- **Status:** Proposed (requires confirmation)
- **Date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R12, R14, R25

## Context

The project could define an upset as an outright underdog win or as a favorite
failing to cover the spread. The choice changes labels, training data, and how
results should be interpreted.

## Decision

Define an upset as the **underdog winning outright**, with the favorite
determined by pregame betting odds (moneyline or equivalent). Games without
usable pregame odds are excluded from training and scoring.

## Consequences

- Labels align with the plain meaning of an upset and with the project's
  explanation to users.
- Spread-cover questions are out of scope unless this decision is revisited.
- Odds availability and source quality become dependencies.
- This remains an open decision in `IMPLEMENTATION_PLAN.md` Section 15.3.
