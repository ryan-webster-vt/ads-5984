# NCAA Division I Men's Basketball Upset Condition Profiler

A multi-agent system that researches historical college basketball upsets,
validates the pregame conditions associated with them, monitors a day's
Division I slate, and ranks potential upsets with cited rationale in a
Streamlit front end.

**Course:** ADS 5984 — Agentic AI in Practice
**Project:** Final Project, idea 34.4.13 — Sports: Upset Condition Profiler
**Author:** Ryan Webster

## Status

**Phase 3: real ESPN slate, Streamlit UI, and cache complete.** The repository
contains strict Pydantic contracts for every agent handoff, checked-in JSON
Schemas under `docs/contracts/`, a migrated SQLite schema with typed
repositories, a shared HTTP client base with timeouts/retries/rate limiting,
thread-safe progress events, fixture loaders, and a working daily pipeline. The
fixture path runs through the Monitor-Orchestrator, two parallel Monitor workers,
and the Upset-Scorer, then emits ranked JSON from the `demo` CLI. Phase 3 adds
the live ESPN scoreboard/schedule adapters, D1-vs-D1 slate filtering, SQLite
slate/analysis caching, and a Streamlit UI with filters, stable sorting, game
selection, run controls, progress/agent-activity display, ranked results with
citations, cached-result badges, empty states, and graceful API-error messages.
It does **not** yet contain MCP context, RAG, the real Extractor or statistical
validation, the real Upset-Scorer, or backtesting. Those land in later phases per
`IMPLEMENTATION_PLAN.md`.

## Problem

Analysts and reporters covering college basketball need to identify which games
on a given day are potential upsets. That research is expensive and manual:
dozens of games per day, and meaningful signals include advanced statistics,
travel fatigue, rest days, revenge-game context, look-ahead spots, injuries,
and other pregame conditions. Betting markets are accurate, so the goal is not
to replace them but to surface conditions that models and casual review may
overlook.

This system will:

1. Research historical upsets and collect structured statistics plus cited
   contextual documents.
2. Extract and statistically validate preconditions associated with upsets.
3. Fetch the current day's Division I vs. Division I slate.
4. Monitor selected games against validated preconditions.
5. Flag games with cited rationale and rank them by upset risk using `d3-mini`.
6. Display results and agent activity in Streamlit.
7. Validate upset probabilities offline on held-out seasons.

Out of scope: guaranteed score or margin predictions, real-time in-game
analysis, automated betting, non-Division-I basketball, and player-level
forecasting.

## Why multiple agents

The workflow combines an **orchestrator-worker** pattern for historical
research with a **sequential pipeline** for extraction, monitoring, and
scoring, plus **parallel Monitor workers** for independent per-game evaluation.
This keeps agent contexts focused, enables parallelism, separates evidence
gathering from validation and scoring, and makes every handoff auditable.

The architecture is described in detail in `IMPLEMENTATION_PLAN.md` Sections 1
and 5. The approved sequence diagrams are in `docs/uml/`.

## Architecture summary

| Phase | Main components |
|---|---|
| A. Historical research ingestion | Research-Orchestrator, Historical-Stats, Context-Research, College Basketball API, MCP (Brave/Fetch/Deep Research), Pydantic, SQLite, ChromaDB, `qwen3-embedding:8b` |
| B. Precondition extraction | Extractor, ChromaDB retrieval, `d3-mini` reranker, statistical validation tools, `validated_preconditions` table |
| C. Daily monitoring and scoring | Streamlit UI, ESPN API, Monitor-Orchestrator, parallel Monitor workers, Upset-Scorer, `d3-mini` |
| D. Offline validation | Backtesting, log loss, Brier score, calibration plot, held-out test season |

Design rules:

- Plain Python orchestration; no agent framework.
- Pydantic contracts at every agent boundary.
- Deterministic Python decides flagging and statistical acceptance; LLMs draft
  explanations and score candidates.
- Fixture adapters keep tests and demos offline and reproducible.

## Repository layout

```text
final/
├── config/                 # source policy, seed preconditions, logging
├── data/                   # raw, processed, cache, chroma, db (git-ignored)
├── docs/                   # architecture, assumptions, ADRs, UML
├── scripts/                # administrative pipeline wrappers (later phases)
├── src/upset_profiler/     # application package
├── tests/                  # unit, integration, e2e, evaluation
├── IMPLEMENTATION_PLAN.md  # authoritative plan and traceability
├── pyproject.toml
├── .env.example
└── README.md
```

Module boundaries are documented in `IMPLEMENTATION_PLAN.md` Section 7.

## Requirements

- Python `>=3.11,<3.14` (verified with 3.13).
- VT ARC / OpenAI-compatible endpoints for `d3-mini` and
  `qwen3-embedding:8b`, reachable via eduroam or the Cisco VPN.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
