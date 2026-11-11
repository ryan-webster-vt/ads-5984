# Implementation Plan — NCAA Division I Men's Basketball Upset Condition Profiler

**Course:** ADS 5984 — Agentic AI in Practice  
**Project:** Final Project, idea 34.4.13 — Sports: Upset Condition Profiler  
**Author:** Ryan Webster  
**Status:** Phase 4 complete (historical research ingestion). Phase 0 delivered the plan and scaffold; Phase 1 delivered strict Pydantic contracts with checked-in JSON Schemas, the SQLite schema/migrations/repositories, the shared HTTP client base, progress events, fixture loaders, and offline tests; Phase 2 delivered deterministic fixture adapters, the Agent base/runtime, Monitor-Orchestrator with parallel Monitor workers, Upset-Scorer, slate/scoring/monitoring services, the daily pipeline, and the `demo` CLI; Phase 3 delivered the ESPN scoreboard/schedule adapters, slate filtering/sorting/selection in Streamlit, agent-activity and ranked-result rendering, graceful API-failure display, and SQLite-backed slate/analysis caching; Phase 4 delivered fixture/ESPN historical providers, optional MCP context research, source whitelisting, tip-off leakage filtering, recursive chunking, embeddings, SQLite/Chroma persistence, quarantine handling, the Research-Orchestrator with parallel workers, and the `ingest` CLI. RAG reranking, statistical validation, the real Upset-Scorer, and backtesting remain later phases.

---

## 0. Purpose of This Document

This document translates the requirements and architecture in:

- `reference_documents/final_project_reqs_and_arch.qmd`
- `reference_documents/final_project_specifications.md`
- `reference_documents/10 Principles for Reliable Multi-Agent Systems.pdf`

into an implementation plan, repository structure, data contracts, phased roadmap, testing strategy, and traceability matrix.

The proposed architecture is preserved unless a compelling implementation reason requires a change. Any such change or unresolved decision is documented explicitly in Sections 12 and 13.

---

## 1. Project Understanding

### 1.1 Problem and user

The project is an **NCAA Division I Men's Basketball Upset Condition Profiler** built with a **multi-agent architecture**.

The primary user is an analyst or reporter who covers college basketball and needs to identify which games on a given day are potential upsets. Today, that research is expensive and manual: there are dozens of games per day, and meaningful upset signals can include advanced statistics, travel fatigue, rest days, revenge-game context, look-ahead spots, injuries, and other pregame conditions.

The system will:

1. Research historical upsets and collect structured statistics plus cited contextual documents.
2. Extract and statistically validate preconditions that are actually associated with upsets.
3. Fetch the current day's Division I vs. Division I slate.
4. Monitor selected games against validated preconditions.
5. Flag games with cited rationale.
6. Rank flagged games by upset risk using the `d3-mini` decision model.
7. Display results and agent activity in Streamlit.
8. Validate the upset probabilities offline using historical seasons.

### 1.2 Scope

**In scope:**

- Historical upset research and ingestion.
- Contextual document collection with citations and timestamps.
- Statistical precondition extraction and validation.
- Daily matchup monitoring.
- Upset risk scoring and ranking.
- Multi-agent orchestration with progress reporting.
- Explainable recommendations with citations.
- Historical backtesting and calibration.
- Streamlit front-end.

**Out of scope:**

- Guaranteed score predictions or scoring margins.
- Real-time in-game analysis.
- Automated sports betting.
- Functionality outside NCAA Division I Men's Basketball.
- Player-level forecasting.

### 1.3 Why this needs multiple agents

A single prompt or script would face several problems:

- Context overflow from combining historical research, statistics, documents, preconditions, and daily matchup analysis.
- No clean way to parallelize the day's games.
- Weak separation between evidence gathering, validation, and scoring.
- Poor auditability and debugging.
- Higher risk of hallucinated or unsupported claims crossing stage boundaries.

The architecture therefore uses:

- An **orchestrator-worker** pattern for historical research.
- A **sequential pipeline** for extraction, monitoring, and scoring.
- **Parallel Monitor workers** for independent per-game evaluation.
- **Deterministic validation** via Pydantic and statistical tools.
- **Provenance and citations** on every important claim.

**Agency level:** Multi-agent design with orchestrator-worker workflow and sequential pipeline stages.

---

## 2. Requirements Inventory

The reference document contains 25 numbered requirements. They are grouped below for planning; the traceability matrix in Section 11 maps each one to implementation components and tests.

### 2.1 Functional requirements

| Group | Req IDs | Summary |
|---|---|---|
| UI and user control | R1–R3 | Streamlit UI; sort/filter/select games; rerun any combination of games |
| Historical research | R4–R7 | Research-Orchestrator + workers; MCP Brave/Fetch/Deep Research + sports API; parallel batches; join on `game_id`; Pydantic validation |
| Storage and RAG | R8 | SQLite for structured statistics; ChromaDB for cited documents; decision-model reranking |
| Precondition extraction | R9 | Extractor agent plus deterministic statistical validation |
| Daily slate | R10–R11 | ESPN API batch pipeline; D1 vs. D1 only; team/player statistics and odds; UI slate |
| Monitoring | R12–R13, R17 | Monitor agent flags upset candidates with rationale; parallel Monitor workers |
| Scoring | R14, R18 | Upset-Scorer uses `d3-mini`; cited rationale plus score |
| Research persistence | R15 | Deep-Researcher/Context-Research JSON in database; Extractor writes separate table |
| Caching | R23 | Cache matchups that have already been analyzed |
| Leakage prevention | R24 | No information known after tip-off; timestamp filtering |
| Model validation | R25 | Train 2021–2024; validate 2024–25; test 2025–26; log loss, Brier score, calibration plot |

### 2.2 Nonfunctional requirements

| Req IDs | Summary |
|---|---|
| R16 | Well-structured code with descriptive comments and run instructions |
| R19 | High reliability and consistent results on repeated matchups |
| R20 | `.env` kept out of Git; `.env.example` provided |
| R21 | Pydantic-standardized JSON output to prevent broken handoffs |
| R22 | Graceful handling of API failures, LLM timeouts, and MCP failures |
| R6, R17 | Parallelism for research and monitoring |
| Cross-cutting | Security/prompt-injection resistance, provenance, auditability, cost control, testability, modularity |

### 2.3 Showcase and submission requirements

From the instructor specifications:

