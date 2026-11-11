# ADR-006: Tavily search, a SQLite search cache, and conditional live research

- **Status:** Accepted; stages A and B implemented in Phase 4.5 (2026-11-10);
  stages C and D remain gated to Phases 5–7
- **Date:** 2026-11-10
- **Deciders:** Project author
- **Related requirements:** R4–R8, R22, R24, R25
- **Supersedes:** the provider choice in ADR-005; the MCP adapter itself remains

## Context

Context-Research needs web discovery. Three facts drove this decision:

1. **Brave is not viable at this dataset size.** Its free allowance is $5 of
   monthly credit (~1,000 searches) and a card is required. Training covers
   three seasons (~17,900 games); one search per game would cost ~$90 and several
   hours, for evidence whose marginal analytic value is low because the signal
   lives in structured stats and odds.
2. **Tavily's free tier is sufficient and needs no card.** The Researcher plan
   gives 1,000 credits/month; a basic search costs 1 credit. A keyless mode
   (`X-Tavily-Access-Mode: keyless`) supports `/search` and `/extract` with no
   account at all, with an identical response schema.
3. **One Tavily call replaces two MCP calls.** `/search` with
   `include_raw_content` returns page text, and `include_published_date` returns
   a publication estimate, so the search-then-fetch fanout (up to 25 fetches per
   game) collapses into a single request.

The current pipeline also searches unconditionally: every ingested game triggers
context research even when structured statistics already answer the question.
That is the expensive ordering, not the necessary one.

## Decision 1 — Tavily is the default Context-Research search backend

- The `ContextResearchProvider` protocol (`get_documents(game_id, season)`) is
  unchanged. Add `TavilyContextResearchProvider` beside the fixture and MCP
  providers. No agent, service, or pipeline code changes.
- New settings: `TAVILY_API_KEY`, `TAVILY_BASE_URL`, `TAVILY_SEARCH_DEPTH`
  (`basic`/`advanced`/`fast`/`ultra-fast`), `TAVILY_TOPIC`
  (`general`/`news`), `TAVILY_MAX_RESULTS`, `TAVILY_INCLUDE_RAW_CONTENT`,
  `TAVILY_KEYLESS`, `TAVILY_TIMEOUT_SECONDS`.
- New CLI choice: `--context-provider tavily`.
- **Leakage (R24):** pass `end_date` (tip-off date) and
  `filter_by_published_date=true` so the provider filters server-side, *and*
  keep the existing client-side `LeakageFilter`. Server-side filtering reduces
  exposure; it never replaces the deterministic check.
- **Whitelisting:** pass `include_domains` from `config/sources.yaml` with
  `include_domains_mode=restrict`, *and* keep the client-side `SourceWhitelist`.
  Tavily's list caps at 300 domains.
- Tavily's `published_date` is explicitly "best estimate of when the source was
  published or last updated" and is marked beta. Treat it as a hint: strict
  leakage mode still rejects documents without a usable timestamp.
- Reuse the shared HTTP client factory (timeouts, retries, rate limiting). No new
  SDK dependency; the REST shape is small enough to call directly.

**Requirements deviation.** R5 names "MCP Brave/Fetch/Deep Research". The MCP
adapters stay in the codebase, stay tested, and stay selectable
(`--context-provider mcp`); Tavily becomes the *default*. This is recorded here
per `AGENTS.md` ("document any such change"), and open question 1 below asks
whether the rubric needs a live MCP call during the showcase.

### Cost model

| Step | Credits | Notes |
|---|---|---|
| Basic search | 1 | `search_depth=basic` |
| Advanced search | 2 | use only for gap-driven follow-ups |
| Extract | 1 per 5 URLs | unnecessary if `include_raw_content` is used |
| Historical game (full) | 1 | one search, content inline |
| 17,900-game training set | 17,900 | ~18 months of free credits |

The cache in Decision 2 is what makes this affordable: repeated seasons,
re-runs, and gap-driven retries do not re-spend credits.

## Decision 2 — SQLite caches search responses; Chroma is only for semantics

Two separate concerns, deliberately not merged:

- **SQLite** answers "have I already seen this exact query, and what came back?"
  It is keyed deterministically and read before any network call.
- **ChromaDB** answers "which stored passages are semantically relevant to this
  query?" It holds chunked `SourceDocument` text with metadata, never raw
  search responses.

Using Chroma as a response cache would be wrong: it cannot express exact-key
reuse, it cannot expire entries cheaply, and it would blend cache entries into
retrieval results.

### Schema (additive; new migration)

`search_queries` — one row per distinct normalized request:

| Column | Purpose |
|---|---|
| `query_id` | primary key |
| `query_hash` | hash of normalized query + provider + params fingerprint |
| `normalized_query` | the normalized text actually sent |
| `provider` | `tavily`, `brave-mcp`, `fixture` |
| `params_hash` | fingerprint of depth/topic/domains/date window |
| `game_id`, `season` | nullable link to the requesting game |
| `created_at`, `last_used_at` | timestamps (UTC) |
| `expires_at` | nullable; see TTL policy |
| `result_count`, `credits_used` | for auditing spend |
| `payload` | provider response metadata / diagnostics |

`search_hits` — one row per returned URL, enabling URL-level reuse:

| Column | Purpose |
|---|---|
| `hit_id` | primary key |
| `query_hash` | foreign key to `search_queries` |
| `url`, `canonical_url` | requested and normalized form |
| `source_domain` | for whitelist auditing |
| `title`, `score` | ranking metadata |
| `published_at`, `retrieved_at` | timestamps (UTC) |
| `content_hash`, `content` | stored text (nullable content) |
| `metadata` | JSON: topic, provider, depth, extras |

