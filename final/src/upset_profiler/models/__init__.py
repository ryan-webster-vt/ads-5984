"""Strict Pydantic contracts shared across agent boundaries.

Importing this package is side-effect free. Models perform validation only and
never access the network or storage.
"""

from __future__ import annotations

from upset_profiler.context import RunContext
from upset_profiler.models.common import (
    SCHEMA_VERSION,
    AgentError,
    Citation,
    Provenance,
    SeasonStr,
    StrictModel,
    TeamRef,
    ThresholdValue,
    UtcDatetime,
    ValidationIssue,
    VersionedModel,
)
from upset_profiler.models.evidence import (
    Claim,
    DocumentChunk,
    RerankedEvidence,
    RetrievedEvidence,
)
from upset_profiler.models.games import (
    DailyGame,
    DailySlate,
    GameOdds,
    HistoricalGame,
    PlayerStatistics,
    TeamStatistics,
)
from upset_profiler.models.monitoring import (
    GameContext,
    GameFlag,
    MonitorAggregate,
    MonitorAssignment,
    MonitorRequest,
    MonitorWorkerResult,
    SignalMatch,
)
from upset_profiler.models.preconditions import (
    CandidatePrecondition,
    Comparison,
    Direction,
    EffectSize,
    ExtractionResult,
    OutOfSampleResult,
    PreconditionDecision,
    StatisticalTest,
    ValidatedPrecondition,
)
from upset_profiler.models.progress import ProgressEvent, ProgressStage, ProgressStatus
from upset_profiler.models.research import (
    ContextResearchResult,
    HistoricalStatsResult,
    ResearchAggregate,
    ResearchBatch,
    ResearchRecord,
    SourceDocument,
)
from upset_profiler.models.scoring import ScoringRequest, UpsetRanking, UpsetScore
from upset_profiler.models.validation import (
    BacktestPrediction,
    BacktestReport,
    CalibrationBin,
    EvaluationMetrics,
)

__all__ = [
    "SCHEMA_VERSION",
    "AgentError",
    "BacktestPrediction",
    "BacktestReport",
    "CalibrationBin",
    "CandidatePrecondition",
    "Citation",
    "Claim",
    "Comparison",
    "ContextResearchResult",
    "DailyGame",
    "DailySlate",
    "Direction",
    "DocumentChunk",
    "EffectSize",
    "EvaluationMetrics",
    "ExtractionResult",
    "GameContext",
    "GameFlag",
    "GameOdds",
    "HistoricalGame",
    "HistoricalStatsResult",
    "MonitorAggregate",
    "MonitorAssignment",
    "MonitorRequest",
    "MonitorWorkerResult",
    "OutOfSampleResult",
    "PlayerStatistics",
    "PreconditionDecision",
    "ProgressEvent",
    "ProgressStage",
    "ProgressStatus",
    "Provenance",
    "RerankedEvidence",
    "ResearchAggregate",
    "ResearchBatch",
    "ResearchRecord",
    "RetrievedEvidence",
    "RunContext",
    "ScoringRequest",
    "SeasonStr",
    "SignalMatch",
    "SourceDocument",
    "StatisticalTest",
    "StrictModel",
    "TeamRef",
    "TeamStatistics",
    "ThresholdValue",
    "UpsetRanking",
    "UpsetScore",
    "UtcDatetime",
    "ValidatedPrecondition",
    "ValidationIssue",
    "VersionedModel",
]