cp .env.example .env
# edit .env with real values; never commit it
```

Install optional extras only when the corresponding phase needs them:

```bash
python3 -m pip install -e ".[dev,ui,stats,rag,mcp]"
```

## Environment variables

`.env.example` is the authoritative template. The existing `.env` key names are
preserved. Never commit `.env`.

| Group | Keys |
|---|---|
| LLM (VT ARC) | `OPEN_AI_ENDPOINT`, `OPEN_AI_API_KEY`, `OPEN_AI_MODEL` |
| LLM overrides | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT_SECONDS` |
| Embeddings | `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE` |
| Decision model | `DECISION_MODEL_BASE_URL`, `DECISION_MODEL_API_KEY`, `DECISION_MODEL_NAME`, `DECISION_MODEL_TIMEOUT_SECONDS` |
| ESPN | `ESPN_BASE_URL`, `ESPN_TIMEOUT_SECONDS` |
| Historical API | `CBB_API_PROVIDER`, `CBB_API_BASE_URL`, `CBB_API_KEY` |
| MCP | `MCP_BRAVE_*`, `MCP_FETCH_*`, `MCP_DEEP_RESEARCH_*`, `BRAVE_API_KEY`, `MCP_TIMEOUT_SECONDS` |
| Storage | `SQLITE_PATH`, `CHROMA_PATH`, `CHROMA_COLLECTION`, `CACHE_TTL_SECONDS` |
| Pipeline | `RESEARCH_BATCH_SIZE`, `MONITOR_WORKER_COUNT`, `MONITOR_MAX_CONCURRENCY`, `RAG_TOP_K`, `RERANK_TOP_N`, `CHUNK_SIZE_TOKENS`, `CHUNK_OVERLAP_TOKENS`, `SOURCE_WHITELIST_PATH`, `LEAKAGE_STRICT` |
| Statistics | `PRECONDITION_ALPHA`, `FDR_ALPHA`, `MIN_SAMPLE_SIZE`, `TRAIN_SEASONS`, `VALIDATION_SEASON`, `TEST_SEASON` |
| Logging | `APP_ENV`, `LOG_LEVEL`, `LOG_FORMAT` |

## Commands

```bash
# Validate configuration and print a redacted summary
python3 -m upset_profiler validate-config

# Strict validation (fails when core settings are missing)
python3 -m upset_profiler validate-config --strict

# Fixture-backed end-to-end demo (offline, no live API calls)
python3 -m upset_profiler demo --date 2025-01-15

# Two parallel workers, progress on stderr, and a subset of games
python3 -m upset_profiler demo --date 2025-01-15 --workers 2 --progress \
  --game-id 401000001 --game-id 401000003

# Write the ranked JSON to a file as well as stdout
python3 -m upset_profiler demo --date 2025-01-15 --output data/processed/ranking.json

# Streamlit UI (fixture mode is the offline default; choose Live ESPN in the sidebar)
python3 -m streamlit run src/upset_profiler/ui/app.py

# Quality gates
python3 -m pytest
ruff check .
mypy src

# Verify checked-in JSON Schemas (regenerate without --check after contract changes)
python3 scripts/export_contracts.py --check
```

Administrative commands `ingest`, `extract`, `monitor`, and `backtest` are
registered but intentionally exit with a "not implemented" message until their
phases land. `demo` is implemented for the fixture-backed slice, and the daily
live path is available through the Streamlit UI.

## Testing

Tests are marked as `unit`, `integration`, `e2e`, `slow`, or `evaluation`.
Fixtures and fake adapters are used so the default suite runs offline.

```bash
python3 -m pytest -m unit
python3 -m pytest -m "not slow and not evaluation"
python3 -m pytest --cov=upset_profiler --cov-report=term-missing
```

## Roadmap


| Phase | Deliverable | Progress |
|---|---|---|
| 0 | Plan, scaffold, config, logging, test infrastructure | ✅ Complete |
| 1 | Pydantic contracts, SQLite schema/repositories, HTTP base, progress events | ✅ Complete |
| 2 | Fixture-backed end-to-end multi-agent slice and `demo` CLI | ✅ Complete |
| 3 | Real ESPN slate, Streamlit UI, cache | ✅ Complete |
| 4 | Historical research ingestion | ⬜ Not started |
| 5 | RAG retrieval and reranking | ⬜ Not started |
| 6 | Extractor and statistical validation | ⬜ Not started |
| 7 | Real Upset-Scorer | ⬜ Not started |
| 8 | Backtesting and validation | ⬜ Not started |
| 9 | Hardening and deliverables | ⬜ Not started |

**Overall Progress:** 4/10 phases complete (40%)


See `IMPLEMENTATION_PLAN.md` Sections 10–11 for acceptance criteria and the
testing strategy, and Section 12 for the requirement traceability matrix.

## Documentation

- `IMPLEMENTATION_PLAN.md` — authoritative architecture, contracts, phases,
  traceability, assumptions, and open decisions.
- `docs/architecture.md` — overview of components and data flow.
- `docs/assumptions.md` — working assumptions.
- `docs/decisions/` — architecture decision records.
- `docs/uml/` — Mermaid class and sequence diagrams.
- `reference_documents/` — instructor specifications and approved architecture.

## Security notes

- `.env` is git-ignored; `.env.example` contains placeholders only.
- Secrets are typed as `SecretStr` and redacted from logs and diagnostics.
- Contextual sources are domain-whitelisted and timestamp-filtered to prevent
  post-tip-off leakage.
- Retrieved web content is treated as untrusted input, never as instructions.
