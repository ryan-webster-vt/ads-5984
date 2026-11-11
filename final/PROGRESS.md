# PROGRESS — Phase 3: ESPN Slate + Streamlit UI + Cache

> **Status:** complete
> **Last updated:** 2026-11-10 — Phase 3 complete; all quality gates pass
> **Current step:** 6. Full quality gates and documentation/status updates (done)

## Resume here
- Phase 3 is complete. Do not start Phase 4 until the user explicitly approves
  it per `IMPLEMENTATION_PLAN.md` Section 10.
- The offline UI path runs with
  `python3 -m streamlit run src/upset_profiler/ui/app.py` (fixture mode default).
- Re-run `python3 -m pytest && ruff check . && mypy src &&
  python3 scripts/export_contracts.py --check` before and after future work.
- The fixture CLI path remains `python3 -m upset_profiler demo --date 2025-01-15`.

## Plan summary
Implement Phase 3 from `IMPLEMENTATION_PLAN.md`: a real ESPN daily slate adapter,
a SQLite-backed cache for slate/analysis reruns, and a Streamlit front end with
filters, sorting, game selection, run control, progress/agent activity, ranked
results, citations, and graceful API-failure display. Keep fixture adapters as
the default offline path; live ESPN supplies the real slate, while schedule
history supplies deterministic rest/back-to-back features. Do not implement
historical research, MCP, RAG, statistical extraction, the real Upset-Scorer, or
backtesting in this phase. Definition of done: live ESPN parsing works against
captured payloads, reruns reuse cache entries, Streamlit renders and runs the
fixture path under `AppTest`, and all quality gates pass.

## Steps
| # | Step | Status | Verification |
|---|------|--------|--------------|
| 1 | Verify Phase 2 baseline and initialize Phase 3 progress state | `[x]` | `python3 -m pytest -q && ruff check . && mypy src && python3 scripts/export_contracts.py --check` |
| 2 | Add ESPN scoreboard/schedule adapters and captured fixtures | `[x]` | `python3 -m pytest tests/unit/test_espn_adapter.py` |
| 3 | Add typed cache service and cached slate/analysis reruns | `[x]` | `python3 -m pytest tests/unit/test_cache_service.py tests/integration/test_caching.py` |
| 4 | Build Streamlit UI (state, filters/sort/selection, progress, results) | `[x]` | `tests/e2e/test_streamlit_app.py` AppTest smoke |
| 5 | Add integration/e2e/AppTest coverage for Phase 3 paths | `[x]` | `python3 -m pytest tests/e2e/test_streamlit_app.py` |
| 6 | Run quality gates and update docs/status | `[x]` | `python3 -m pytest && ruff check . && mypy src && python3 scripts/export_contracts.py --check` |

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## Progress to date (append-only journal)
- 2026-11-10 — Phase 2 baseline re-verified: 135 tests pass; ruff, mypy, and
  contract `--check` pass. Initialized Phase 3 state.
- 2026-11-10 — Step 2: added `integrations/espn.py` (scoreboard parser, slate
  provider, schedule-derived context provider), captured
  `tests/fixtures/espn/scoreboard_2026-11-10.json`, and added adapter tests.
  Verification: 5 adapter tests pass; ruff and mypy pass.
- 2026-11-10 — Step 3: added `services/cache.py` (typed SQLite cache,
  `CachedSlateProvider`, `CachingMonitoringService`) plus unit/integration
  tests. Verification: 11 combined cache/ESPN tests pass; ruff and mypy pass.
- 2026-11-10 — Step 4: added `ui/app.py`, `ui/state.py`, and `ui/components/*`
  for fixture/live source selection, slate filters/sorting, all-games selection,
  run controls, progress/agent activity, ranked results/citations, cache badge,
  empty state, and error display. Verification: `AppTest` renders the fixture
  slate and runs analysis without exceptions.
- 2026-11-10 — Step 5: added `tests/unit/test_ui_slate_table.py` and
  `tests/e2e/test_streamlit_app.py` covering filters, stable sorting, cache
  reruns, and the empty-filter state. Verification: 8 UI tests pass.
- 2026-11-10 — Step 6: full suite 154 passed; ruff, mypy, and contract
  `--check` pass. Updated README, AGENTS, IMPLEMENTATION_PLAN, and architecture
  docs to Phase 3 status.

## Failures & fixes
| Step | Symptom | Root cause | Fix / alternative | Status |
|------|---------|------------|-------------------|--------|
| 2 | Ruff RUF001 on venue assertion | Captured ESPN venue uses a Unicode right single quote | Asserted on a stable substring instead of the exact typographic string | resolved |
| 2 | mypy rejected favorite id assignment and an unused ignore | Tuple assignment was inferred as `str`; status helper returns plain `str` | Annotated favorite/underdog ids as `str \| None` and removed the ignore | resolved |

## Decisions & alternatives
- 2026-11-10 — Live slate comes from ESPN scoreboard `groups=50`; all parsed
  teams are marked D1, and `SlateService` still applies the D1-vs-D1 policy.
  Alternatives: infer division from unavailable ESPN fields. Chosen because the
  group filter is ESPN's D1 selector and keeps the contract explicit.
- 2026-11-10 — Live context in Phase 3 uses team schedules to derive
  `rest_days_difference` and `underdog_back_to_back`; travel-distance features
  wait for richer historical/MCP context. Alternatives: leave live analysis
  empty or invent travel data. Chosen to make live monitoring useful without
  fabricating features.
- 2026-11-10 — Cache keys include slate date, sorted selected game ids, worker
  count, and a precondition fingerprint. Alternatives: date-only keys (stale
  across selection changes). Chosen so an identical request reuses work while
  different requests do not collide.
