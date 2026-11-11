# AGENTS.md — ADS 5984 Final Project

## Project

NCAA Division I Men's Basketball **Upset Condition Profiler** — a multi-agent
system with a Streamlit front end. See `IMPLEMENTATION_PLAN.md` for the
authoritative architecture, requirements inventory, data contracts, phases,
traceability matrix, and unresolved decisions. Keep this file concise; keep
detailed planning in `IMPLEMENTATION_PLAN.md`.

## Read first

1. `IMPLEMENTATION_PLAN.md`
2. `initial_prompt.md` (the approved task and its constraints)
3. `reference_documents/final_project_specifications.md`
4. `reference_documents/final_project_reqs_and_arch.qmd`
5. `reference_documents/10 Principles for Reliable Multi-Agent Systems.pdf`
6. `docs/decisions/ADR-006-tavily-search-cache-and-conditional-research.md`

Do not edit anything under `reference_documents/`. Preserve the architecture in
the reference documents unless there is a compelling technical reason to change
it, and document any such change in `IMPLEMENTATION_PLAN.md` Sections 12–13.

## Current status

**Phase 4.5 complete.** The repository contains the Phase 1 contracts and core
infrastructure, the Phase 2 fixture-backed multi-agent slice, the Phase 3 daily
path, the Phase 4 historical ingestion path, and ADR-006 stages A and B. Phase 4
adds fixture/ESPN historical providers, optional MCP context research, source
whitelisting, tip-off leakage filtering, recursive chunking, embeddings,
SQLite/Chroma persistence, quarantine handling, the Research-Orchestrator with
parallel Historical-Stats and Context-Research workers, and the `ingest` CLI.
Phase 4.5 adds the Tavily Context-Research provider (default search backend),
typed `TAVILY_*` settings, `--context-provider tavily|none`, migration v2
(`search_queries`/`search_hits`), normalized query keys/TTLs, cache-first reads,
and thread-safe SQLite access for the parallel worker. All quality gates pass
offline; live adapters use captured fixtures and optional extras.

Do not implement RAG retrieval/reranking, statistical precondition extraction,
the real Upset-Scorer, or backtesting until the user explicitly approves Phase
5. ADR-006 stages C (Chroma retrieval) and D (`ContextGap`, gap-driven
orchestration) remain gated; Phase 5 is stage C.

## Hard constraints

- No unnecessary agent frameworks. Use plain Python orchestration plus Pydantic
  contracts.
- Build the minimal fixture-backed end-to-end multi-agent slice (Phase 2) before
  expensive historical research, embedding, or statistical validation work.
- Prefer deterministic Python for anything that does not need LLM reasoning.
- Tavily is the default Context-Research search backend (ADR-006). Keep the MCP
  Brave/Fetch/Deep Research adapters present, tested, and selectable so
  requirement R5 stays covered; do not remove them.
- Cache search responses in SQLite behind normalized query keys. ChromaDB is for
  semantic retrieval only — never use it as the search-response cache.
- Live research is conditional (ADR-006 Decision 3): compute structured
  preconditions first, retrieve existing evidence second, search only for gaps.
  Do not reintroduce unconditional per-game web search.
- Do not invent requirements absent from the reference documents. Record
  assumptions and open questions instead.
- Every agent handoff is a Pydantic model; no ad-hoc dicts between stages.
- Never commit, print, log, or expose secrets. Never modify the existing `.env`;
  create `.env.example` with placeholders only.

## Environment

- Python `>=3.11,<3.14` (current interpreter is 3.13.5).
- `src/` layout with `pyproject.toml`.
- Expected `.env` key names (values are secret; do not display them):
  `OPEN_AI_ENDPOINT`, `OPEN_AI_API_KEY`, `OPEN_AI_MODEL`.
  Planned additions are listed in `IMPLEMENTATION_PLAN.md` Section 8.2.
- `vt-arc-llm` is a model name, not a Python package; use an OpenAI-compatible
  client. Embeddings use `qwen3-embedding:8b`; scoring uses `d3-mini`.
- VT ARC endpoints require eduroam or Cisco VPN.

## Planned commands

Use these quality gates (documented in the plan, Section 11.7). Verify they
exist in `pyproject.toml` before running:

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
ruff check .
mypy src
python3 scripts/export_contracts.py --check
```

CLI entry point: `python3 -m upset_profiler <command>` with admin commands
`ingest`, `extract`, `monitor`, `backtest`, `demo`, `validate-config`. The
Phase 2 offline demo runs with
`python3 -m upset_profiler demo --date 2025-01-15`; the Phase 4 offline
historical ingestion runs with
`python3 -m upset_profiler ingest --season 2024-2025`.

## Repo conventions

- `models/*` are Pydantic contracts only — no I/O.
- `integrations/*` own all network access; use the shared HTTP client factory
  with timeouts, retries, and rate limiting.
- `storage/*` owns SQLite and Chroma access; no SQL elsewhere.
- `services/*` hold use cases; `agents/*` hold agent roles; `ui/*` is Streamlit.
- Use typed settings, structured logging, and redact secrets.
- Tests: `unit`, `integration`, `e2e`, `evaluation`; use fixtures and fake
  adapters so the suite runs offline.

## Deliverables to keep in view

Functional end-to-end multi-agent system, Streamlit UI showing agent activity,
UML class + sequence diagrams (PDF), source zip, and a README with run
instructions and expected `.env` keys (never `.env` itself). Showcase:
Thursday, December 3, 5–7 PM.

## Session state — read PROGRESS.md first

`PROGRESS.md` at the repo root is the single source of truth for in-progress
task state. If it exists, read it before starting any work and resume from
its "Resume here" section. Keep it updated at important junctions (see the
progress-management skill).
