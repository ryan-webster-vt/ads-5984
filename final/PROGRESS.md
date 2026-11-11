# PROGRESS — Phase 4.5: Tavily provider and SQLite search cache

> **Status:** complete
> **Last updated:** 2026-11-10 — ADR-006 stages A and B implemented; all gates pass
> **Current step:** done (Phase 5 / ADR-006 stage C remains gated)

## Resume here
- **Phase 4.5 is complete.** No further work is approved. Do not start ADR-006
  stage C (Chroma semantic retrieval) or stage D (`ContextGap`, gap-driven
  orchestration), Phase 5+, the Extractor, the real Upset-Scorer, or backtesting
  without explicit user approval.
- If a regression appears, re-run the gates and start from the failing test:
  `python3 -m pytest && ruff check . && mypy src &&
  python3 scripts/export_contracts.py --check`.
- Live Tavily calls need `TAVILY_API_KEY` (or `TAVILY_KEYLESS=true`) and VT ARC /
  eduroam or Cisco VPN is only required for LLM/embedding endpoints, not Tavily.

## Plan summary
Implemented Phase 4.5 of `IMPLEMENTATION_PLAN.md`, which is ADR-006 stages A and
B. Tavily is now the default Context-Research search backend behind the existing
provider protocol: one `/search` call with `include_raw_content`, server-side
`end_date` + `filter_by_published_date` leakage bounds when tip-off is known,
`include_domains` whitelisting in restrict mode, and the existing deterministic
client-side whitelist and leakage filter as the authoritative checks. A new
SQLite migration adds `search_queries` and `search_hits`, with a pure
normalization/key function, parameter fingerprints, a TTL policy (historical =
no expiry, live = `CACHE_TTL_SECONDS`), and cache-first reads so repeated or
gap-driven searches never re-spend credits. Chroma remains semantic retrieval
only. The MCP Brave/Fetch/Deep Research adapters stay present, tested, and
selectable.

Definition of done (met): Tavily is selectable via `--context-provider tavily`;
a cached query makes zero network calls on the second run; historical cache rows
never expire while live rows do; whitelist and leakage checks still run on
cached and fresh documents; invalid records still quarantine; all quality gates
pass offline.

## Steps
| # | Step | Status | Verification |
|---|------|--------|--------------|
| 1 | Verify Phase 4 baseline and initialize Phase 4.5 state | `[x]` | `python3 -m pytest -q` (200 passed baseline) |
| 2 | Add Tavily settings, `.env.example` keys, and config tests | `[x]` | `python3 -m pytest tests/unit/test_config.py` (15 passed) |
| 3 | Add search-cache contracts, migration v2, repository, and key/TTL service | `[x]` | `tests/unit/test_search_cache_keys.py`, `tests/integration/test_search_cache.py` |
| 4 | Add `TavilyContextResearchProvider` with cache-first reads and parsing | `[x]` | `tests/unit/test_tavily.py` (28 passed) |
| 5 | Wire `--context-provider tavily`/`none` and the SQLite cache into `ingest` | `[x]` | `tests/e2e/test_ingest_cli.py` |
| 6 | Update docs, ADR status, plan, README, AGENTS, and regenerate contracts | `[x]` | full gates (see below) |

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## Progress to date (append-only journal)
- 2026-11-10 — Step 1: read all reference documents (specifications,
  requirements/architecture, 10 Principles PDF via `pdftotext`), `PROGRESS.md`,
  ADR-006, and `IMPLEMENTATION_PLAN.md`. Installed the dev extra (`respx`,
  `mypy`, `freezegun`, `pytest-cov`) because the active interpreter lacked it.
  Regenerated `docs/contracts` under pydantic 2.12.5; baseline was 200 passed.
- 2026-11-10 — Prior work: Phase 4 is complete (commit `49d5c55 phase 4
  complete`): fixture/ESPN historical providers, MCP context research, source
  whitelist, leakage filter, chunking, embeddings, SQLite/Chroma persistence,
  quarantine, Research-Orchestrator, and the `ingest` CLI. Its detailed journal
  lives in git history.
- 2026-11-10 — Step 2: added `TavilySettings` (`TAVILY_API_KEY`,
  `TAVILY_BASE_URL`, `TAVILY_SEARCH_DEPTH`, `TAVILY_TOPIC`,
  `TAVILY_MAX_RESULTS`, `TAVILY_INCLUDE_RAW_CONTENT`, `TAVILY_KEYLESS`,
  `TAVILY_TIMEOUT_SECONDS`), wired it into `Settings`, updated `.env.example`
  and the test environment scrub list, and added config tests.
- 2026-11-10 — Step 3: added `SearchHitRecord`/`CachedSearchResponse`, migration
  v2, `SearchCacheRepository`, and `services/search_cache.py`
  (`normalize_search_query`, `search_params_fingerprint`, `search_query_hash`,
  `resolve_search_expiry`, `SearchCache`). Added unit key/TTL tests and SQLite
  round-trip/expiry/purge integration tests.
- 2026-11-10 — Step 4: added `integrations/tavily.py` (config validation,
  `published_date` parsing across ISO/RFC/Unix formats, response parsing that
  prefers `raw_content`, canonical-URL dedupe, cache-first reads, whitelist and
  document-cap enforcement) plus 28 unit tests. Verified long-line/typing
  cleanup (`ruff`, `mypy`).
