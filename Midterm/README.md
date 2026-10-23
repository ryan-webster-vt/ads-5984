# VT College of Science Grant Team Builder

Phase 5 implementation of the ADS 5984 midterm project. The app refreshes live
Grants.gov opportunities into a local JSON store, filters/sorts/paginates them,
builds a grounded, cited Virginia Tech College of Science team for a selected
opportunity using the full faculty/publication index in ChromaDB, and refines the
team through follow-up constraint changes. LLM stages run under explicit call,
token, wall-clock, retry, and circuit-breaker limits.

The full architecture, requirements, phases, and decisions are in `PLAN.md`.

## Design choice

The system uses ChromaDB vector RAG rather than GraphRAG. The corpus is small
enough to compute observed co-authorship deterministically from shared
publication IDs, and team constraints are easier to audit in Python than in a
graph database. Publication abstracts and metadata are embedded with the same
configured model used for queries. The LLM is bounded to role/rationale
generation, verification, and follow-up interpretation. Identity joins,
deduplication, eligibility, candidate scoring, PI policy, tie-breaking, and
citation validation stay in code. This keeps every hard constraint auditable even
when the model is unavailable, slow, or returns malformed output.

Opportunity data follows the same deterministic-first rule. The Grants.gov open
feed is authoritative: records missing from a complete feed are archived, while
page failures preserve the last good snapshot. Detail descriptions and award
amounts are cached by opportunity ID and revision; a failed detail fetch keeps
search-level fields and marks the record for later retry rather than inventing
data.

The deterministic baseline and validator are the reproducibility backstop: if the
LLM output is unavailable or unsupported, the app shows the baseline team and
records the failure rather than presenting an ungrounded recommendation.

## What works in Phase 5

- Live Grants.gov `search2` pagination (`rows=1000`) until `hitCount` is covered.
- Revision-recorded detail cache, concurrent `fetchOpportunity` calls, retries,
  timeouts, and partial-detail handling.
- Normalization of status, dates, awards, eligibility, CFDA numbers, and HTML
  descriptions; missing or `"none"` amounts become `null`.
- Atomic local opportunity storage with `.bak` backup and a cross-process lock.
- Complete-feed archiving: previously open records absent from a fully successful
  feed become `archived`; closed/expired/archived records are hidden.
- Partial page failures never archive and never overwrite the last good snapshot.
- Streamlit browser with status/agency/document-type/CFDA/title filters, open
  and award date/amount filters, sortable columns, page sizes, pagination, open
  and archived counts, expired-record counts, and last-update time.
- Select an opportunity, then build a team from the full attributed publication
  corpus with cited evidence and saved run metadata.
- Structured query optimization (`prompts/optimize_query.md`) with a deterministic
  fallback when VT ARC is unavailable or returns invalid JSON.
- Multi-query retrieval union (configurable `K_in=30`, union cap 60) and batched
  LLM reranking (`prompts/rerank.md`, batches of 8, at most 4 calls).
- Deterministic evidence diversity: one chunk per publication, at most three per
  faculty, with concept-coverage preference.
- Configurable scoring weights for relevance, expertise coverage, author-level
  metrics, recency, observed co-authorship, and department diversity. Missing
  metrics drop their weight instead of being imputed.
- Explicit constraint state for team size, required/excluded members, expertise,
  department preferences, PI preference, tenure-track local policy, and recency.
- LLM generation (`prompts/generate_team.md`), one bounded repair, one
  evidence-only verifier (`prompts/verify_team.md`), and deterministic fallback
  to the baseline team if any grounding or constraint check fails.
- Follow-up conversation (`prompts/parse_followup.md`) for requests such as
  “replace a member with a chemist”, “add climate-change expertise and keep the
  team at four”, “shrink to three”, and “only tenure-track faculty”. Include and
  exclude of the same person asks for clarification instead of guessing.
- Evidence inspector showing roster facts, exact publication excerpts,
  provenance, matched concepts, rerank scores, inferred suitability, and run
  metadata/prompt versions.
- Full `raw_data/authors.csv` roster loading with exact Scopus ID matching,
  duplicate-ID conflict detection, EID/DOI/title deduplication, and the full
  attributed-publication index (19,986 canonical works, 32,892 vectors).
- Bounded LLM execution: per-request timeouts, exponential backoff with jitter,
  `Retry-After` support, non-retryable 4xx handling, a failure-count circuit
  breaker, and per-run call/token/wall-clock budgets. Budget exhaustion falls
  back to the deterministic baseline instead of hanging.
- A redacting logger and error path for API keys, bearer tokens, and URL
  credentials; run metadata and the UI never echo raw exception text.
- Version-keyed retrieval caching with explicit `--no-cache` evaluation. Cache
  keys include index version, corpus hash, embedding model, prompt version,
  `K_in`/`K_out`, union cap, and rerank settings; transient reranker failures are
  never cached.
