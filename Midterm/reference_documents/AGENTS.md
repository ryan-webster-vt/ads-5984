# AGENTS.md — Mid-term Project: Grants.gov Harvester + VT Science Team Builder

## What this is

ADS 5984 mid-term project. Build a Streamlit application that:

1. Harvests **open** and **closed** solicitations from the grants.gov REST API into a local
   JSON database (open ones displayed, closed ones archived).
2. Lets the user filter/sort/paginate open solicitations, select one, and click **Build Team**.
3. Retrieves relevant College of Science faculty/publication data via RAG (ChromaDB) and asks a
   hosted VT ARC LLM to propose a team with roles and rationale.
4. Supports follow-up refinement (swap members, adjust scope) and grounds every factual claim in
   retrievable source text.

The architecture is a **deterministic workflow**, not an autonomous agent. Predefined steps,
predictable step count, bounded cost/tokens, developer-controlled termination. The trade-off is
rigidity on unforeseen edge cases — handle them with explicit branches, not free-form autonomy.

Authoritative spec: `mid_term_project_reqs_and_arch_ryanw07.qmd` (rendered as the matching
`.pdf`). The assignment's written answers must be authored without AI; that restriction applies to
the exercise document, not to implementing the application it describes.

## Layout

```
Midterm/
  mid_term_project_reqs_and_arch_ryanw07.qmd   # assignment + architecture (source of truth)
  mid_term_project_reqs_and_arch_ryanw07.pdf   # rendered spec
  rag_chunking.py                              # raw_data CSV -> document-aware chunks -> ChromaDB
  raw_data/authors.csv                         # faculty metrics (H-index, output, dept, rank, ...)
  raw_data/publications.csv                    # publications incl. Title, Year, Source title, Abstract
  .env                                         # secrets/config (gitignored, NEVER commit/expose)
```

Planned output (create as needed): Streamlit app, grants.gov client, local JSON store, ingestion
script, retrieval/rerank module, team-generation/verification module.

## Environment & commands

- Use the `ads_5984` conda environment.
- `.env` keys (names only): `OPEN_AI_ENDPOINT`, `OPEN_AI_API_KEY`, `OPEN_AI_MODEL` (VT ARC LLM),
  `OLLAMA_ENDPOINT`, `EMBED_MODEL` (embedding model, e.g. `qwen3-embedding:8b`). Load with
  `load_dotenv()`.
- Only reachable on eduroam or the Cisco VPN.
- Typical commands once the app exists:
  - `python rag_chunking.py --dry-run` — preview chunk counts and samples
  - `python rag_chunking.py` — ingest `raw_data/` into ChromaDB (`chroma_db/`, collection `vt_science_rag`)
  - `streamlit run app.py` — launch the UI
  - `quarto render mid_term_project_reqs_and_arch_ryanw07.qmd` — rebuild the spec PDF
- Verify deps: `python -c "import streamlit, chromadb, openai, dotenv; print('deps OK')"`

## Functional requirements (all must hold every run)

1. User-friendly Python **Streamlit** UI.
2. Harvest **open** grants.gov solicitations via REST API into a local JSON database.
3. Archive **closed** solicitations in the same local JSON database.
4. Filter/sort open solicitations by relevant fields (type, date, amount, etc.).
5. Paginate the open list; show total open count and last-update timestamp.
6. Select a solicitation, then click a **Build Team** button.
7. **Refresh** re-calls the API: upsert open, archive closed, persist, redisplay.
8. RAG over `raw_data/authors.csv` (faculty descriptions + metrics such as H-index).
9. RAG over `raw_data/publications.csv` (titles, years, venues, abstracts). Prototype uses the
   CSV; final product uses the **Elsevier API** instead of `publications.csv`.
10. After Build, move the user to a chat interface that auto-constructs the team from an
    **optimized prompt template** plus retrieved RAG context.