- High-level problem overview.
- Design overview explaining why the solution requires multiple agents.
- UML class diagrams for agents and collaborators.
- Sequence diagrams showing the main agentic loop and interactions over time.
- Python source code with descriptive in-line comments.
- A `.zip` of the project source with a README containing run instructions and expected `.env` keys, excluding `.env`.
- A PDF of the UML class and sequence diagrams.
- Live showcase demo and Q&A readiness.

---

## 3. Architectural Components

| Component | Responsibility | Requirements |
|---|---|---|
| Streamlit UI | Display slate, filters/sorting/selection, run control, progress, results, citations | R1–R3, R11, R18 |
| Research-Orchestrator | Decompose historical research into batches; aggregate; validate; persist | R4, R6, R7, R8 |
| Historical-Stats worker | Retrieve historical box scores and structured statistics from the sports API | R4–R5 |
| Context-Research worker | Retrieve reports, news, and contextual evidence through MCP; preserve citations and timestamps | R4–R5, R24 |
| College Basketball API | Historical game/team/player statistics | R4–R5, R10 |
| MCP servers | Brave Search, Fetch, Deep Research | R5 |
| Pydantic validator | Validate agent handoffs and stored records | R7, R21 |
| SQLite | Structured games, research records, preconditions, runs, cache, quarantine | R8, R15, R23 |
| ChromaDB | Cited unstructured documents and chunks | R8 |
| `qwen3-embedding:8b` | Chunk and query embeddings | R8 |
| `d3-mini` | RAG reranker and Upset-Scorer decision model | R8, R14 |
| Extractor agent | Generate/test candidate preconditions and accept/reject them | R9, R15 |
| Statistical validation tools | Effect sizes, confidence intervals, significance, out-of-sample validity | R9, R25 |
| ESPN API | Daily slate, team statistics, odds, matchup data | R10–R11 |
| Monitor-Orchestrator | Retrieve validated preconditions; partition games; aggregate results | R12, R17 |
| Monitor workers (default 2) | Evaluate games against validated preconditions and produce cited rationale | R12–R13, R17 |
| Upset-Scorer | Score and rank flagged games using `d3-mini` | R14, R18 |
| Cross-cutting infrastructure | Config, logging, progress events, cache, source whitelist, leakage filter, citation verifier | R16, R19–R24 |

### 3.1 Naming ambiguity

R9 and R15 refer to a **Deep-Researcher** agent, while the architecture calls the worker **Context-Research**. For this plan, "Deep-Researcher" is treated as the Context-Research worker combined with the Deep Research MCP capability. This will be recorded as an assumption.

---

## 4. Rubric Mapping

| Rubric category | Points | Planned evidence |
|---|---:|---|
| Problem definition & motivation | 100 | README problem/scope section, this plan, `docs/architecture.md` |
| Multi-agent design justification | 150 | Architecture document, agent role table, ADRs, explicit single-agent comparison |
| UML class diagrams | 100 | `docs/uml/class_diagram.mmd` plus rendered PDF |
| Sequence diagram(s) | 100 | Refined versions of the three diagrams from the `.qmd`, rendered PDF |
| Functional multi-agent solution | 200 | Phases 2–8; fixture and live end-to-end paths |
| Streamlit front-end | 100 | Phase 3 UI plus Streamlit `AppTest` |
| Code quality & in-line comments | 100 | Ruff/mypy, docstrings, module boundaries, README |
| Showcase demo | 75 | Demo script, fixture fallback, runbook |
| Q&A / depth of understanding | 75 | Architecture/ADR documentation and walkthrough notes |

---

## 5. Proposed Architecture

The proposed architecture is retained with the following structure.

### 5.1 Phase A — Historical research ingestion

- One-time administrative process, not initiated by the user.
- `Research-Orchestrator` decomposes work into batches of game IDs.
- `Historical-Stats` retrieves structured box scores and statistics from the College Basketball API.
- `Context-Research` uses MCP tools (Brave Search, Fetch, Deep Research) to collect reports, news, and pregame context.
- Both worker types run in parallel per batch.
- The orchestrator joins results by `game_id` and validates them with Pydantic.
- Valid records go to SQLite; cited documents are chunked, embedded with `qwen3-embedding:8b`, and stored in ChromaDB.
- Invalid records are quarantined with error details.

### 5.2 Phase B — Statistical precondition extraction

- One-time administrative process.
- Extractor retrieves historical games and candidate preconditions from SQLite.
- For each candidate precondition:
  - Embed the precondition query.
  - Retrieve related historical evidence from ChromaDB.
  - Rerank with `d3-mini`.
  - Submit statistical and document evidence to deterministic statistical validation tools.
  - Estimate effect sizes and confidence intervals.
  - Test statistical significance.
  - Evaluate out-of-sample predictive validity.
  - Accept or reject the precondition with justification and citations.
- Accepted preconditions are stored in a separate validated-preconditions table.
- Rejected preconditions are retained with reasons for auditability.

### 5.3 Phase C — Daily matchup monitoring and upset scoring

- User opens the Streamlit UI.
- UI retrieves the day's D1 vs. D1 slate from ESPN.
- UI displays sortable and filterable games.
- User selects games (all selected by default) and starts analysis.
- Monitor-Orchestrator retrieves validated preconditions from SQLite.
- Monitor-Orchestrator partitions games across workers (default: two).
- Each worker, independently and in parallel:
  - Retrieves current matchup data from ESPN.
  - Researches pregame context through MCP.
  - Compares evidence against validated preconditions.
  - Flags matching games with cited rationale.
- Orchestrator aggregates and deduplicates results.
- If games are flagged, Upset-Scorer uses `d3-mini` to estimate upset risk and rank games.
- UI displays ranked results, rationale, citations, and agent progress.
- If no games are flagged, UI displays that no qualifying upset alerts were found.

### 5.4 Phase D — Offline backtesting and validation

- Build historical predictions using leakage-safe pregame features.
- Train on 2021–2022, 2022–2023, and 2023–2024.
- Validate on 2024–2025.
- Test on 2025–2026.
- Report log loss, Brier score, and calibration plot.
- Compare against reasonable baselines.
- Keep the test season untouched during tuning.

### 5.5 Implementation choices that preserve the architecture