- 2026-11-10 — Step 5: added `NullContextResearchProvider`, rewired
  `_run_ingest` to bootstrap repositories before building the provider, build a
  `SearchCache` from `CACHE_TTL_SECONDS`, pass a `games`-table `game_lookup`, and
  close context/historical providers in `finally`. CLI choices are now
  `auto|fixture|tavily|mcp|none`; `auto` resolves to `fixture` for fixture stats
  and `tavily` for live stats. Added e2e tests for `none` and for a respx-mocked
  Tavily run that proves the SQLite cache prevents repeat HTTP calls.
- 2026-11-10 — Step 5 fix: context research runs in a worker thread, so its cache
  and game lookups hit a `check_same_thread=True` connection and failed with
  `StorageError`. Opened the shared connection with `check_same_thread=False` and
  serialized statement/transaction access with a re-entrant `database_lock()`.
- 2026-11-10 — Step 6: updated `IMPLEMENTATION_PLAN.md`, `README.md`,
  `docs/architecture.md`, `docs/assumptions.md`, ADR-006, and `AGENTS.md`;
  regenerated all 47 contract schemas (including `SearchHitRecord` and
  `CachedSearchResponse`). Final gates: **247 passed**, `ruff check .` clean,
  `mypy src` clean, `export_contracts.py --check` current.
- 2026-11-10 — Post-completion fix (credit preservation): a cold run searched
  before the `games` row existed, so the Tavily request omitted `end_date`, the
  response was cached under a different `params_hash` with a TTL instead of a
  permanent row, and re-running a season spent a second credit per game. The
  orchestrator now resolves and persists each batch's games before dispatch
  (`ResearchOrchestrator._prefetch_games`) and passes them to Historical-Stats
  through its new `prefetched=` argument, which is authoritative: the provider is
  called exactly once per game and the parallel worker fan-out is unchanged.
  Games that cannot be resolved are excluded from the batch's Context-Research
  scope, so no credit is spent on a record that would be quarantined as
  `missing_historical_game`. Added
  `test_context_research_sees_tipoff_before_searching_on_a_cold_run`, which fails
  without the prefetch, and updated the e2e Tavily cache test to expect three
  calls for four requested games. Also made the suite hermetic: the CLI's
  `--env-file` default (the project `.env`) is redirected to a nonexistent path in
  the autouse fixture, so a developer's real `TAVILY_API_KEY`/`TAVILY_KEYLESS`
  can no longer flip `test_ingest_cli_tavily_requires_an_api_key`. Gates: **248
  passed**, `ruff` clean, `mypy src` clean, contracts current.

## Failures & fixes
| Step | Symptom | Root cause | Fix / alternative | Status |
|------|---------|------------|-------------------|--------|
| 1 | `respx`/`mypy` import errors during collection | The active conda interpreter did not have the `dev` extra installed | `python3 -m pip install -e ".[dev]"` | resolved |
| 1 | `test_checked_in_schemas_are_current` failed for 21 schemas | Checked-in schemas were generated by an older pydantic; 2.12.5 emits `additionalProperties: true` for free-form objects | Regenerated with `python3 scripts/export_contracts.py` | resolved |
| 5 | Context research raised `StorageError` (`SELECT`) for every game | `SearchCache`/game lookups ran in the parallel worker thread against a thread-bound SQLite connection | `check_same_thread=False` plus a re-entrant lock in `_Repository`/`transaction` | resolved |
| 3/6 | `test_migrations_are_idempotent` expected only version 1 | Migration v2 was added earlier in Phase 4.5 | Updated the expectation to `(1, 2)` | resolved |

## Decisions & alternatives
- 2026-11-10 — Implement only ADR-006 stages A and B. Alternatives: also do
  Stage C/D now. Chosen because the plan and AGENTS gate Stages C/D to Phases
  5–7 and the user asked for Phase 4.5.
- 2026-11-10 — Search cache rows are exposed as strict Pydantic contracts
  (`CachedSearchResponse`, `SearchHitRecord`) instead of raw dicts. Alternatives:
  return dicts from the repository. Chosen because every boundary in this repo
  is typed and the contracts are schema-exported.
- 2026-11-10 — Tavily resolves tip-off through an optional injected
  `game_lookup` (backed by the `games` table) because the Context-Research
  protocol receives only `game_id` and `season`. When tip-off is unknown (first
  ingestion run, before the game is stored), the request omits `end_date` and
  relies on the deterministic client-side leakage filter; when it is known, the
  provider sends `end_date` + `filter_by_published_date=true`. Alternatives:
  change the provider protocol (rejected by ADR-006) or double ESPN traffic to
  pre-fetch summaries (too expensive).
- 2026-11-10 — The cache stores every parsed hit, including non-whitelisted
  domains, so a rejection is auditable from the cache; the whitelist is
  re-applied when documents are built and again in the orchestrator.
- 2026-11-10 — `--context-provider auto` resolves to `fixture` with fixture
  stats and `tavily` with live stats. `none` remains available for
  statistics-only runs. Default `ingest` behavior is otherwise unchanged because
  Stage D (gap-driven defaults) is still gated.

## Verification commands
```bash
python3 -m pytest
ruff check .
mypy src
python3 scripts/export_contracts.py --check
```
