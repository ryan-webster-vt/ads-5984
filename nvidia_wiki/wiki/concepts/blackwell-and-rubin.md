---
type: concept
title: Blackwell and Rubin roadmap
description: NVIDIA's data-center architecture cadence from Hopper to Blackwell, Blackwell Ultra/GB300, and Rubin — a new annual rhythm.
tags: [gpu, architecture, blackwell, rubin, roadmap]
resource: raw/nvidia_10-K_FY2026.md
updated: 2026-09-17
---

# Blackwell and Rubin roadmap

NVIDIA's data-center GPU platform moved to a **new annual architecture cadence** during this wiki's coverage, with each generation sold as a full data-center-scale system rather than a chip (10-K FY2025, FY2026).

## Generations in the filings

- **Ampere → Ada Lovelace (FY2022–FY2024):** gaming/workstation architectures; [Data Center](data-center.md) was led by **Ampere**, then **Hopper**.
- **Hopper / H100 (FY2023–FY2025):** announced in the FY2023 10-K; drove the FY2024 +217% and much of the FY2025 Data Center growth for LLM training, recommendation engines and generative AI. **Grace Hopper Superchips** combined Grace CPU + Hopper GPU (shipped Q3 FY2024).
- **Blackwell (FY2025):** launched as "a full set of data center scale infrastructure … GPUs, CPUs, DPUs, interconnects, switch chips and systems, and networking adapters." Production systems (GB200 NVL72/NVL36, B200) began shipping **Q4 FY2025**. Q2 FY2025 gross margin was hit by inventory provisions for **low-yielding Blackwell material**.
- **Blackwell Ultra / GB300 (FY2026):** "optimized for agentic, reasoning, and physical AI"; production units shipped **Q2 FY2026**. Blackwell architectures were the **majority of Data Center revenue** in FY2026.
- **Rubin (FY2026):** "unveiled" in FY2026, expected to **commence production shipments in 2H FY2027**; built for agentic AI and reasoning, with **up to a 10x reduction in cost per token vs Blackwell**.
- **NVLink Fusion (FY2026):** lets hyperscalers and custom ASIC designers integrate custom CPUs and XPUs with NVIDIA's fabric.

## Why it matters

- Each transition carries **mix and margin risk**: the FY2026 gross-margin decline to 71.1% is attributed partly to moving from Hopper HGX systems to Blackwell full-scale data-center solutions (10-K FY2026), and Blackwell product transitions were flagged as a demand-forecasting/supply-mix risk.
- The cadence is central to the [accelerated computing](accelerated-computing.md) thesis: annual full-stack releases make competing on a single chip harder.
- [Export controls](export-controls.md) explicitly name generations (A100/H100/H200/B100/B200/GB200/GB300, plus H20/H200 licensing), so each new architecture is also a policy event.

## Related

[Data Center](data-center.md) · [Accelerated computing](accelerated-computing.md) · [Networking](networking.md) · [Reportable segments](reportable-segments.md) · [Export controls](export-controls.md) · [Groq](../entities/groq.md) · [NVIDIA](../entities/nvidia.md)