- Use plain Python orchestration rather than LangChain, CrewAI, AutoGen, or another agent framework.
- Use `ThreadPoolExecutor` for I/O-bound parallel work.
- Use a thread-safe progress sink for Streamlit.
- Make Monitor signal matching deterministic Python against validated thresholds.
- Allow an LLM to draft cited explanations, but let deterministic checks decide whether a game is flagged.
- Use Pydantic contracts at every agent boundary.
- Use fixture adapters so the full system can run offline for tests and demos.

---

## 6. Proposed Directory Structure

```text
final/
├── README.md
├── IMPLEMENTATION_PLAN.md
├── pyproject.toml
├── .gitignore
├── .env.example
├── config/
│   ├── sources.yaml              # whitelisted domains / source policy
│   ├── preconditions.yaml        # seed candidate preconditions and thresholds
│   └── logging.yaml              # optional logging configuration
├── data/
│   ├── raw/.gitkeep
│   ├── processed/.gitkeep
│   ├── cache/.gitkeep
│   ├── chroma/.gitkeep
│   └── db/.gitkeep
├── docs/
│   ├── architecture.md
│   ├── assumptions.md
│   ├── decisions/
│   │   ├── ADR-001-orchestration-runtime.md
│   │   ├── ADR-002-data-providers.md
│   │   ├── ADR-003-reproducibility.md
│   │   └── ADR-004-upset-label.md
│   ├── contracts/                 # generated JSON schemas (Phase 1)
│   ├── reports/.gitkeep
│   └── uml/
│       ├── class_diagram.mmd
│       ├── sequence_research.mmd
│       ├── sequence_extraction.mmd
│       ├── sequence_monitoring.mmd
│       └── README.md
├── scripts/
│   ├── ingest_history.py
│   ├── extract_preconditions.py
│   ├── run_monitor.py
│   └── backtest.py
├── src/
│   └── upset_profiler/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── context.py
│       ├── errors.py
│       ├── logging_config.py
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── research_orchestrator.py
│       │   ├── historical_stats.py
│       │   ├── context_research.py
│       │   ├── extractor.py
│       │   ├── monitor_orchestrator.py
│       │   ├── monitor_worker.py
│       │   └── upset_scorer.py
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── runtime.py
│       │   ├── progress.py
│       │   └── pipelines/
│       │       ├── __init__.py
│       │       ├── ingestion.py
│       │       ├── extraction.py
│       │       └── monitoring.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── slate.py
│       │   ├── ingestion.py
│       │   ├── extraction.py
│       │   ├── monitoring.py
│       │   ├── scoring.py
│       │   ├── caching.py
│       │   └── backtesting.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── common.py
│       │   ├── games.py
│       │   ├── research.py
│       │   ├── evidence.py
│       │   ├── preconditions.py
│       │   ├── monitoring.py
│       │   ├── scoring.py
│       │   ├── validation.py
│       │   └── progress.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── sqlite.py
│       │   ├── repositories.py
│       │   ├── schema.sql
│       │   └── chroma.py
│       ├── integrations/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── fakes.py            # deterministic offline adapters (Phase 2)
│       │   ├── llm.py
│       │   ├── embeddings.py
│       │   ├── decision_model.py
│       │   ├── espn.py
│       │   ├── college_basketball.py
│       │   └── mcp.py
│       ├── rag/
│       │   ├── __init__.py
│       │   ├── chunking.py
│       │   ├── retrieval.py
│       │   └── rerank.py
│       ├── security/
│       │   ├── __init__.py
│       │   ├── source_whitelist.py
│       │   ├── leakage.py
│       │   └── citation_verifier.py
│       ├── stats/
│       │   ├── __init__.py
│       │   ├── validation.py
│       │   ├── metrics.py
│       │   └── backtest.py
│       └── ui/
│           ├── __init__.py
│           ├── app.py
│           ├── state.py
│           └── components/
│               ├── __init__.py
│               ├── slate_table.py
│               ├── progress_panel.py
│               ├── results_panel.py
│               └── citations.py
└── tests/
    ├── conftest.py
    ├── fakes.py
    ├── fixtures/
    │   ├── espn/
    │   ├── college_basketball/
    │   ├── mcp/
    │   ├── llm/
    │   ├── decision_model/
    │   ├── historical/
    │   └── slates/
    ├── unit/
    ├── integration/
    ├── e2e/
    └── evaluation/
```

---

## 7. Module Responsibilities

| Package/module | Responsibility |
|---|---|
| `config.py` | Typed settings groups, `.env` loading, path resolution, fail-fast validation |
| `logging_config.py` | Structured logging setup, log levels, redaction of secrets |
| `context.py` | Run IDs, agent context, provenance helpers |
| `errors.py` | Domain exception hierarchy with retryability metadata |
| `models/*` | Pydantic contracts only; no I/O |
| `agents/base.py` | Shared agent protocol, input/output typing, logging/progress helpers |
| `agents/research_orchestrator.py` | Historical research batching, aggregation, validation, persistence |
| `agents/historical_stats.py` | Structured historical statistics retrieval |
| `agents/context_research.py` | MCP-based contextual research and citation collection |
| `agents/extractor.py` | Candidate precondition testing and acceptance/rejection |
| `agents/monitor_orchestrator.py` | Precondition loading, game partitioning, worker aggregation |
| `agents/monitor_worker.py` | Per-game evidence matching and flagging with rationale |
| `agents/upset_scorer.py` | Upset probability scoring and ranking |
| `orchestration/runtime.py` | Batching, thread pool, cancellation, per-game isolation |
| `orchestration/progress.py` | `ProgressSink` protocol and logging/queue implementations |
| `orchestration/pipelines/*` | Phase composition; no UI or provider-specific code |
| `services/slate.py` | Daily slate retrieval, filtering, sorting, selection |
| `services/ingestion.py` | Historical ingestion use case |
| `services/extraction.py` | Precondition extraction use case |
| `services/monitoring.py` | Daily monitoring use case |
| `services/scoring.py` | Upset scoring use case |
| `services/caching.py` | Analysis cache keys, lookup, invalidation |
| `services/backtesting.py` | Historical backtest use case |
| `storage/sqlite.py` | Connection factory, migration runner, transaction helpers |
| `storage/repositories.py` | Repositories for games, research, preconditions, runs, cache, quarantine |
| `storage/schema.sql` | SQLite DDL and indexes |
| `storage/chroma.py` | Chroma collection management, filtered upsert/query |
| `integrations/base.py` | HTTP client factory, timeouts, retries, rate limiting, redaction |
| `integrations/fakes.py` | Deterministic fixture-backed slate, context, and decision-model adapters for offline demos/tests |
| `integrations/llm.py` | OpenAI-compatible chat/structured-output client |
| `integrations/embeddings.py` | `qwen3-embedding:8b` batch embeddings |
| `integrations/decision_model.py` | `d3-mini` reranking/scoring adapter and score normalization |
| `integrations/espn.py` | Daily slate, D1 filtering, odds, team/player statistics |
| `integrations/college_basketball.py` | Historical sports API provider abstraction |
| `integrations/mcp.py` | MCP client manager for Brave/Fetch/Deep Research |
| `rag/chunking.py` | Recursive chunking within documents; deterministic chunk IDs |
| `rag/retrieval.py` | Query embedding and Chroma retrieval with filters |
| `rag/rerank.py` | Decision-model reranking, deduplication, citation assembly |
| `security/source_whitelist.py` | Domain allowlist and URL canonicalization |
| `security/leakage.py` | Timestamp-based post-tip-off filtering |
| `security/citation_verifier.py` | Deterministic check that claims have supporting citations |
| `stats/validation.py` | Effect sizes, confidence intervals, significance, FDR, out-of-sample tests |
| `stats/metrics.py` | Log loss, Brier score, calibration bins, reliability metrics |
| `stats/backtest.py` | Season splits, leakage-safe backtesting, report generation |
| `ui/app.py` | Streamlit entry point |
| `ui/state.py` | Typed session state |
| `ui/components/*` | Slate table, progress panel, results panel, citations |
| `cli.py` | Admin commands: `ingest`, `extract`, `monitor`, `backtest`, `demo`, `validate-config` |
| `scripts/*` | Thin wrappers for administrative pipelines |

