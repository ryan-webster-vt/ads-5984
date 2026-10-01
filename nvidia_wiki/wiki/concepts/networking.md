---
type: concept
title: Networking and interconnect
description: NVIDIA's networking business — InfiniBand, Ethernet, Spectrum-X, NVLink and BlueField DPUs, rooted in the Mellanox acquisition.
tags: [networking, interconnect, infiniband, ethernet, mellanox]
resource: raw/nvidia_10-K_FY2026.md
updated: 2026-09-17
---

# Networking and interconnect

Networking is reported inside the [Compute & Networking](reportable-segments.md) segment and grew from a post-[Mellanox](../entities/mellanox.md) add-on into a multi-billion-dollar layer of the [Data Center](data-center.md) platform as AI clusters scaled. It is frequently mentioned across this wiki but had no page before this lint.

## Products and platforms

- **InfiniBand** — high-performance interconnect from the Mellanox acquisition; **Quantum-2** 400Gbps platform began shipping December 2022 (10-K FY2023).
- **Ethernet** — **Spectrum-4** end-to-end 400Gbps Ethernet announced FY2023; **Spectrum-X** "accelerated networking platform for AI" announced FY2024; "Ethernet for AI" becomes a named growth driver (10-K FY2024, FY2025).
- **NVLink / NVLink compute fabric** — GPU-to-GPU fabric; **GB200/GB300 NVLink** drove FY2026 networking growth, and **NVLink Fusion** (FY2026) lets hyperscalers and custom ASIC designers integrate custom CPUs/XPUs (see [Blackwell and Rubin](blackwell-and-rubin.md)).
- **BlueField DPUs** with the **DOCA** software framework — data-processing units added to the data-center platform in FY2022 (10-K FY2022).
- **Adaptive routing / switch systems** built by contract manufacturers; cables from Fabrinet ([supply chain](supply-chain.md)).

## Revenue trajectory (Data Center networking)

| Fiscal year | Networking revenue | YoY | Source |
|---|---|---|---|
| FY2023 | $3.688B | — | 10-K FY2025 comparative |
| FY2024 | $8.575B | +133% | 10-K FY2024/FY2025 |
| FY2025 | $12.990B | +51% | 10-K FY2025 |
| FY2026 | not recoverable | +142% | 10-K FY2026 narrative |

- FY2024: networking +133%, described alongside compute +244% (10-K FY2024).
- FY2025: networking +51% vs compute +162% — networking grew, but **compute was the faster layer that year**; Ethernet for AI (including Spectrum-X) was the networking driver (10-K FY2025).
- FY2026: networking **+142%** vs compute +59% — networking was the **fastest-growing Data Center layer**, on NVLink for GB200/GB300 plus Ethernet and InfiniBand (10-K FY2026).

## Risks

- Networking customers (Arista, Cisco, Juniper, Broadcom, Marvell, etc.) may also be competitors; some have greater resources (10-K FY2022 [risk factors](risk-factors.md)).
- Networking equipment embedded in restricted systems is caught by [export controls](export-controls.md) on DGX/HGX/MGX systems.
- Mellanox commitments are the subject of China's Sept 2025 antitrust preliminary finding ([Mellanox](../entities/mellanox.md), [antitrust scrutiny](antitrust-scrutiny.md)).

## Related

[Data Center](data-center.md) · [Blackwell and Rubin](blackwell-and-rubin.md) · [Supply chain](supply-chain.md) · [Mellanox](../entities/mellanox.md) · [NVIDIA](../entities/nvidia.md)
