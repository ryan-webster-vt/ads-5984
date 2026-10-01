# PROGRESS — NVIDIA 10-K wiki ingest (FY2022–FY2026)

> **Status:** complete
> **Last updated:** 2026-09-17 — all five filings ingested; lint pass fixed 1 contradiction, 3 stale claims, added 2 missing concept pages (28 pages, 0 broken links, 0 orphans)
> **Current step:** done

## Resume here
- Task complete. If resuming: read `wiki/index.md` first; all five source pages exist in `wiki/sources/`.
- Optional future work: file a `wiki/synthesis/` page comparing revenue by market platform across FY2022–FY2026 (data already captured on the five market-platform concept pages).
- `PROGRESS.md` is scoped to `nvidia_wiki/` (the outer repo holds unrelated projects).

## Plan summary
Maintain the LLM Wiki described in `AGENTS.md`. One source-summary page per 10-K in
`wiki/sources/`, keep `wiki/index.md` catalog current, revise entity/concept pages the filing
touches, and append a dated `wiki/log.md` entry per ingest. When a newer filing changes a number
an older one reported (revenue, segments, risk factors), update the page and **flag the change**
rather than silently overwriting. Definition of done: all five filings ingested, index lists every
page, log has one entry per filing, no broken cross-links, `updated:` dates current.

## Steps
| # | Step | Status | Verification |
|---|------|--------|--------------|
| 1 | Ingest FY2022 | `[x]` | source+index+log exist; spot-checked vs raw:989 |
| 2 | Finish FY2023 bookkeeping | `[x]` | FY2023 source + concept rows present in index and log |
| 3 | Ingest FY2024 | `[x]` | `wiki/sources/nvidia_10-K_FY2024.md`; source row + log entry present |
| 4 | Ingest FY2025 | `[x]` | `wiki/sources/nvidia_10-K_FY2025.md`; source row + log entry present |
| 5 | Ingest FY2026 | `[x]` | `wiki/sources/nvidia_10-K_FY2026.md`; source row + log entry present |
| 6 | Final consistency pass | `[x]` | python check: 0 unindexed pages, 0 broken links, all 26 pages have OKF `type` |

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## Progress to date (append-only journal)
- 2026-09-17 — Reconstructed prior session state. Step 1 complete: `wiki/sources/nvidia_10-K_FY2022.md`, 4 entity pages, 11 concept pages, index and log all present and consistent with `raw/nvidia_10-K_FY2022.md` (spot-checked revenue $26.91B/+61% at raw:989).
- 2026-09-17 — Step 2 in progress: FY2023 source page and FY2023 revisions existed, but `wiki/index.md` and `wiki/log.md` lacked FY2023 entries.
- 2026-09-17 — Step 2 complete: added FY2023 source row + revised concept descriptions to `wiki/index.md` and the FY2023 log entry.
- 2026-09-17 — Steps 3–5 complete: extracted full dossiers from FY2024/FY2025/FY2026 raw filings (spot-checked key figures against raw), authored 3 source pages, revised `entities/nvidia.md` through FY2026, added entities `groq.md` and `openai.md`, added concept `blackwell-and-rubin.md`, revised all 12 existing concept pages, updated index and appended 3 log entries.
- 2026-09-17 — Step 6 complete: consistency script reports every `wiki/**/*.md` (26) present in index and no broken relative links; every page has OKF frontmatter `type`. Also appended a `maintenance` log entry summarizing cross-filing reconciliation.
- 2026-09-17 — Lint pass (28 pages): fixed a factual contradiction in `entities/mellanox.md` (networking was NOT the fastest FY2025 layer); refreshed stale company facts in `entities/nvidia.md` (fiscal calendar, holders, HQ size, officers); created `concepts/networking.md` and `concepts/antitrust-scrutiny.md`; updated index and log. Re-run: 0 missing from index, 0 broken links, 0 orphans, all OKF `type`.

## Failures & fixes
| Step | Symptom | Root cause | Fix / alternative | Status |
|------|---------|------------|-------------------|--------|
| 2 | Index/log missing FY2023 | Prior session ended before bookkeeping step | Reconstructed index rows + log entry from existing pages | resolved |
| 2 | Edit targeted `log.md` using PROGRESS.md text | Confused the two files | Re-read `log.md`, applied correct edit | resolved |

## Decisions & alternatives
- 2026-09-17 — `PROGRESS.md` placed in `nvidia_wiki/` rather than the git repo root. Alternatives: outer `/home/ryanw07/ads_5984/PROGRESS.md`. Chosen because the repo holds several unrelated projects and this task's working root is `nvidia_wiki/`.
- 2026-09-17 — Raw filings render most financial tables as `[TABLE]` placeholders; figures drawn from narrative text and notes, and each page states this caveat. Used dedicated extraction subagents per filing (three in parallel) then spot-checked critical numbers against the raw before authoring.
- 2026-09-17 — Kept the two-segment structure as the organizing claim across filings, but flagged FY2026's narrowed segment *definitions* and recast geographic basis instead of silently replacing the older description.