---

## 8. Dependencies and Environment

### 8.1 Python and dependency policy

- Python `>=3.11,<3.14`.
- Use a `src/` layout and `pyproject.toml`.
- No unnecessary agent frameworks. Plain Python orchestration with Pydantic contracts.

**Core dependencies:**

- `pydantic`
- `pydantic-settings`
- `httpx`
- `tenacity`
- `PyYAML`

**Optional extras:**

- `ui`: `streamlit`
- `stats`: `numpy`, `pandas`, `scipy`, `statsmodels`
- `rag`: `chromadb`, `openai` or a thin OpenAI-compatible client
- `mcp`: `mcp`
- `dev`: `pytest`, `pytest-cov`, `pytest-asyncio`, `pytest-timeout`, `respx`, `freezegun`, `ruff`, `mypy`, `types-PyYAML`

### 8.2 Environment variables

The existing `.env` contains VT ARC / OpenAI-compatible keys. `.env.example` will use placeholders and preserve the existing naming. Secrets will use `SecretStr` and be redacted from logs.

```dotenv
# VT ARC / OpenAI-compatible LLM
OPEN_AI_ENDPOINT=
OPEN_AI_API_KEY=
OPEN_AI_MODEL=

# Optional LLM overrides
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TEMPERATURE=0
LLM_TIMEOUT_SECONDS=60

# Embeddings
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=qwen3-embedding:8b
EMBEDDING_BATCH_SIZE=32

# Decision model
DECISION_MODEL_BASE_URL=
DECISION_MODEL_API_KEY=
DECISION_MODEL_NAME=d3-mini
DECISION_MODEL_TIMEOUT_SECONDS=60

# ESPN
ESPN_BASE_URL=https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball
ESPN_TIMEOUT_SECONDS=30

# Historical sports API
CBB_API_PROVIDER=
CBB_API_BASE_URL=
CBB_API_KEY=

# MCP
MCP_BRAVE_TRANSPORT=stdio
MCP_BRAVE_COMMAND=
MCP_BRAVE_URL=
BRAVE_API_KEY=
MCP_FETCH_COMMAND=
MCP_FETCH_URL=
MCP_DEEP_RESEARCH_COMMAND=
MCP_DEEP_RESEARCH_URL=
MCP_TIMEOUT_SECONDS=120

# Tavily (default Context-Research search backend, ADR-006)
TAVILY_API_KEY=
TAVILY_BASE_URL=https://api.tavily.com
TAVILY_SEARCH_DEPTH=basic
TAVILY_TOPIC=general
TAVILY_MAX_RESULTS=5
TAVILY_INCLUDE_RAW_CONTENT=true
TAVILY_KEYLESS=false
TAVILY_TIMEOUT_SECONDS=30

# Storage
SQLITE_PATH=./data/db/upset_profiler.db
CHROMA_PATH=./data/chroma
CHROMA_COLLECTION=research_documents
CACHE_TTL_SECONDS=86400

# Pipeline controls
RESEARCH_BATCH_SIZE=10
MONITOR_WORKER_COUNT=2
MONITOR_MAX_CONCURRENCY=4
RAG_TOP_K=20
RERANK_TOP_N=8
CHUNK_SIZE_TOKENS=800
CHUNK_OVERLAP_TOKENS=120
SOURCE_WHITELIST_PATH=./config/sources.yaml
LEAKAGE_STRICT=true

# Statistical validation
PRECONDITION_ALPHA=0.05
FDR_ALPHA=0.05
MIN_SAMPLE_SIZE=30
TRAIN_SEASONS=2021-2022,2022-2023,2023-2024
VALIDATION_SEASON=2024-2025
TEST_SEASON=2025-2026

# Logging
APP_ENV=development
LOG_LEVEL=INFO
LOG_FORMAT=console
```

### 8.3 SQLite schema outline

Core tables:

- `schema_migrations`
- `games`
- `source_documents`
- `research_runs`
- `quarantine`
- `candidate_preconditions`
- `validated_preconditions`
- `analysis_runs`
- `monitor_results`
- `upset_scores`
- `cache_entries`
- `search_queries`
- `search_hits`
- `backtest_predictions`

Use normalized core fields plus JSON payload columns for provider-specific statistics. Store JSON booleans/dates consistently and keep timestamps in UTC.

`search_queries` stores one row per normalized provider request (`query_hash`,
normalized text, provider, parameter fingerprint, game/season, timestamps,
nullable `expires_at`, result/credit counts, diagnostics payload).
`search_hits` stores one row per returned URL with canonical URL, domain, title,
score, publication/retrieval timestamps, content/hash, and JSON metadata,
cascading from `search_queries`. Both are populated by the SQLite search cache
(ADR-006); Chroma holds only chunked `SourceDocument` text for semantic recall.

