# Architecture Overview

This document is a short orientation to the system. The authoritative plan,
contracts, phases, and traceability matrix live in `IMPLEMENTATION_PLAN.md`.
The approved requirement and architecture write-up is
`reference_documents/final_project_reqs_and_arch.qmd`.

## Goal

Identify which NCAA Division I men's basketball games are potential upsets by
learning preconditions from historical upsets, validating them statistically,
and monitoring the current day's slate against those validated signals.

## Design in one paragraph

The system combines an **orchestrator-worker** pattern for one-time historical
research with a **sequential pipeline** for extraction, monitoring, and scoring,
plus **parallel Monitor workers** for independent per-game evaluation. The
Research-Orchestrator fans historical work out to Historical-Stats (structured
box scores from a sports API) and Context-Research (cited documents through
MCP tools), joins results by `game_id`, and validates every record with
Pydantic. Structured data goes to SQLite; documents are chunked, embedded with
`qwen3-embedding:8b`, and stored in ChromaDB. The Extractor tests candidate
preconditions with deterministic statistics and persists accepted signals in a
separate table. At runtime, the Monitor-Orchestrator partitions the selected
slate across workers, which compare evidence to validated preconditions and
produce cited flags; the Upset-Scorer ranks flagged games with `d3-mini`, and
Streamlit displays the slate, progress, rankings, and citations.

## Components

| Layer | Components | Responsibility |
|---|---|---|
| UI | Streamlit app and components | Slate display, filters/sorting/selection, run control, progress, results, citations |
| Agents | Research-Orchestrator, Historical-Stats, Context-Research, Extractor, Monitor-Orchestrator, Monitor workers, Upset-Scorer | Role-specific work with typed handoffs |
| Orchestration | Runtime, progress, pipelines | Batching, thread pools, cancellation, per-game isolation, progress events |
| Services | Slate, ingestion, extraction, monitoring, scoring, caching, backtesting | Use cases composed from agents and integrations |
| Contracts | `models/*` Pydantic modules | Strict schemas for every boundary and stored record |
| Storage | SQLite, ChromaDB | Structured records, research, preconditions, runs, cache, quarantine; cited documents |
| Integrations | LLM, embeddings, decision model, ESPN (`espn.py`), College Basketball API, MCP | All network access with timeouts, retries, and rate limiting |
| RAG | Chunking, retrieval, reranking | Recursive chunking, filtered retrieval, `d3-mini` reranking |
| Security | Whitelist, leakage filter, citation verifier | Prompt-injection resistance, no post-tip-off data, supported claims |
| Statistics | Validation, metrics, backtesting | Effect sizes, confidence intervals, significance, FDR, out-of-sample tests, log loss, Brier score |

## Phase 2 implementation status

The fixture-backed slice of the daily path remains the offline demo and test
path. `FixtureSlateProvider`, `FixtureContextProvider`, and
`FixtureDecisionModel` supply deterministic inputs. The `MonitorOrchestrator`
loads validated preconditions, partitions selected games across parallel
`MonitorWorker` instances, and aggregates cited `GameFlag` records. Workers
evaluate `GameContext.features` against precondition comparisons in
deterministic Python; the `UpsetScorer` then ranks flags with a fake decision
model. The `MonitoringService` and daily pipeline compose the stages, persist
results when repositories are supplied, and the `demo` CLI exercises the full
path.

## Phase 3 implementation status

`integrations/espn.py` owns the live slate path: `EspnSlateProvider` converts
the ESPN scoreboard into validated `DailySlate`/`DailyGame` contracts with odds,
and `EspnContextProvider` derives rest and back-to-back features from team
schedules. `services/slate.py` still enforces the D1-vs-D1 policy.
`services/cache.py` adds a typed SQLite cache for slate payloads and exact
analysis requests; cache keys include the date, sorted selected game ids, worker
count, and a fingerprint of the validated preconditions.

The Streamlit front end (`ui/app.py`, `ui/state.py`, `ui/components/*`) shows
the slate with free-text, conference, and status filters plus stable sorting;
all visible games are selected by default and users can narrow the selection
before pressing Execute Search. It renders a progress bar, an ordered
agent-activity table, ranked alerts with rationale and citations, a cached-result
badge, an explicit empty state, and user-facing fetch/run errors. Fixture mode is
the offline default; live mode fetches ESPN and uses schedule-derived features.
MCP context, travel features, RAG, the extractor, the real Upset-Scorer, and
backtesting remain later phases.

## Phase flow

### A. Historical research ingestion (one-time, admin)

Research-Orchestrator → batches of game IDs → parallel Historical-Stats and
Context-Research → join by `game_id` → Pydantic validation → valid records to
SQLite, cited documents to ChromaDB, invalid records to quarantine.

### B. Statistical precondition extraction (one-time, admin)

Extractor → per candidate precondition: embed query, retrieve evidence from
ChromaDB, rerank with `d3-mini`, run deterministic statistical validation →
accept or reject → validated preconditions in their own SQLite table.

### C. Daily monitoring and scoring (user-initiated)

Streamlit → ESPN slate (D1 vs. D1 only) → user selection → Monitor-Orchestrator
loads validated preconditions, partitions games across parallel workers →
workers retrieve matchup data and pregame context, flag matches with citations →
orchestrator aggregates → Upset-Scorer ranks flagged games with `d3-mini` →
Streamlit displays ranked results and agent activity.

### D. Offline backtesting

Leakage-safe features, train on 2021–22/2022–23/2023–24, validate on 2024–25,
test once on 2025–26, report log loss, Brier score, and a calibration plot.

## Reliability principles

1. Deterministic validation decides correctness, not agent consensus.
2. Independent workers evaluate isolated game batches.
3. Different workers use genuinely different tools and data.
4. Preconditions are falsified against null hypotheses before acceptance.
5. A separate judge scores candidates and a held-out season verifies the model.
6. Provenance, timestamps, and run IDs accompany important claims.
7. Rejected preconditions and missing-data reasons are retained.
8. Results are measured against baselines, not just self-reported.

See `IMPLEMENTATION_PLAN.md` Section 13 for the full mapping to the ten
principles for reliable multi-agent systems.

## Boundaries

- `models/*` contain contracts only; they perform no I/O.
- `integrations/*` own all network access.
- `storage/*` owns all SQLite and Chroma access.
- `services/*` hold use cases; `agents/*` hold agent roles.
- `ui/*` is Streamlit; pipelines contain no UI or provider-specific code.
