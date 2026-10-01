---
type: log
title: Wiki log
description: Append-only timeline of ingests, queries, and lints.
tags: [log]
updated: 2026-09-17
---

# Log

## [2026-09-17] ingest | NVIDIA 10-K FY2022

First ingest; wiki created. Read `raw/nvidia_10-K_FY2022.md` in full. Added source page, 4 entity pages (nvidia, jensen-huang, colette-kress, mellanox), 11 concept pages (accelerated-computing, reportable-segments, gaming, data-center, professional-visualization, automotive, cryptocurrency-exposure, supply-chain, export-controls, arm-acquisition, risk-factors), plus index and this log. Note: raw markdown renders most financial tables as `[TABLE]` placeholders; figures drawn from narrative text.

## [2026-09-17] ingest | NVIDIA 10-K FY2023

Read `raw/nvidia_10-K_FY2023.md` in full. Added source page and new concept [AI cloud services](concepts/ai-cloud-services.md); revised [NVIDIA](entities/nvidia.md), [Gaming](concepts/gaming.md), [Data Center](concepts/data-center.md), [Professional Visualization](concepts/professional-visualization.md), [Automotive](concepts/automotive.md), [Cryptocurrency exposure](concepts/cryptocurrency-exposure.md), [Reportable segments](concepts/reportable-segments.md), [Supply chain](concepts/supply-chain.md), [Export controls](concepts/export-controls.md), [Arm acquisition](concepts/arm-acquisition.md), [Risk factors](concepts/risk-factors.md), and [Mellanox](entities/mellanox.md). Figures from narrative text (tables are `[TABLE]` placeholders). Note: this ingest's content was authored by a prior session that ended before index/log were updated; bookkeeping completed now.

## [2026-09-17] ingest | NVIDIA 10-K FY2024

Read `raw/nvidia_10-K_FY2024.md` in full. Added source page; revised [NVIDIA](entities/nvidia.md) (financial history now covers FY2022–FY2026), [Jensen Huang](entities/jensen-huang.md), [Colette Kress](entities/colette-kress.md), and all concept pages. Key changes flagged in-page: FY2024 revenue **$60.92B (+126%)** vs FY2023 $26.97B; gross margin **72.7%** (from 56.9%); **first customer ≥10%** (Customer A, 13%); useful-life accounting change (+$135M op income); FY2023 net income now recoverable as **$4.368B** from FY2024 comparatives (the FY2023 page had marked it unrecoverable).

## [2026-09-17] ingest | NVIDIA 10-K FY2025

Read `raw/nvidia_10-K_FY2025.md` in full. Added source page and new concept [Blackwell and Rubin roadmap](concepts/blackwell-and-rubin.md); revised [NVIDIA](entities/nvidia.md), all market-platform pages, [Reportable segments](concepts/reportable-segments.md), [Supply chain](concepts/supply-chain.md), [Export controls](concepts/export-controls.md), [AI cloud services](concepts/ai-cloud-services.md), [Accelerated computing](concepts/accelerated-computing.md), [Risk factors](concepts/risk-factors.md). Key changes flagged: revenue **$130.50B (+114%)**; Data Center **$115.19B (+142%)**; gross margin **75.0%**; **ten-for-one stock split (June 2024)** restates all prior per-share data; **AI Diffusion IFR** (Jan 2025) new; three direct customers ≥10%.

## [2026-09-17] ingest | NVIDIA 10-K FY2026

Read `raw/nvidia_10-K_FY2026.md` in full. Added source page, entities [Groq](entities/groq.md) and [OpenAI](entities/openai.md); revised [NVIDIA](entities/nvidia.md), [Mellanox](entities/mellanox.md), [Reportable segments](concepts/reportable-segments.md), all market-platform pages, [Export controls](concepts/export-controls.md), [Supply chain](concepts/supply-chain.md), [Risk factors](concepts/risk-factors.md), [Cryptocurrency exposure](concepts/cryptocurrency-exposure.md), [AI cloud services](concepts/ai-cloud-services.md). Key changes flagged vs older filings: revenue **$215.94B (+65%)** but gross margin **71.1%** (from 75.0%, incl. **$4.5B H20 charge**); **segment definitions narrowed** (Graphics no longer includes infotainment/Omniverse; Compute & Networking adds AI software); **geographic revenue recast to customer-HQ basis** (31% outside US; prior basis 41%/48%); customer concentration **22% + 14%** with Customer A/B letters dropped; **supply commitments $95.2B**; **effectively foreclosed from China data center market**; **Groq IP license ($14.4B goodwill)**. Note: FY2026 market-platform dollar figures and segment revenue are not recoverable from the `[TABLE]` placeholders.

## [2026-09-17] maintenance | cross-filing consistency + PROGRESS.md

Created `PROGRESS.md` (prior session had been interrupted mid-FY2023) and added the session-state pointer to `AGENTS.md`. Backfilled FY2023 index/log entries. No contradictions left unresolved: FY2023 net income revised from "unrecoverable" to $4.368B (FY2024 comparative); per-share figures are split-adjusted from FY2025 onward and flagged as such; geographic-mix series is discontinuous at FY2026 (basis change) and flagged.

## [2026-09-17] lint | full wiki

Mechanical: 28 pages, all in `index.md`, **0 broken relative links**, **0 orphan pages**, all with OKF `type`. Content findings and fixes:
- **Contradiction fixed:** `entities/mellanox.md` called Data Center networking "the segment's fastest layer in FY2025," but FY2025 compute grew +162% vs networking +51%. Corrected (networking was the fastest layer in FY2026, +142% vs compute +59%).
- **Stale claims fixed in `entities/nvidia.md`:** fiscal-calendar note omitted FY2024–FY2026 (now added, incl. FY2027 53-week); registered holders updated ~313 (2022) → **~1,226 (Feb 2026)**; HQ space ~1.76M → **~3M sq ft**; officer list updated with FY2026 ages (all three still in office).
- **Missing concept pages created:** [Networking and interconnect](concepts/networking.md) (referenced in 16 files) and [Antitrust and competition scrutiny](concepts/antitrust-scrutiny.md) (referenced by 6). Both added to index and linked from related pages.
- No unresolved contradictions remain. Known data gaps (segment revenue/gross margin, FY2026 platform dollars) are artifacts of the `[TABLE]` placeholders and are labeled as such on each page.