### 8.4 ChromaDB collections

Primary collection: `research_documents`.

Required metadata:

- `game_id`
- `season`
- `source_url`
- `source_domain`
- `published_at` (epoch seconds for filtering)
- `retrieved_at`
- `document_hash`
- `chunk_index`
- `whitelisted`

Retrieval must support filtering by game, season, and publication time relative to tip-off.

---

## 9. Shared Pydantic Schemas and Data Contracts

All models inherit a strict base with `extra="forbid"`. Aggregate models include a `schema_version` field. Contracts are serialized with `model_dump(mode="json")`.

| Model module | Key contracts |
|---|---|
| `common.py` | `TeamRef`, `Citation`, `Provenance`, `ValidationIssue`, `AgentError`, `RunContext` |
| `games.py` | `GameOdds`, `TeamStatistics`, `PlayerStatistics`, `HistoricalGame`, `DailyGame`, `DailySlate` |
| `research.py` | `ResearchBatch`, `HistoricalStatsResult`, `SourceDocument`, `ContextResearchResult`, `SearchHitRecord`, `CachedSearchResponse`, `ResearchRecord`, `ResearchAggregate` |
| `evidence.py` | `DocumentChunk`, `RetrievedEvidence`, `RerankedEvidence`, `Claim` |
| `preconditions.py` | `CandidatePrecondition`, `EffectSize`, `StatisticalTest`, `OutOfSampleResult`, `PreconditionDecision`, `ValidatedPrecondition`, `ExtractionResult` |
| `monitoring.py` | `MonitorRequest`, `GameContext`, `SignalMatch`, `GameFlag`, `MonitorWorkerResult`, `MonitorAggregate` |
| `scoring.py` | `ScoringRequest`, `UpsetScore`, `UpsetRanking` |
| `validation.py` | `BacktestPrediction`, `CalibrationBin`, `EvaluationMetrics`, `BacktestReport` |
| `progress.py` | `ProgressEvent` |

### 9.1 Handoff contracts

| Producer → Consumer | Contract |
|---|---|
| UI → Monitor-Orchestrator | `MonitorRequest` |
| Monitor-Orchestrator → Monitor worker | `MonitorAssignment` / `MonitorRequest` |
| Monitor worker → Monitor-Orchestrator | `MonitorWorkerResult` |
| Monitor-Orchestrator → Upset-Scorer | `ScoringRequest` |
| Upset-Scorer → UI | `UpsetRanking` |
| Research-Orchestrator → Historical-Stats | `ResearchBatch` |
| Historical-Stats → Research-Orchestrator | `HistoricalStatsResult` |
| Research-Orchestrator → Context-Research | `ResearchBatch` |
| Context-Research → Research-Orchestrator | `ContextResearchResult` |
| Research-Orchestrator → storage | `ResearchAggregate` / `ResearchRecord` |
| Extractor → SQLite | `ExtractionResult` / `ValidatedPrecondition` |
| Backtest → report | `BacktestReport` |

### 9.2 Contract rules

- Citations are required for every flagged game.
- Citations are required for every accepted precondition.
- Rejected preconditions retain justification.
- Timestamps are stored in UTC.
- `published_at` is required for leakage checks in strict mode.
- Every agent output must validate before being stored or passed onward.
- Contract tests compare agent outputs against golden fixtures.
- Export JSON schemas to `docs/contracts/` in Phase 1.
- `GameContext.features` carries deterministic pregame feature values keyed by
  validated-precondition feature names. It is the typed input to Monitor rule
  evaluation and was added in Phase 2 as an optional, backward-compatible field.

---

## 10. Phased Implementation Roadmap

| Phase | Depends on | Deliverables | Acceptance criteria |
|---|---|---|---|
| **0. Plan & scaffold** | — | This plan, repo skeleton, `pyproject.toml`, `.gitignore`, `.env.example`, README, config/logging/test infrastructure | Package imports; `pytest` collects; `ruff`/`mypy` pass; no agents, API, or UI logic |
| **1. Contracts & core infrastructure** | 0 | Pydantic models, SQLite schema/repositories, errors, retry/timeout HTTP base, progress events, fixture loaders, contract tests | Schema tests pass; migrations run on a temp database; retry/timeout tested; no network required |
| **2. Minimal end-to-end multi-agent slice** | 1 | Agent classes with fake adapters, daily pipeline, `demo` CLI, fixture slate and preconditions, two-worker parallelism | Fixture run produces ranked JSON; repeated runs are identical; contract validation passes |
| **3. Real ESPN slate + Streamlit UI + cache** | 1, 2 | ESPN adapter, slate service, filters/sorting/selection, run control, progress display, cache, error display | Fetch today's D1-vs-D1 slate; select subset; rerun uses cache; UI shows progress/results; API failure degrades gracefully |
| **4. Historical research ingestion** | 1 | College Basketball adapter, MCP adapter, source whitelist, leakage filter, parallel batches, join/validate/quarantine, SQLite/Chroma writes, recursive chunking, embeddings | Fixture season ingested; invalid records quarantined; post-tip-off documents excluded; rerun is idempotent; Chroma retrieval returns citations |
| **5. RAG retrieval + reranking** | 4 | Embedding/query service, Chroma retrieval, `d3-mini` reranker, citation assembly | Top-k retrieval with metadata; rerank deterministic per snapshot; citations valid |
| **6. Extractor + statistical validation** | 4, 5 | Candidate generation, effect sizes/CIs/significance/FDR, out-of-sample checks, accepted/rejected decisions, `validated_preconditions` table | Synthetic positive/negative signals recovered; spurious signals rejected; schema valid; results deterministic |
| **7. Real Upset-Scorer** | 2, 6 | `d3-mini` decision adapter, score-to-probability mapping/calibration, ranking 1..n, cited explanations | Scores in `[0,1]`; stable ranking; rationale/citations present; baseline sanity check |
| **8. Backtesting/validation** | 6, 7 | Dataset builder, season splits, log loss/Brier/calibration plot, model report | Train 2021–22/2022–23/2023–24; validation 2024–25; untouched test 2025–26; reproducible report |
| **9. Hardening and deliverables** | all | Failure injection, rate/cost controls, docs, UML class/sequence PDFs, demo script, packaging checklist | Three consecutive E2E runs consistent; failure tests pass; lint/typecheck; zip/PDF/README complete |

