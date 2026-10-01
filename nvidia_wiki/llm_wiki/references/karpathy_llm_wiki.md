# The LLM Wiki pattern (Karpathy)

Source: Andrej Karpathy, *LLM Wiki* (GitHub Gist, 2026).
<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>

## The idea

Vector RAG re-discovers everything from scratch on every query — nothing accumulates. The
LLM Wiki inverts this: the LLM **incrementally builds and maintains a persistent, interlinked
collection of markdown files** that sits between you and the raw sources. When a new source
arrives the model reads it, extracts what matters, and *integrates* it — updating entity pages,
revising summaries, flagging contradictions. Knowledge is compiled once and kept current.

Karpathy's image: **Obsidian is the IDE, the LLM is the programmer, the wiki is the codebase.**
You keep the agent on one side and a markdown browser on the other; the agent edits pages as you
talk and you browse the results.

## Why it works

The bottleneck in any knowledge base is not reading or thinking — it is the **bookkeeping**:
updating cross-references, keeping summaries current, flagging contradictions, holding consistency
across dozens of pages. Humans abandon wikis because that maintenance grows faster than the value.
LLMs do not get bored and can touch fifteen files in one pass, so upkeep falls to near zero and the
artifact stays alive. It is the **Memex** (Vannevar Bush, 1945) finally made practical.

## The "knowledge graph" is just markdown links

Each page is a node; each `[[wikilink]]` or relative markdown link is an edge. The whole graph is
human-readable, diffable, git-versioned, and needs no vector or graph database. It trades raw scale
for transparency and zero infrastructure.

## Three operations

- **Ingest** — read a source, write a summary page, update the index, revise related pages, log it.
- **Query** — read the index, open the relevant pages, answer with citations, optionally file the
  answer back as a new page so explorations compound.
- **Lint** — periodically health-check: contradictions, stale claims, orphans, missing pages and
  cross-references, index drift.

## Two special files

- `index.md` — content-oriented catalog of every page. At moderate scale (a few hundred pages) the
  index alone is enough retrieval; it stands in for a vector store.
- `log.md` — chronological, append-only. Consistent dated prefixes keep it greppable.
