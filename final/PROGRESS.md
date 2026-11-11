# PROGRESS — Phase 4: Historical Research Ingestion

> **Status:** complete
> **Last updated:** 2026-11-10 — Phase 4 complete; all quality gates pass
> **Current step:** 7. Full quality gates and documentation/status updates

## Resume here
- Phase 4 is complete (plus the 2026-11-10 provider fixes below). The next
  approved work is **ADR-006 stages A and B** (Tavily provider, SQLite search
  cache), recorded in
  `docs/decisions/ADR-006-tavily-search-cache-and-conditional-research.md`.
  Stage A/B are additive; they do not change agent flow.
- Do not start Phase 5 (RAG retrieval/reranking) or Phase 6 (extractor) until
  the user explicitly approves it per `IMPLEMENTATION_PLAN.md` Section 10.
  ADR-006 Stage C belongs to Phase 5 and Stage D to Phases 6–7.
- Do **not** make live research unconditional again. ADR-006 Decision 3 requires
  the order: structured preconditions → existing evidence → search for gaps.
  Until Stage D lands, `ingest` may still research per game, but any new
  orchestration must follow the gap-driven order.
- Tavily replaces Brave as the default search backend (ADR-006 Decision 1). The
  MCP Brave/Fetch/Deep Research adapters must stay present and selectable so
  requirement R5 stays covered.
- Chroma is for semantic retrieval only; never use it as the search-response
  cache (ADR-006 Decision 2). Search responses live in SQLite.
- The offline historical path runs with
  `python3 -m upset_profiler ingest --season 2024-2025`; add
  `--vector-store chroma --embedding-provider http` for the live stores.
- The live full-season path needs explicit game ids because ESPN cannot
  enumerate a season. Enumerate first, then ingest:
  ```bash
  python3 scripts/collect_espn_game_ids.py --season 2021-2022 \
    --output data/processed/espn_game_ids_2021-2022.txt
  python3 -m upset_profiler ingest --season 2021-2022 --provider espn \
    --context-provider fixture \
    $(sed 's/^/--game-id /' data/processed/espn_game_ids_2021-2022.txt)
  ```
  The 2021-2022 season is 5,976 completed games and takes roughly eight minutes.
- Live documents require MCP configuration: `BRAVE_API_KEY` plus
  `MCP_BRAVE_COMMAND` and `MCP_FETCH_COMMAND` (see `.env.example`). Set
  `--context-provider fixture` to ingest records without documents.
- Re-run `python3 -m pytest && ruff check . && mypy src &&
  python3 scripts/export_contracts.py --check` before and after future work.
- `tests/integration/test_chroma_store.py` is skipped until the optional `rag`
  extra (`pip install -e ".[rag]"`) is installed.

## Plan summary
Implement Phase 4 of `IMPLEMENTATION_PLAN.md`: a College Basketball historical
stats adapter (ESPN historical summary plus fixtures), an MCP contextual
research adapter (fixture-backed plus optional live SDK), deterministic source
whitelisting and timestamp leakage filtering, recursive document chunking,
embeddings, SQLite/vector-store persistence, quarantine of invalid records, and
the Research-Orchestrator with parallel Historical-Stats and Context-Research
workers. Add the `ingest` CLI command and fixtures so a historical season can be
ingested offline, rerun idempotently, and queried for cited evidence. Do not
implement RAG reranking, the Extractor, the real Upset-Scorer, or backtesting.
Definition of done: fixture season ingested; invalid records quarantined;
post-tip-off documents excluded; rerun idempotent; vector retrieval returns
citations; all quality gates pass.