**Phase 2 status: complete.** The fixture-backed daily slice now runs a fixture
slate through the Monitor-Orchestrator, two parallel Monitor workers, and the
Upset-Scorer, persists results to SQLite when repositories are supplied, and
emits stable ranked JSON from `python3 -m upset_profiler demo`. Tests cover
contract validation, parallel aggregation, per-game failure isolation, stable
ranking, persistence, and the CLI path.

**Phase 3 status: complete.** The live daily path now fetches ESPN's
Division I scoreboard, converts it into validated `DailySlate`/`DailyGame`
contracts (including odds), and derives rest/back-to-back features from ESPN
team schedules. The Streamlit app in `src/upset_profiler/ui/app.py` provides
filters, stable sorting, all-games-by-default selection, run controls, progress
and agent-activity display, ranked results with citations, graceful fetch/run
error messages, and a fixture mode that runs fully offline. `services/cache.py`
caches slate payloads and exact analysis requests (date, selected ids, worker
count, and precondition fingerprint) in SQLite with TTL expiry. The fixture
daily path and CLI remain unchanged and all quality gates pass.

**Phase 4 status: complete.** The historical path now exposes a
`CollegeBasketballProvider` interface with a fixture implementation and an ESPN
game-summary implementation. The Research-Orchestrator splits a season into
deterministic batches, runs Historical-Stats and Context-Research in parallel
per batch, joins records by `game_id`, and validates every handoff. Contextual
documents pass deterministic source-domain allowlisting and UTC tip-off leakage
filters before persistence; violations and invalid game records are written to
quarantine. Valid documents are chunked recursively, embedded with the
configured `qwen3-embedding:8b` adapter (or deterministic fixtures in tests),
and upserted into Chroma when the `rag` extra is installed or an in-memory
store otherwise. `python3 -m upset_profiler ingest --season 2024-2025` exercises
the fixture path end-to-end, and reruns upsert the same deterministic document
and chunk ids. RAG retrieval/reranking, statistical precondition extraction, the
real Upset-Scorer, and backtesting remain later phases.

**Phase 4.5 status: complete** (see
`docs/decisions/ADR-006-tavily-search-cache-and-conditional-research.md`). Three
approved changes to the research path: Tavily's free tier becomes the default
Context-Research search backend; search responses are cached in SQLite behind
normalized query keys (with Chroma reserved for semantic retrieval, not response
caching); and live research becomes conditional — compute structured
preconditions first, retrieve existing evidence second, search only for genuine
gaps. Stages A (Tavily provider) and B (SQLite search cache) are additive and do
not change agent flow; Stage C belongs to Phase 5 and Stage D to Phases 6–7.

Stage A adds `TavilyContextResearchProvider` (`integrations/tavily.py`), typed
`TAVILY_*` settings, and CLI choices `--context-provider tavily|none` alongside
the existing `fixture`/`mcp` adapters. One `/search` request sends
`include_raw_content`, `include_published_date`, restricted `include_domains`,
and — when the game's tip-off is known from the `games` table — `end_date` plus
`filter_by_published_date`. The deterministic `SourceWhitelist` and
`LeakageFilter` still run client-side and remain authoritative. Stage B adds
migration v2 (`search_queries`, `search_hits`), a pure normalization/key/TTL
service (`services/search_cache.py`), a typed repository, and cache-first reads
so a repeated query makes zero network calls. SQLite access is serialized across
worker threads with a re-entrant database lock; Chroma is untouched.

### 10.1 Parallel work tracks

After Phases 1–2:

- **UI/daily track:** Phase 3 → Phase 7 integration.
- **Historical track:** Phase 4 → 5 → 6.
- **Validation track:** Phase 8 after Phases 6 and 7.
- **Cross-cutting reliability and testing:** continuously through all phases.

### 10.2 Priority rule

Build the minimal working end-to-end multi-agent system with fixtures first (Phase 2). Do not begin expensive historical research, embedding, or statistical validation pipelines until the typed contracts, fake-adapter E2E flow, and test harness are working.

---

## 11. Testing Strategy

### 11.1 Unit tests

- Settings validation and path resolution.
- Pydantic schema constraints and serialization.
- Error hierarchy and retryability.
- Retry, timeout, and rate-limit handling.
- Timestamp leakage filtering.
- Source whitelist and URL canonicalization.
- Recursive chunking and deterministic chunk IDs.
- Cache key generation, expiration, and invalidation.
- Statistical functions: effect sizes, confidence intervals, significance, FDR.
- Ranking stability and deterministic tie-breaking.
- Progress event ordering and thread safety.

### 11.2 Contract tests

- Every agent output validates against its Pydantic model.
- Golden JSON fixtures are stable.
- Exported JSON schemas are checked into `docs/contracts/`.
- Breaking schema changes require an explicit version bump.

### 11.3 Integration tests

- Real SQLite with temporary databases.
- Temporary Chroma persistent client.
- Mocked HTTP with `respx`.
- Stub MCP server.
- Fake LLM, embedding, and decision-model adapters.
- Ingestion validation and quarantine.
- Extractor persistence.
- Parallel monitor aggregation.
- Cache behavior across repeated runs.

### 11.4 End-to-end tests

- Fixture slate through the full daily pipeline via the CLI.
- Repeated-run reproducibility test.
- Partial-failure injection: one game fails, others still complete.
- Streamlit `AppTest` smoke test.
- Fixture mode for offline demos.

### 11.5 Statistical and evaluation tests

- Synthetic datasets with known effect sizes.
- Spurious signals rejected under multiple-testing correction.
- Calibration, log loss, and Brier score correctness.
- Held-out season verification.
- Planted-error checks to validate the validators.

### 11.6 Leakage and security tests

- Documents published after tip-off are excluded.
- Documents with missing timestamps are rejected in strict mode.
- Timezone handling is correct.
- Non-whitelisted domains are blocked.
- Citation verifier rejects unsupported claims.

### 11.7 Quality gates

- `pytest`
- `ruff check .`
- `mypy src`
- Core coverage target of at least 80%; overall target of at least 70%.
- Marked test suites: `unit`, `integration`, `e2e`, `slow`, `evaluation`.
- Run commands documented in `README.md`.

