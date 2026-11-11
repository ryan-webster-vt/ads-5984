"""Deterministic JSON Schema export for all public contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from upset_profiler.models import (
    AgentError,
    BacktestPrediction,
    BacktestReport,
    CachedSearchResponse,
    CalibrationBin,
    CandidatePrecondition,
    Citation,
    Claim,
    ContextResearchResult,
    DailyGame,
    DailySlate,
    DocumentChunk,
    EffectSize,
    EvaluationMetrics,
    ExtractionResult,
    GameContext,
    GameFlag,
    GameOdds,
    HistoricalGame,
    HistoricalStatsResult,
    IngestionResult,
    MonitorAggregate,
    MonitorAssignment,
    MonitorRequest,
    MonitorWorkerResult,
    OutOfSampleResult,
    PlayerStatistics,
    PreconditionDecision,
    ProgressEvent,
    Provenance,
    RerankedEvidence,
    ResearchAggregate,
    ResearchBatch,
    ResearchRecord,
    RetrievedEvidence,
    RunContext,
    ScoringRequest,
    SearchHitRecord,
    SignalMatch,
    SourceDocument,
    StatisticalTest,
    TeamRef,
    TeamStatistics,
    UpsetRanking,
    UpsetScore,
    ValidatedPrecondition,
    ValidationIssue,
)

MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        AgentError,
        BacktestPrediction,
        BacktestReport,
        CalibrationBin,
        CachedSearchResponse,
        CandidatePrecondition,
        Citation,
        Claim,
        ContextResearchResult,
        DailyGame,
        DailySlate,
        DocumentChunk,
        EffectSize,
        EvaluationMetrics,
        ExtractionResult,
        GameContext,
        GameFlag,
        GameOdds,
        HistoricalGame,
        HistoricalStatsResult,
        IngestionResult,
        MonitorAggregate,
        MonitorAssignment,
        MonitorRequest,
        MonitorWorkerResult,
        OutOfSampleResult,
        PlayerStatistics,
        PreconditionDecision,
        ProgressEvent,
        Provenance,
        RerankedEvidence,
        ResearchAggregate,
        ResearchBatch,
        ResearchRecord,
        RetrievedEvidence,
        RunContext,
        ScoringRequest,
        SearchHitRecord,
        SignalMatch,
        SourceDocument,
        StatisticalTest,
        TeamRef,
        TeamStatistics,
        UpsetRanking,
        UpsetScore,
        ValidatedPrecondition,
        ValidationIssue,
    )
}


def schema_filename(model_name: str) -> str:
    """Return the checked-in schema filename for a model."""
    return f"{model_name}.schema.json"


def build_schemas() -> dict[str, str]:
    """Render every contract schema as deterministic JSON text."""
    rendered: dict[str, str] = {}
    for name in sorted(MODEL_REGISTRY):
        schema: dict[str, Any] = MODEL_REGISTRY[name].model_json_schema()
        rendered[schema_filename(name)] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered


def write_schemas(output_dir: Path) -> list[Path]:
    """Write all contract schemas to ``output_dir`` and return written paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in build_schemas().items():
        path = output_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check_schemas(output_dir: Path) -> list[str]:
    """Return human-readable differences between generated and checked-in schemas."""
    differences: list[str] = []
    expected = build_schemas()
    for filename, text in sorted(expected.items()):
        path = output_dir / filename
        if not path.exists():
            differences.append(f"missing schema: {filename}")
            continue
        if path.read_text(encoding="utf-8") != text:
            differences.append(f"outdated schema: {filename}")
    if output_dir.exists():
        for path in sorted(output_dir.glob("*.schema.json")):
            if path.name not in expected:
                differences.append(f"stale schema: {path.name}")
    return differences