## Steps
| # | Step | Status | Verification |
|---|------|--------|--------------|
| 1 | Verify Phase 3 baseline and initialize Phase 4 progress state | `[x]` | `python3 -m pytest -q && ruff check . && mypy src && python3 scripts/export_contracts.py --check` |
| 2 | Add Phase 4 contracts, fixtures, source whitelist, and leakage filter | `[x]` | `python3 -m pytest tests/unit/test_source_whitelist.py tests/unit/test_leakage.py` |
| 3 | Add recursive chunking, embeddings, and vector stores | `[x]` | `python3 -m pytest tests/unit/test_chunking.py tests/unit/test_embeddings.py tests/unit/test_vector_store.py` |
| 4 | Add College Basketball and MCP research adapters | `[x]` | `python3 -m pytest tests/unit/test_college_basketball.py tests/unit/test_mcp.py` |
| 5 | Add research agents, orchestrator, ingestion service, and persistence | `[x]` | `python3 -m pytest tests/integration/test_ingestion_pipeline.py` |
| 6 | Add `ingest` CLI and E2E fixture coverage | `[x]` | `python3 -m pytest tests/e2e/test_ingest_cli.py` |
| 7 | Run full quality gates and update docs/status | `[x]` | `python3 -m pytest && ruff check . && mypy src && python3 scripts/export_contracts.py --check` |
| 8 | Fix live ESPN odds mapping and MCP stdio transport (2026-11-10) | `[x]` | `python3 -m pytest tests/unit/test_college_basketball.py tests/unit/test_mcp.py` |

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## Progress to date (append-only journal)
- 2026-11-10 — Step 1: re-verified Phase 3 baseline (154 tests pass; ruff,
  mypy, contract `--check` pass) and initialized Phase 4 state.
- 2026-11-10 — Step 2: added `IngestionResult` and run counts to
  `models/research.py`, historical game and research-document fixtures,
  `security/source_whitelist.py`, and `security/leakage.py`. Verification:
  12 whitelist/leakage tests pass; ruff and mypy pass.
- 2026-11-10 — Step 3: added `rag/chunking.py`, `integrations/embeddings.py`,
  and `storage/chroma.py` (in-memory and lazy-imported Chroma adapters).
  Verification: 12 chunking/embedding/vector-store tests pass; ruff and mypy
  pass.
- 2026-11-10 — Step 4: added `integrations/college_basketball.py` (fixture and
  ESPN summary providers), `integrations/mcp.py` (MCP search/fetch/deep
  research plus optional lazy SDK client), fixture historical games and ESPN
  summary, and a fixture context-research provider. Verification: 10 adapter
  tests pass; ruff and mypy pass.
- 2026-11-10 — Step 5: added Historical-Stats and Context-Research workers,
  `agents/research_orchestrator.py` (parallel batches, join, whitelist,
  leakage, quarantine, persistence, chunking/embedding), `services/ingestion.py`,
  and `orchestration/pipelines/ingestion.py`. Verification: 3 ingestion
  integration tests pass; ruff and mypy pass.
- 2026-11-10 — Step 6: added the `ingest` CLI command with fixture/ESPN
  historical, fixture/MCP context, fixture/HTTP embeddings, in-memory/Chroma
  vector stores, database/output/progress options, and E2E tests covering
  offline ingestion, quarantine, limits, persistence, and idempotent reruns.
  Verification: 2 CLI E2E tests pass; ruff and mypy pass.
- 2026-11-10 — Step 7: regenerated `docs/contracts`, added an optional Chroma
  integration test (skipped without `chromadb`), updated README, AGENTS,
  IMPLEMENTATION_PLAN, architecture, assumptions, and ADR-005. Verification:
  194 passed, 1 skipped; ruff, mypy, and contract `--check` pass. Manual
  `ingest` run produced 3 records, 4 documents, 4 chunks, and 4 quarantine
  issues with all expected codes.