---

## 12. Traceability Matrix

| Req | Implementation component | Verification |
|---|---|---|
| R1 | `ui/app.py`, `ui/components/*` | Streamlit `AppTest` |
| R2 | `ui/components/slate_table.py`, `services/slate.py` | UI filter/selection tests |
| R3 | `services/monitoring.py`, `ui/state.py` | E2E rerun test |
| R4 | `agents/research_orchestrator.py`, `historical_stats.py`, `context_research.py` | Integration fixture test |
| R5 | `integrations/tavily.py`, `integrations/mcp.py`, `college_basketball.py` | Stub Tavily/MCP/API tests (`tests/unit/test_tavily.py`, `test_mcp.py`); `pickcenter` odds precedence tests; stdio command/argv and `BRAVE_API_KEY` environment tests; MCP adapters remain selectable |
| R6 | `orchestration/runtime.py` | `tests/unit/test_runtime.py`; parallel monitor integration test |
| R7 | `services/ingestion.py`, `models/research.py` | Join and schema validation tests |
| R8 | `storage/sqlite.py`, `storage/chroma.py`, `rag/rerank.py`, `integrations/embeddings.py` | Storage/RAG integration tests |
| R9 | `agents/extractor.py`, `stats/validation.py` | Synthetic-signal tests |
| R10 | `integrations/espn.py`, `services/slate.py`, `models/games.py` | ESPN fixture tests |
| R11 | `services/slate.py`, `ui/app.py` | Slate UI E2E |
| R12 | `agents/monitor_worker.py`, `services/monitoring.py` | `tests/unit/test_monitor_worker.py`, `tests/integration/test_monitoring_pipeline.py` |
| R13 | `models/monitoring.py` (`GameFlag.rationale`) | Flag contract test and worker citation test |
| R14 | `agents/upset_scorer.py`, `integrations/decision_model.py` | `tests/unit/test_upset_scorer.py`; ranking integration test |
| R15 | `models/research.py`, `storage/sqlite.py`, `services/extraction.py` | Persistence tests |
| R16 | README, docstrings, code structure | Ruff/mypy/manual review |
| R17 | `agents/monitor_orchestrator.py`, `orchestration/runtime.py` | Two-worker integration test |
| R18 | `models/scoring.py`, `ui/components/results.py` | UI result contract test |
| R19 | Deterministic matching, caching, fixed model parameters, stable sorting | Repeated-run stability test and E2E demo rerun test |
| R20 | `config.py`, `.env.example`, `.gitignore` | Config/secrets tests |
| R21 | `models/*` | Contract tests |
| R22 | `errors.py`, `integrations/base.py`, partial-result handling | Failure-injection tests |
| R23 | `services/cache.py`, `services/search_cache.py`, SQLite `cache_entries`/`search_queries` tables | Cache hit/TTL tests; `tests/unit/test_search_cache_keys.py`, `tests/integration/test_search_cache.py`, CLI Tavily replay test |
| R24 | `security/leakage.py`, `integrations/tavily.py`, `SourceDocument.published_at` | Timestamp leakage tests; Tavily `end_date`/`filter_by_published_date` request test plus client-side filter test |
| R25 | `stats/backtest.py`, `stats/metrics.py` | Season-split/calibration tests |

---

## 13. Alignment with the 10 Principles for Reliable Multi-Agent Systems

| Principle | Planned implementation |
|---|---|
| 1. Deterministic validation decides | Pydantic validation, statistical tests, citation verifier; no agent vote decides correctness |
| 2. Independent drafts | Monitor workers evaluate isolated game batches and do not see each other's flags |
| 3. Anonymity | Aggregation by `game_id`; workers do not receive peer identities |
| 4. Real diversity | Historical-Stats and Context-Research use different tools and data; workers cover different games |
| 5. Falsification | Extractor tests null hypotheses and rejects unsupported preconditions |
| 6. Separate judge | Deterministic statistical arbiter; `d3-mini` separate from rationale generation; held-out test season |
| 7. Hard-to-game verifier | 2025–26 test season held out; planted-error tests |
| 8. Provenance | Citations, source timestamps, run IDs, model and precondition versions |
| 9. Preserve dissent | Store rejected preconditions, missing-data reasons, and disagreements; cap loops |
| 10. Measure honestly | Backtest against baselines with log loss, Brier score, and calibration; human selects games and reviews results |

---

## 14. Assumptions

1. An "upset" means the underdog wins outright, with the favorite determined from pregame betting odds.
2. Games without usable odds are excluded from upset-label training and scoring.
3. Training seasons are 2021–22, 2022–23, and 2023–24; validation is 2024–25; test is 2025–26.
4. The scope includes all D1-vs-D1 games with odds, including conference tournaments, but excludes exhibitions.
5. The historical sports API provider is abstracted behind a `CollegeBasketballProvider` interface because the exact provider is unspecified.
6. `d3-mini` and `qwen3-embedding:8b` are reachable through OpenAI-compatible HTTP endpoints or can be adapted to them.
7. MCP servers are external processes or services accessed through the MCP client; they are not implemented inside this repository.
8. Monitor signal matching is deterministic Python against validated thresholds; an LLM may draft cited explanations.
9. `d3-mini` raw scores are not treated as calibrated probabilities until a calibration layer has been validated.
10. A rerun of the same analysis reuses a stored evidence snapshot; a new run may fetch fresh live evidence.
11. Player statistics are stored because the requirements call for them, but player-level forecasting is out of scope.
12. "Today" uses the user's local basketball slate date, while all stored timestamps are UTC.
13. In strict leakage mode, documents with missing or unparseable publication timestamps are excluded.
14. Tests and demos use fixtures or recorded responses; they do not make paid or live API calls by default.
15. The existing `.env` is preserved and never committed.
16. ESPN summary payloads carry betting lines in a top-level `pickcenter` (or
    `odds`) list rather than on the header competition, so the summary parser
    attaches the first usable entry before handing the game to the shared
    scoreboard parser. A competition that already carries odds wins.
17. A configured stdio MCP server command is parsed as a command line
    (`executable` plus arguments) and receives the parent process environment,
    with `BRAVE_API_KEY` injected from settings; the SDK's default allowlist is
    too narrow to carry provider credentials.