- Persisted run metadata for every build (including follow-up turns) with LLM
  call count, token usage, budget state, cache status, latency, validation
  issues, and redacted errors.
- `scripts/eval_repeatability.py` reports member/PI/evidence stability, validator
  pass rate, p50/p95 latency, call/token use, and whether the runtime budget was
  met. Five uncached offline runs on the fixed fixture produced 1.0 member
  Jaccard, 1.0 PI agreement, and a 100% validator pass rate.
- Elsevier/Scopus adapter interface and response parsers, tested against synthetic
  fixtures only. Live Elsevier access is not claimed.

Phase 5 intentionally does **not** include live Elsevier ingestion or the final
submission package. Those remain Phase 6.

### Live refresh measurements (2026-10-22)

- Grants.gov open feed: **1,468** records (869 posted, 599 forecasted).
- Full cold refresh: 1,468 detail fetches in **28.3 s**.
- Warm-cache refresh: **0.67 s** with all 1,468 details reused.
- Active open records after hiding **206** records with an explicit past close
  date: **1,262**. The UI and CLI report both the feed count and active count.
- A synthetic previously open record was absent from a complete live feed and was
  archived with reason `absent_from_complete_open_feed`.

These numbers are observations from one live run, not guarantees about future
Grants.gov contents or response times.

### Live team-build budget measurements (2026-10-22)

- Five uncached offline runs against the fixed fixture (full-v1 index and real
  embedding endpoint): member Jaccard 1.0, PI agreement 1.0, evidence Jaccard
  1.0, validator pass rate 1.0, p95 latency 294–334 ms, 0 LLM calls.
- One live VT ARC run with the 85 s per-run wall budget: 63.0 s, 2 LLM calls,
  0 validator issues, deterministic baseline fallback after an LLM timeout, and
  within the 90 s p95 reporting target.
- A slower live run before tightening the budget took 259 s and 5 LLM calls.
  The current per-run wall budget and per-request timeout cap that behavior; the
  app reports the fallback instead of waiting indefinitely.

The live LLM endpoint can be slow or unavailable. The deterministic baseline is
the reliability backstop; bounded budgets trade some semantic generation quality
for predictable latency and a complete, cited team.

## Environment

Python 3.12 is expected. The current environment was verified with Python
3.12.13.

Create a `.env` file in the project root. Never commit it. Required key names:

```text
OPEN_AI_ENDPOINT
OPEN_AI_API_KEY
OPEN_AI_MODEL
OLLAMA_ENDPOINT
EMBED_MODEL
```

Optional Elsevier keys (the adapter is fixture-tested now; live ingestion arrives
in Phase 6):

```text
ELSEVIER_API_KEY
ELSEVIER_INSTTOKEN
```

VT ARC requires eduroam or Cisco VPN. `OPEN_AI_MODEL` may be `vt-arc-llm`; that
is a model name, not a Python package. Generation uses the `openai` SDK.
Embeddings use the OpenAI-compatible endpoint at `OLLAMA_ENDPOINT` with
`EMBED_MODEL`. Grants.gov search and detail endpoints need no key.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If the packages are already available in the active environment, installing
again is optional.

## Build the research index

```bash
python3 scripts/build_index.py --rebuild
```

This builds the `research_full_v1` Chroma collection under `data/chroma/` and
writes `data/index_manifest.json`. The default source is `raw_data/publications.csv`
and the default scope is the full attributed corpus.

```bash
# Reuse a compatible index; rebuild automatically if only the CSV content changed
python3 scripts/build_index.py --incremental

# Optional reduced-scope preview build (manifest records that it is not full)
python3 scripts/build_index.py --limit 500

# Elsevier option is present but fixture-tested only until Phase 6
python3 scripts/build_index.py --source elsevier
```

The full build loads 21,485 raw rows, deduplicates them to 21,428 canonical works,
and indexes the 19,986 works with at least one eligible roster author. The
remaining 1,442 canonical works are excluded from faculty evidence and counted in
the manifest. Long abstracts are split sentence-aware into at most five chunks;
each publication remains a separate evidence document.

The embedding model and dimension are recorded in the manifest. If the embedding
model, chunker version, schema version, corpus hash, or index version changes, the
app and CLI refuse the stale index and instruct you to rebuild. Never reuse a
manifest/collection with a different embedding model.

## Refresh opportunities

Use the **Refresh opportunities** button in the app sidebar or run:

```bash
python3 scripts/refresh_opportunities.py
```

The default run paginates the full open feed and fetches details for every open
record. Details are cached under `data/grants_detail_cache/` (generated, ignored
by git). The opportunity store is `data/opportunities.json` with
`data/opportunities.json.bak` as the previous snapshot.

Useful options:

