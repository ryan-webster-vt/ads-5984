---
type: concept
title: Accelerated computing platform strategy
description: NVIDIA's core thesis — GPUs + CUDA + full-stack software addressing many markets from one architecture; scaled into data-center-scale AI infrastructure.
tags: [strategy, gpu, cuda, platform]
resource: raw/nvidia_10-K_FY2026.md
updated: 2026-09-17
---

# Accelerated computing platform strategy

NVIDIA's founding bet: parallel GPUs solve problems CPUs cannot, and a **full platform** (hardware + systems + software + algorithms + libraries + services) beats selling chips (10-K FY2022).

## Core claims (10-K FY2022)

- Invented the **GPU in 1999** (modern computer graphics); introduced the **CUDA programming model in 2006**, opening GPUs to general-purpose computing. Over **$29B cumulative R&D** since inception.
- While CPU advances no longer track Moore's Law, NVIDIA claims GPU performance improvements run **ahead of Moore's Law**.
- One programmable architecture, many software stacks: supports several multi-billion-dollar end markets ([Gaming](gaming.md), [Data Center](data-center.md), [Professional Visualization](professional-visualization.md), [Automotive](automotive.md)) with the same underlying technology — leveraged R&D.
- **Ecosystem scale**: ~3 million CUDA developers worldwide; 2,500+ GPU-accelerated applications including the top 15 HPC applications; ~10,000 startups in the Inception program; Deep Learning Institute training; partnerships with hundreds of universities.

## Compute portfolio (10-K FY2022)

- **GPUs** for parallel workloads (training + inference), available from every major server maker and cloud provider (AWS, Azure, Google Cloud, Alicloud, Baidu, IBM, Oracle, Tencent).
- **Systems**: DGX (AI supercomputer), HGX (hyperscale/supercomputing), EGX (enterprise/edge), AGX (autonomous machines).
- **DPUs**: BlueField (introduced FY2021) with DOCA software framework.
- **CPUs**: **Grace**, NVIDIA's first Arm-based data center CPU — unveiled FY2022, planned to ship early FY2024.
- **Software**: CUDA-X libraries, NGC GPU-cloud registry, standalone enterprise software ([NVIDIA AI Enterprise](data-center.md), vGPU, Base Command, Fleet Command) sold as perpetual license or subscription; [Omniverse](professional-visualization.md) subscription.
- **Scale proof points**: powers >70% (8 of top 10) of TOP500 supercomputers; 23 of top 25 systems on the Nov 2021 Green500; GPU servers ~40x more energy-efficient than CPU servers for AI workloads.
- Five stated strategies: advance the accelerated computing platform; extend AI leadership; extend graphics leadership; advance the leading AV platform; leverage IP via licenses.

## Scaling into AI infrastructure (10-K FY2024 → FY2026)

- **FY2024:** NVIDIA describes itself as "a full-stack computing infrastructure company with data-center-scale offerings." The data-center platform added **DPUs in FY2022 and CPUs in FY2024**; **Grace CPU and Grace Hopper** began shipping Q3 FY2024. CUDA reaches **4.7M developers**; >75% of TOP500 supercomputers; >500 RTX AI apps.
- **FY2025:** **Blackwell** launched as a full data-center-scale platform (GPUs, CPUs, DPUs, interconnects, switch chips, systems, adapters); new **annual product/architecture cadence**. GPU performance now sold on **cost per token** and energy efficiency. CUDA **5.9M developers**; 4,400+ applications; 38 of top 50 Green500.
- **FY2026:** **Blackwell Ultra/GB300** shipping, **Rubin** unveiled (2H FY2027, up to 10x cost-per-token reduction vs Blackwell); **NVLink Fusion** opens the fabric to custom CPUs/XPUs; physical-AI/robotics and models (Nemotron, Cosmos). CUDA ecosystem ~**6,000 applications**; >78% of TOP500. Cumulative R&D passes **$76.7B**.
- Through-line: the moat is the **software/developer ecosystem and full-stack integration**, not any single chip — the same argument from FY2022, now at data-center scale.

## Related

[Reportable segments](reportable-segments.md) · [Arm acquisition attempt](arm-acquisition.md) (would have added IP-licensing reach) · [NVIDIA](../entities/nvidia.md)