18. Live ingestion requires explicit game ids because the ESPN historical
    provider cannot enumerate a season; `scripts/collect_espn_game_ids.py`
    sweeps the public scoreboard endpoint to produce them.
19. Tavily is the default Context-Research search backend, chosen for its 1,000
    free credits/month (no card) and because one `/search` call with
    `include_raw_content` replaces a search plus up to 25 fetches. The MCP
    Brave/Fetch/Deep Research adapters remain present, tested, and selectable so
    requirement R5 stays covered. See ADR-006.
20. Search responses are cached in SQLite behind a normalized query key that
    includes the game id, provider, and a parameters fingerprint; ChromaDB is
    reserved for semantic retrieval over chunked documents and is never used as
    the search-response cache. Historical (past tip-off) entries do not expire;
    live entries expire after `CACHE_TTL_SECONDS`.
21. Live research is conditional: structured preconditions are computed first,
    existing evidence is retrieved second (SQLite exact, Chroma semantic), and
    web search runs last, only for gaps. Spend therefore scales with gaps rather
    than games.
22. Tavily's `published_date` is a beta best-estimate ("published or last
    updated") and is treated as a hint; the deterministic client-side leakage
    filter remains authoritative, and strict mode still rejects undated
    documents.
23. The Context-Research protocol carries only `game_id` and `season`, so the
    Tavily provider resolves tip-off through an injected `game_lookup` backed by
    the `games` table. When the game is not yet stored (first ingestion run), the
    request omits `end_date` and `filter_by_published_date`, and the
    deterministic client-side leakage filter remains the only timestamp gate.
    Subsequent runs add the server-side bounds. Changing the provider protocol
    was rejected by ADR-006, and pre-fetching every game summary just to learn
    tip-off would double network traffic.
24. The shared SQLite connection is opened with `check_same_thread=False` and
    statement/transaction access is serialized by a re-entrant database lock,
    because ADR-006 cache reads and writes happen inside the parallel
    Context-Research worker while the Historical-Stats worker reads provider
    data. Persistence of joined records still runs on the orchestrator thread
    after each batch joins.

---

## 15. Unresolved Architectural Decisions and Critical Questions

These do not block scaffolding, but they block later phases.

1. **Historical sports API:** Which College Basketball API/provider and credentials will be used? Recommendation: implement a provider interface and start with free ESPN historical box scores unless a paid provider is already available.
2. **`d3-mini` and `qwen3-embedding:8b` access:** What are the base URLs, model IDs, authentication method, embedding dimension, rate limits, and response schema?
3. **Upset definition:** Confirm outright underdog win versus spread cover. Recommendation: outright winner based on pregame moneyline.
4. **MCP servers:** ~~Which Brave/Fetch/Deep Research MCP implementations and transports will be used? Is a Brave API key available?~~ **Superseded by ADR-006.** Tavily is the default search backend; the MCP adapters remain selectable. Still open: whether the showcase must exercise MCP *live* (ADR-006 open question 1), which is the only thing that would require a Brave key.
5. **Candidate preconditions:** Should candidates come from a curated seed list, LLM proposals, or both? Recommendation: curated seed list plus LLM proposals that must pass statistical validation.
6. **Season and data scope:** Confirm season splits, D1-vs-D1 filtering, and whether conference tournaments are included.
7. **Reproducibility policy:** Confirm whether rerunning the same analysis should reuse an evidence snapshot while a new run fetches fresh data.
8. **Monitor matching:** Confirm deterministic rule evaluation for flagging, with LLM only drafting cited explanations.

---

## 16. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Live web evidence is nondeterministic | Snapshot evidence per run; cache; deterministic matching; fixed model parameters |
| `d3-mini` scores are not calibrated probabilities | Add calibration layer and validate on held-out season |
| Historical API provider unavailable or costly | Provider abstraction; fixture/replay mode; cache raw responses |
| MCP servers unavailable | Timeouts, retries, partial results, fixture fallback |
| Post-tip-off data leaks into monitoring | Strict timestamp filtering; exclude unknown dates; leakage tests |
| LLM hallucinates rationale | Citation verifier; require citations; deterministic rule decisions |
| Context overflow in Extractor | Validate one candidate precondition at a time |
| Prompt injection from web sources | Domain whitelist, content sanitization, provenance, no direct tool execution from retrieved text |
| Multiple-testing false positives | Benjamini–Hochberg FDR correction and minimum sample sizes |
| Streamlit progress becomes inconsistent | Thread-safe queue and typed progress events |
| Expensive research reruns | Idempotent upserts, checkpoints, response cache, batch controls |
| Schema drift breaks handoffs | Strict Pydantic models, contract tests, schema versioning |
| Requirement R19 fails due to random sampling | Temperature 0 where supported, deterministic tie-breaking, stable sorting, caching |

---

## 17. Scaffolding Manifest

The initial scaffold (Phase 0) will create:

- `IMPLEMENTATION_PLAN.md`
- `README.md`
- `pyproject.toml`
- Expanded `.gitignore`
- `.env.example` without secrets
- `config/sources.yaml`, `config/preconditions.yaml`, `config/logging.yaml`
- `data/` and `docs/` directory structure with `.gitkeep`
- `src/upset_profiler/` package directories with `__init__.py`
- `config.py`, `errors.py`, `logging_config.py`, `context.py`
- `tests/conftest.py` plus initial config/logging/import tests
- `docs/architecture.md`, `docs/assumptions.md`, initial ADRs, `docs/uml/` placeholders

The initial scaffold will **not** include:

- Agent implementations.
- Orchestration logic.
- External API integrations.
- Streamlit functionality.
- Any changes to the existing `.env`.

---

## 18. Definition of Done

The project is complete when:

1. Historical research can be ingested once into SQLite and ChromaDB with validated records, citations, and leakage protection.
2. Validated preconditions are extracted with deterministic statistical evidence and stored separately.
3. The daily slate loads from ESPN and can be filtered, sorted, and selected in Streamlit.
4. Monitor workers run in parallel and produce cited flags.
5. Upset-Scorer ranks flagged games with calibrated risk scores.
6. Results and agent activity are visible in Streamlit.
7. Repeated runs produce consistent results.
8. Failures in APIs, LLMs, or MCP tools degrade gracefully.
9. Historical backtesting reports log loss, Brier score, and a calibration plot using the specified season splits.
10. UML class and sequence diagrams, README run instructions, and the source zip are ready for submission and showcase.