- 2026-11-10 — Step 8: ingested the live 2021-2022 season (5,976 completed ESPN
  games, enumerated by the new `scripts/collect_espn_game_ids.py` because the
  provider cannot list a season) and found two provider defects: live summaries
  produced `odds=None` for all 5,976 games, and the MCP stdio transport could
  neither accept an argument-bearing command nor pass `BRAVE_API_KEY` to the
  server. Fixed both in `integrations/college_basketball.py`,
  `integrations/espn.py`, and `integrations/mcp.py`. Verification: 6 new tests;
  182 passed with the `respx`-dependent modules skipped; `ruff check .` passes;
  a live stdio call to `uvx mcp-server-fetch` returned page text; a live ESPN
  game now reports `provider=consensus`, `spread=-1.5`, `total=140.5`, and both
  moneylines.
- 2026-11-10 — Re-ran the full 2021-2022 live ingest after the fix: 5,976 games,
  0 quarantined, 5,473 with odds (91.6%), 5,470 with both moneylines, and 1,514
  underdog wins (27.7% of games with odds). The 503 games without odds are
  concentrated in November (19.8%) and December (17.8%) 2021 and fall below 2%
  from January onward, consistent with ESPN lacking archived early-season lines.
  Assumption 2 therefore excludes those 503 games from upset labelling.

## Failures & fixes
| Step | Symptom | Root cause | Fix / alternative | Status |
|------|---------|------------|-------------------|--------|
| 8 | Every live ESPN game had `odds=None`, so no game had a favorite/underdog | `parse_espn_summary` rebuilt a scoreboard-shaped event from `header.competitions[0]`, but summaries expose lines in top-level `pickcenter`/`odds`; `_odds_from_competition` only reads `competition["odds"]` | `_competition_with_odds` attaches the first usable `pickcenter`/`odds` entry when the competition has none; `_moneyline_close` falls back to `<side>TeamOdds.moneyLine` | Fixed |
| 8 | `MCP_BRAVE_COMMAND=npx -y @modelcontextprotocol/server-brave-search` raised `FileNotFoundError` | `StdioServerParameters(args=[])` treated the whole string as one executable | `split_stdio_command` uses `shlex.split` to separate executable and arguments | Fixed |
| 8 | `BRAVE_API_KEY` never reached the stdio MCP server | `StdioServerParameters(env=None)` makes the SDK forward only `HOME`/`LOGNAME`/`PATH`/`SHELL`/`TERM`/`USER` | `stdio_environment` passes the parent environment and injects `BRAVE_API_KEY` from settings | Fixed |

## Decisions & alternatives
- 2026-11-10 — Phase 4 uses the ESPN historical `summary` endpoint as the first
  live College Basketball provider, with a fixture provider as the offline
  default. Alternatives: wait for a paid provider selection (plan Section 15).
  Chosen because ESPN was the recommended free fallback and enables an
  end-to-end live path without new credentials.
- 2026-11-10 — Offline tests use a deterministic in-memory vector store; the
  real Chroma adapter lazy-imports the optional `chromadb` extra. Alternatives:
  hard-require Chroma for all tests. Chosen so default quality gates stay
  offline and installable with `.[dev]` only.
- 2026-11-10 — Removed the pre-existing `docs/contracts/*.json` ignore rule so
  the generated JSON Schemas can actually be checked in, matching
  `IMPLEMENTATION_PLAN.md` Section 9.2 and the contract test. Alternatives:
  leave schemas untracked and rely on local generation. Chosen because the plan
  and AGENTS both describe checked-in schemas.
- 2026-11-10 — Live ESPN odds come from `pickcenter`/`odds` at the summary root,
  not from the header competition. The header competition stays authoritative
  when it already carries odds, so scoreboard-shaped fixtures and payloads are
  unaffected. Chosen over rewriting `_odds_from_competition` so the scoreboard
  and summary paths share one parser.
- 2026-11-10 — The stdio MCP transport passes the parent process environment to
  the server and injects `BRAVE_API_KEY` from settings, instead of relying on
  the SDK's six-variable allowlist. Chosen because the allowlist cannot carry
  provider credentials and every alternative (wrapper scripts, holding the key
  in the repo) is worse for reproducibility. The key is never logged or stored
  outside `.env`.
