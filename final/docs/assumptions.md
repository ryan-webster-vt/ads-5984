# Assumptions

Working assumptions carried from `IMPLEMENTATION_PLAN.md` Section 14. Each is
subject to confirmation in `IMPLEMENTATION_PLAN.md` Section 15 before the
dependent phase begins.

1. An **upset** means the underdog wins outright, with the favorite determined
   from pregame betting odds.
2. Games without usable odds are excluded from upset-label training and
   scoring.
3. Training seasons are 2021–22, 2022–23, and 2023–24; validation is 2024–25;
   test is 2025–26.
4. Scope includes all D1-vs-D1 games with odds, including conference
   tournaments, but excludes exhibitions.
5. The historical sports API provider is abstracted behind a
   `CollegeBasketballProvider` interface because the exact provider is
   unspecified.
6. `d3-mini` and `qwen3-embedding:8b` are reachable through OpenAI-compatible
   HTTP endpoints or can be adapted to them.
7. MCP servers are external processes or services accessed through the MCP
   client; they are not implemented inside this repository.
8. Monitor signal matching is deterministic Python against validated
   thresholds; an LLM may draft cited explanations.
9. `d3-mini` raw scores are not treated as calibrated probabilities until a
   calibration layer has been validated.
10. A rerun of the same analysis reuses a stored evidence snapshot; a new run
    may fetch fresh live evidence.
11. Player statistics are stored because the requirements call for them, but
    player-level forecasting is out of scope.
12. "Today" uses the user's local basketball slate date, while all stored
    timestamps are UTC.
13. In strict leakage mode, documents with missing or unparseable publication
    timestamps are excluded.
14. Tests and demos use fixtures or recorded responses; they do not make paid or
    live API calls by default.
15. The existing `.env` is preserved and never committed.
16. The first live historical provider uses ESPN's public game-summary endpoint
    behind the `CollegeBasketballProvider` interface; the fixture provider is
    the offline default.
17. Offline historical ingestion uses a deterministic in-memory vector store.
    The real Chroma store is used when the optional `rag` extra is installed or
    when `--vector-store chroma` is requested.
18. Live MCP context research uses the official MCP SDK through configured
    stdio/HTTP/SSE transports. Fixture documents are used for offline runs.
19. Chunk sizing counts whitespace-delimited tokens, an intentionally simple
    deterministic approximation of model tokens.
20. Tavily is the default Context-Research search backend (ADR-006). The MCP
    Brave/Fetch/Deep Research adapters remain present, tested, and selectable.
21. Search responses are cached in SQLite behind a normalized query key
    (normalized text + provider + parameter fingerprint). ChromaDB is used only
    for semantic retrieval over chunked documents, never as a response cache.
    Historical rows never expire; live rows expire after `CACHE_TTL_SECONDS`.
22. Tavily's `published_date` is a beta best-estimate and is treated as a hint;
    strict leakage mode still rejects documents without a usable timestamp.
23. Tip-off is resolved through an injected `game_lookup` backed by the `games`
    table. On a first ingestion run the game may not yet be stored, so the
    provider omits `end_date`/`filter_by_published_date` and relies on the
    deterministic client-side leakage filter; later runs add server-side bounds.
24. The shared SQLite connection uses `check_same_thread=False` with a
    re-entrant database lock because cache reads/writes run in the parallel
    Context-Research worker.

## Naming

R9 and R15 in the requirements refer to a **Deep-Researcher** agent, while the
approved architecture calls the worker **Context-Research**. For this project,
"Deep-Researcher" is treated as the Context-Research worker combined with the
Deep Research MCP capability.
