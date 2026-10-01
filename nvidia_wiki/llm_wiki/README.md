# LLM Wiki skill

A Claude Code / OpenCode skill that turns a folder of raw sources into a **Karpathy-style LLM
Wiki** — a persistent, interlinked markdown knowledge base with **OKF-style frontmatter** — and
answers questions from it. No vector database, no embeddings: the knowledge graph is plain markdown
files linking to each other, and the `index.md` file is the retrieval layer.

Authored for **ADS 5984: Agentic AI in Practice** (Lab 3b). See the course primer
*"The LLM Wiki memory pattern"* for the concept, and the lab for the guided build.

## What it does

The agent performs three operations against a `wiki/` folder it owns:

- **Ingest** — read a source, write a summary page, update the index, revise related entity/concept
  pages, log it.
- **Query** — read `index.md`, open the relevant pages, answer with citations, optionally file the
  answer back so explorations compound.
- **Lint** — health-check the wiki (contradictions, stale claims, orphans, missing pages/links,
  index drift).

## Contents

| Path | What it is |
|---|---|
| `SKILL.md` | The skill itself — the maintainer's instructions (ingest / query / lint + OKF schema). |
| `assets/AGENTS.md` | Drop-in project constitution. Keep as `AGENTS.md` for OpenCode/Codex, rename to `CLAUDE.md` for Claude Code. |
| `assets/wiki_query.py` | A from-scratch query helper (GLM-5.2, no embeddings) a chatbot UI can import. |
| `references/karpathy_llm_wiki.md` | The source pattern. |
| `references/okf_frontmatter.md` | The OKF frontmatter fields used. |

## Install

```bash
./install_skill.sh   # installs to ~/.claude/skills/llm_wiki/
```

For OpenCode, copy `assets/AGENTS.md` into your project root (as `AGENTS.md`); the skill body lives
in `SKILL.md`.

## Compare against

Two community implementations of the same pattern, useful as production references:

- <https://github.com/Astro-Han/karpathy-llm-wiki> — Agent-Skills-compatible (Claude Code, Cursor, Codex).
- <https://github.com/praneybehl/llm-wiki-plugin> — Claude Code plugin.