```bash
# Reuse fresh detail cache entries (24 h default)
python3 scripts/refresh_opportunities.py --ttl-hours 24

# Force a complete detail refresh
python3 scripts/refresh_opportunities.py --force-details

# Search-only quick refresh; amounts remain Unknown
python3 scripts/refresh_opportunities.py --no-details

# Explicit demo limit; marks the feed partial and never archives
python3 scripts/refresh_opportunities.py --max-pages 1
```

Archive policy:

- Only a complete open-feed pass can archive records.
- A record previously open but absent from a complete feed becomes `archived`
  with reason `absent_from_complete_open_feed`.
- A search/page failure leaves the previous JSON untouched and reports `failed`.
- A detail failure reports `partial` but does not invalidate the complete open
  feed; the affected record keeps search fields, is marked `detail_status=failed`,
  and its award amount is shown as Unknown.
- Records with an explicit past close date are hidden from the browser while
  remaining in the store with their API status.

## Run the app

```bash
python3 -m streamlit run app.py
```

Refresh opportunities, filter/sort/paginate the list, select one record, and click
**Build team for selected opportunity**. The team builder optimizes the query,
retrieves and reranks evidence, scores candidates, and offers **Build Team**.
After a team appears, use the chat input to change size, members, expertise,
department preferences, PI preference, or the tenure-track local policy. If VT ARC
is unavailable, slow, or returns invalid structured output, the deterministic
baseline team is still shown and the limitation is reported.

If no live store exists yet, the app shows the saved real Grants.gov opportunity
fixture (NSF Long Term Research in Environmental Biology, ID 330296) so the team
builder remains usable offline.

## Tests

```bash
python3 -m pytest -q
python3 -m ruff check .
```

The tests are offline. They cover exact identity joins, the duplicate-ID
conflict, full-corpus counts, deduplication, chunking (including long abstracts),
manifest mismatch detection, fixture normalization, search pagination, partial
page failures, detail caching, atomic store writes, complete-feed archiving,
filter/sort/pagination oracles, retrieval aggregation, query optimization and
fallback, batched reranking and diversity caps, configurable scoring and missing
metrics, constraint merging and member resolution, scripted follow-ups, team
validation, verifier/repair fallback, prompt injection, five-run deterministic
repeatability, the VT ARC stray-brace parser fallback, and Elsevier fixture
parsing. Phase 5 adds failure injection for 429/500/timeout/401/refusal/malformed
JSON/invalid schema, LLM and embedding failures, call/token/wall budgets, the
circuit breaker, secret redaction, retrieval-cache keys/TTL/no-cache behavior,
pipeline metadata persistence, and evaluation metrics. Fixtures prove parsing and
offline behavior only; they do not prove that live Grants.gov, Elsevier, VT ARC,
or embedding integrations work. Grants.gov and the live VT ARC optimizer/reranker
were also smoke-tested as described above.

## Data notes

- `raw_data/` is read-only and unchanged.
- `Top Journal Quartile %` is an author-level metric. It is not proof that a
  specific publication is in a top-quartile journal.
- Corporate/International Collaboration % are author-level aggregates, not
  observed team co-authorship.
- The duplicate Scopus ID shared by two different people is flagged and
  excluded from automatic selection.
- The roster contains no faculty biographies, availability, leadership, or
  sponsor-eligibility data. The app does not invent them.
- The corpus excludes publications with no eligible exact roster ID: 1,425 rows
  have no roster ID at all and 18 more are attributable only to the conflicted
  duplicate ID. The manifest records both raw and canonical unattributed counts.
- Grants.gov occasionally lists records whose explicit close date is already
  past. They remain in the store but are hidden from the active list; the UI shows
  the hidden count.
- Live multi-member VT ARC generation can be slow or return a structured payload
  that fails validation. The app reports the failure and shows the deterministic
  baseline. Per-run call/token/wall budgets, per-request timeouts, retries, and a
  circuit breaker bound the wait; run metadata and the repeatability report record
  the actual latency and call count.
- `data/retrieval_cache/` is generated and ignored by git. Entries expire after
  24 hours and are invalidated automatically when the index version, corpus hash,
  embedding model, prompt version, query, or retrieval settings change.

## Key commands

```bash
python3 scripts/build_index.py --rebuild
python3 scripts/build_index.py --incremental
python3 scripts/refresh_opportunities.py
python3 -m streamlit run app.py
python3 scripts/eval_repeatability.py --runs 5 --offline --no-persist
python3 scripts/eval_repeatability.py --runs 5 --no-persist
python3 -m pytest -q
python3 -m ruff check .
```

`eval_repeatability.py` runs uncached by default. Add `--use-cache` to exercise
the retrieval cache or `--clear-cache` to invalidate it first. The script exits
nonzero when member Jaccard, PI agreement, validator pass rate, or the runtime
budget misses its target.

See `AGENTS.md` for durable project rules and `PLAN.md` for the full design.
