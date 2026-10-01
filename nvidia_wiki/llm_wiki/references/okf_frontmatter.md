# OKF frontmatter

Source: Google Cloud, *Open Knowledge Format (OKF) v0.1* (2026). OKF is a vendor-neutral markdown
spec that formalizes the LLM-wiki pattern: a directory of markdown files with YAML frontmatter,
linked to each other with ordinary markdown links to form a knowledge graph. It ships a static
HTML viewer that renders an OKF repository as an interactive graph with no backend.

This skill borrows OKF's **frontmatter** so wiki pages carry a real, portable schema instead of an
ad-hoc convention.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `type` | **yes** | The page kind. This skill uses: `source`, `entity`, `concept`, `synthesis`, `index`, `log`. |
| `title` | no | Human-readable page title. |
| `description` | no | One-line summary (also used as the page's index entry). |
| `resource` | no | Originating raw source(s) — e.g. `raw/nvidia_10-K_FY2025.md`. |
| `tags` | no | List of free-form tags for grouping. |
| `updated` | no | ISO date the page was last revised. |

Everything below the frontmatter is an ordinary markdown body. The `type` field is the only hard
requirement; fill the rest whenever the value is known.

## Example

```yaml
---
type: concept
title: Data-center compute
description: NVIDIA's largest and fastest-growing reporting segment
tags: [segment, revenue, gpu]
resource: raw/nvidia_10-K_FY2025.md
updated: 2026-04-02
---
```

## Relation to this skill

- **Karpathy pattern** provides the workflow (ingest / query / lint) and the two special files
  (`index.md`, `log.md`).
- **OKF** provides the per-page frontmatter schema and — optionally — a zero-backend HTML graph
  viewer for the finished wiki.

They are the same idea at two levels of formality; this skill uses the loose workflow with the
formal frontmatter.