11. Use the **hosted VT ARC LLMs**.
12. Allow follow-up on the initial team.
13. Apply follow-ups (swap members, change scope) and return a revised team.
14. Well-structured code with **descriptive comments** and run instructions.
15. Holistic team construction: contributor metrics **and** publications (recency + journal
    prestige at minimum) drive members and role definitions.
16. **Optimize the original prompt** with an LLM before generation.
17. Ground every fact in source material; every claim must trace back to original text.
18. High reliability: repeating the same prompt yields relatively consistent results. Wildly
    divergent output is failure.

## Architecture

**Research-data ingestion.** Read `authors.csv` and `publications.csv` (prototype) or the Elsevier
API (final) alongside `authors.csv`. Chunk **document-aware**: one author / one publication per
chunk, never splitting a row or orphaning headers, so no faculty member's data leaks into another's.
Embed with `qwen3-embedding:8b` on the ADS server and store chunks + vectors + metadata in
ChromaDB. Re-run the pipeline only when source data changes.

**Grant-data ingestion.** Call grants.gov, upsert opportunities into the local JSON database, mark
closed ones archived, display only open ones.

**Team generation + refinement.** User selects a grant and enters a query → prompt-optimization LLM
turns grant text + query into an optimized retrieval query → embed query with the same embedding
model → ANN search returns `k_in` chunks → LLM re-ranker scores and cuts to `k_out` → team-generation
LLM proposes members, roles, and rationale → Streamlit persists/displays the draft → loop over user
refinement (prior team + feedback + retrieved context) until accepted or an iteration limit is
reached → persist final team.

Keep `k_in`, `k_out`, temperature, embedding model, re-ranker, corpus, and index version fixed and
recorded.

## LLM/RAG risks and required mitigations

| # | Risk (requirement) | Mitigation |
|---|---|---|
| 1 | Suboptimal chunking splits answers or leaks data across authors/publications (8, 9) | Document-aware chunking: one row/record per chunk, preserve headers and structure |
| 2 | Retrieved docs in the middle get ignored (10) | LLM re-ranker puts the most relevant context first/last; keep `k_out` small |
| 3 | Contradictory follow-ups confuse retrieval; longer context increases hallucination (13) | Rolling summary of user asks and clarifications |
| 4 | No verification of claims; hard to trace facts (17) | Chain-of-Verification: context-free verification questions + re-retrieval; attach source citations |
| 5 | Same prompt can yield different outputs (18) | Low temperature, fixed `k_in`/`k_out`, same embedding model/re-ranker, pinned corpus and index version |

Additional safeguards: require citations/grounding for every claim, cap refinement iterations,
validate/retry LLM output, cache or snapshot retrieval results where useful, and keep prompts terse
(each call costs money and latency).

## Conventions

- Follow the pattern in `rag_chunking.py`: a ChromaDB `EmbeddingFunction` calling Ollama's
  OpenAI-compatible endpoint, so insert and query always use the same model.
- Idempotent ingestion: delete/recreate or upsert collections so re-runs are safe.
- Keep grants.gov data separate from RAG data; never mix embeddings for grants and faculty.
- Prefer small, testable modules over one large script. Add descriptive comments and run
  instructions (requirement 14).
- `raw_data/` is read-only source material. Never edit or delete it.
- `.env` is gitignored: never commit, print, or log `OPEN_AI_API_KEY` or any secret.

## Verifying work

- `python rag_chunking.py` completes, reports chunk counts, and ChromaDB `count()` matches.
- `streamlit run app.py` starts; refresh persists open/closed grants to JSON; filters, sort, and
  pagination behave; counts and last-update shown.
- Selecting a grant and clicking **Build Team** returns members, roles, and rationale with source
  citations; a follow-up swaps members and returns a revised team.
- Re-running identical input produces materially the same team (requirement 18).
- Spot-check that each factual claim maps to a retrieved chunk / CSV row or abstract.