Indexes: `(query_hash)`, `(url)`, `(game_id)`, `(published_at)`.

### Normalization

A single pure, unit-tested function: Unicode NFKC, casefold, collapse internal
whitespace, strip surrounding punctuation, keep token order and stopwords.
Token order matters — "duke at north carolina" and "north carolina at duke" are
different queries, so sorting tokens is explicitly rejected.

### TTL policy

- **Historical games (tip-off in the past):** `expires_at = NULL`. The evidence
  asset is immutable, and re-searching would spend credits for the same answer.
- **Live/future games:** `expires_at = now + CACHE_TTL_SECONDS`, since pregame
  news genuinely changes.

## Decision 3 — live research becomes conditional

Ordering for any analysis, cheapest and most reliable first:

1. **Compute structured preconditions** from stored statistics — deterministic,
   free, no network.
2. **Retrieve existing evidence** — SQLite for known documents, Chroma for
   semantic recall over stored passages.
3. **Search only for gaps** — games/preconditions still lacking support.

Consequences:

- Ingestion becomes **stats-first**; documents become on-demand. `ingest`
  defaults to no context research; `--context-provider` selects
  `none`/`fixture`/`tavily`/`mcp`.
- Spend scales with *gaps*, not games, which is what makes a 1,000-credit month
  realistic.
- It requires an evidence-gap contract, e.g.
  `ContextGap(game_id, precondition_id, reason, required_kind)` — a **new
  Pydantic model**, and a dependency on Phase 6 (the extractor), which is not
  built yet.

## Staging

| Stage | Scope | Phase | Status |
|---|---|---|---|
| A | `TavilyContextResearchProvider`, settings, `--context-provider tavily`, tests | 4.5 (now) | Implemented |
| B | SQLite search cache (normalization, keys, TTL), cache-first reads, tests | 4.5 (now) | Implemented |
| C | Chroma semantic retrieval over cached documents | 5 | Gated |
| D | `ContextGap` contract and gap-driven orchestration | 6/7 | Gated |

Stage D is the only part that changes pipeline ordering; A and B are additive and
can ship without touching agent flow.

### Stage A/B implementation notes

- `integrations/tavily.py` implements `TavilyContextResearchProvider` over the
  shared HTTP client. `--context-provider` now accepts
  `auto`/`fixture`/`tavily`/`mcp`/`none`; `auto` resolves to `fixture` with
  fixture stats and `tavily` with live stats. The MCP adapters stay selectable.
- `config.py` exposes `TavilySettings`; `.env.example` documents the placeholders.
- `storage/schema_v2.sql` adds `search_queries` and `search_hits`.
  `services/search_cache.py` owns normalization, parameter fingerprints, query
  hashes, and the TTL policy; `SearchCacheRepository` owns SQL.
- Both providers and cache rows are typed contracts (`SearchHitRecord`,
  `CachedSearchResponse`) and are exported to `docs/contracts/`.
- Because the Context-Research worker runs in a thread, the shared SQLite
  connection uses `check_same_thread=False` and a re-entrant lock serializes
  statement/transaction access.
- The Context-Research protocol receives only `game_id`/`season`, so tip-off
  comes from an injected lookup over the `games` table. The orchestrator now
  resolves and persists each batch's games *before* dispatching the workers
  (`ResearchOrchestrator._prefetch_games`), and hands the same games to
  Historical-Stats so the provider is called exactly once. Without that, a cold
  run searched before the `games` row existed: the request omitted `end_date`, the
  response was cached under a different `params_hash` with a 24h TTL instead of a
  permanent row, and re-running the season spent a second credit per game. The
  prefetch keeps the tip-off known before any search is issued, so the cache key
  is stable from the first run; the deterministic client-side leakage filter
  still applies when a game genuinely cannot be resolved.
- A game whose statistics cannot be resolved is **not searched for**. Such a
  record is quarantined as `missing_historical_game`, so a search would spend a
  credit on a document set that is never persisted; Context-Research is scoped to
  the resolved ids of the batch instead. The e2e cache test therefore expects
  three Tavily calls for four requested games.

## Consequences

- No paid search dependency; the free tier is sufficient with caching.
- One network call per game instead of one search plus up to 25 fetches.
- Leakage and whitelisting are enforced twice (provider-side and client-side).
- A new SQLite migration and two new tables must be maintained and migrated.
- Spend is auditable per query, which R25 and the cost-control rubric expect.
- Requirement R5 coverage depends on the MCP adapters remaining present, which
  Decision 1 preserves.

## Open questions

1. **R5 interpretation:** must the showcase exercise MCP *live*, or is having
   tested adapters plus a selectable `--context-provider mcp` sufficient? Only
   this determines whether a Brave key is needed at all.
2. **Timestamp trust:** is Tavily's `published_date` (beta, "best estimate")
   acceptable as the leakage timestamp for historical documents, or must a
   document be excluded unless a date is parsed from the page itself?
3. **Cache scope:** global across runs (shared, recommended) or per-run? A global
   cache maximises credit reuse but needs provenance columns for reproducibility.
4. **Keyless or keyed:** keyless mode is ideal for demos, but credit accounting
   and dashboards require a key. Recommend keyed by default with
   `TAVILY_KEYLESS=true` as an offline/demo fallback.
