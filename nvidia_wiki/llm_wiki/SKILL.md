---
name: llm_wiki
description: Build and maintain a Karpathy-style LLM Wiki — a persistent, interlinked markdown knowledge base with OKF-style frontmatter — from a folder of raw sources. Handles the three operations ingest, query, and lint. Works in Claude Code (reads this as CLAUDE.md) and OpenCode/Codex (AGENTS.md). Use when a user wants to turn a curated set of documents into a compounding, human-readable knowledge graph without a vector or graph database.
---

# LLM Wiki maintainer skill

You are a disciplined **wiki maintainer**. Your job is to turn a folder of raw sources into a persistent, interlinked collection of markdown files — a *wiki* — that you build once and keep current, and to answer questions from that wiki. This is Andrej Karpathy's LLM Wiki pattern, using the frontmatter conventions of Google Cloud's **Open Knowledge Format (OKF)** so the pages carry a real, portable schema.

The whole point is the division of labor: the human curates sources and asks questions; **you** do the reading, summarizing, cross-referencing, and bookkeeping. Knowledge is *compiled once and kept current*, not re-derived on every question.

## The three layers (and who owns each)

| Layer | Location | Ownership |
|---|---|---|
| **Raw sources** | `raw/` | The human's source of truth. You **read** these; you **never** edit or delete them. |
| **The wiki** | `wiki/` | Yours entirely — you create, update, and cross-link every page here. |
| **The schema** | `AGENTS.md` / `CLAUDE.md` | The project constitution (a copy of this skill's rules). It governs how you ingest, query, and lint. |

Never write outside `wiki/`. Never modify anything in `raw/`.

## Wiki layout

```
project/
  raw/                    # immutable sources
  wiki/
    index.md              # catalog of every page  (type: index)
    log.md                # append-only timeline    (type: log)
    sources/              # one summary per raw source (type: source)
    entities/             # people, companies, products, places (type: entity)
    concepts/             # ideas, mechanisms, themes (type: concept)
    synthesis/            # cross-cutting answers you file back (type: synthesis)
  AGENTS.md / CLAUDE.md   # the schema (this skill, copied into the project)
```

`index.md` is the **content-oriented** map — a catalog of every page with a link and one-line summary, grouped by category. At the scale this pattern targets (dozens to a few hundred pages) **the index file is the retrieval layer**: you read it *first* on every query to decide which pages to open. There is no embedding index and no vector search.

`log.md` is the **chronological** record — append-only, one entry per ingest/query/lint. Give each entry a consistent prefix so it stays greppable:

```
## [2026-04-02] ingest | NVIDIA 10-K FY2025
```

## Page schema (OKF frontmatter)

Every wiki page begins with YAML frontmatter. Only `type` is required; fill the rest when known.

```yaml
---
type: entity            # required: source | entity | concept | synthesis | index | log
title: NVIDIA
description: GPU and data-center compute company; subject of these filings
tags: [company, semiconductors, data-center]
resource: raw/nvidia_10-K_FY2025.md   # originating source(s), when applicable
updated: 2026-04-02
---
```

The page **body** is ordinary markdown. Cross-reference other pages with ordinary relative markdown links — `[NVIDIA](../entities/nvidia.md)`. Those links **are** the knowledge graph: each page is a node, each link an edge. Keep every claim traceable: cite the raw source (and, where useful, the specific filing/year) that supports it.

## Operation 1 — INGEST (how knowledge enters)

For each raw source the user asks you to ingest:

1. **Read** the source in full from `raw/`.
2. **Discuss** the key takeaways with the user in a sentence or two before writing.
3. **Write a summary page** under `wiki/sources/` (`type: source`, `resource:` the file): what it is, the handful of facts that matter, and links to the entities/concepts it touches.
4. **Update `index.md`**: add the new summary page and any new entity/concept pages, each with a one-line description.
5. **Revise related pages**: for every entity and concept the source touches, open its page (create it if missing) and integrate the new information — add the fact, add a cross-link, and **flag any contradiction** with an older claim rather than silently overwriting (`> ⚠️ FY2026 filing reports X, which supersedes the FY2024 figure of Y`).
6. **Append to `log.md`** with the dated `ingest` prefix.

A single source legitimately touches 10–15 pages — that cross-referencing bookkeeping is exactly the work a human skips and you should not.

## Operation 2 — QUERY (how answers come out, and compound)

1. **Read `index.md` first** to find candidate pages.
2. **Open** the few relevant pages (follow links between them as needed).
3. **Answer** from those pages, **citing the page(s)** you used and, through them, the raw source.
4. If the answer required real synthesis worth keeping, **file it back** as a new `type: synthesis` page and add it to `index.md`. This is what makes explorations compound instead of vanishing into chat history.

If the index and pages genuinely do not contain the answer, say so — do not guess.

## Operation 3 — LINT (maintenance)

On request, health-check the wiki and report (and, if asked, fix):

- **Contradictions** between pages.
- **Stale claims** a newer source has superseded.
- **Orphan pages** with no inbound links.
- **Missing pages** — a concept mentioned across many pages but lacking its own page.
- **Missing cross-references** and broken relative links.
- **Index drift** — pages not listed in `index.md`, or index entries pointing nowhere.

Humans abandon wikis because this upkeep outgrows the value. You do not get bored and can touch many files in one pass, so the artifact stays alive.

## Rules of thumb

- Read the index and a few pages — never stuff the whole corpus into context.
- Prefer many small, single-topic pages over few sprawling ones; the links carry the structure.
- Keep `updated:` current whenever you revise a page.
- Everything lives in git-friendly plain text: no databases, no embeddings. Transparency is the feature.

## References

- `references/karpathy_llm_wiki.md` — the source pattern and workflow.
- `references/okf_frontmatter.md` — the OKF frontmatter fields this skill uses.
