"""Contracts for daily monitoring requests, worker results, and game flags."""

from __future__ import annotations

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import (
    AgentError,
    Citation,
    StrictModel,
    ThresholdValue,
    UtcDatetime,
    VersionedModel,
)
from upset_profiler.models.evidence import RetrievedEvidence
from upset_profiler.models.games import DailyGame, TeamStatistics
from upset_profiler.models.preconditions import Comparison, ValidatedPrecondition
from upset_profiler.models.research import SourceDocument


class MonitorRequest(VersionedModel):
    """UI request to evaluate selected games for one day."""

    run_id: UUID
    slate_date: date
    games: list[DailyGame] = Field(min_length=1)
    worker_count: int = Field(default=2, ge=1)
    requested_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _games_are_unique_and_match_slate(self) -> Self:
        game_ids = [game.game_id for game in self.games]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("monitor request contains duplicate game_id values")
        for game in self.games:
            if game.slate_date != self.slate_date:
                raise ValueError("every game must match the request slate_date")
        return self


class MonitorAssignment(VersionedModel):
    """A batch of games and validated signals assigned to one Monitor worker."""

    run_id: UUID
    worker_id: str = Field(min_length=1)
    games: list[DailyGame] = Field(min_length=1)
    preconditions: list[ValidatedPrecondition] = Field(default_factory=list)
    assigned_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _games_are_unique(self) -> Self:
        game_ids = [game.game_id for game in self.games]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("monitor assignment contains duplicate game_id values")
        return self


class GameContext(VersionedModel):
    """Current matchup data and cited pregame evidence for one game.

    ``features`` carries deterministic pregame feature values keyed by the
    feature names used in validated preconditions. It is the typed input to
    deterministic Monitor rule evaluation; it is intentionally separate from
    provider-specific statistics and from retrieved documents.
    """

    game: DailyGame
    team_statistics: list[TeamStatistics] = Field(default_factory=list)
    features: dict[str, ThresholdValue] = Field(default_factory=dict)
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    documents: list[SourceDocument] = Field(default_factory=list)
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _documents_match_game(self) -> Self:
        for document in self.documents:
            if document.game_id != self.game.game_id:
                raise ValueError("every document must match game.game_id")
        return self


class SignalMatch(StrictModel):
    """Comparison of one validated precondition against a game's evidence."""

    precondition_id: str = Field(min_length=1)
    matched: bool
    comparison: Comparison
    threshold: ThresholdValue
    observed_value: ThresholdValue | None = None
    explanation: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _matched_signals_cite_evidence(self) -> Self:
        if self.matched and not self.citations:
            raise ValueError("matched signals require at least one citation")
        return self


class GameFlag(VersionedModel):
    """A game flagged as an upset candidate with cited rationale."""

    game_id: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)
    signals: list[SignalMatch] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    flagged_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _flag_contains_a_matched_signal(self) -> Self:
        if not any(signal.matched for signal in self.signals):
            raise ValueError("a game flag requires at least one matched signal")
        return self


class MonitorWorkerResult(VersionedModel):
    """Flags and failures reported by one Monitor worker."""

    run_id: UUID
    worker_id: str = Field(min_length=1)
    flags: list[GameFlag] = Field(default_factory=list)
    evaluated_game_ids: list[str] = Field(default_factory=list)
    failures: list[AgentError] = Field(default_factory=list)
    completed_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _worker_results_are_consistent(self) -> Self:
        flag_ids = [flag.game_id for flag in self.flags]
        if len(flag_ids) != len(set(flag_ids)):
            raise ValueError("worker result contains duplicate flags for a game")
        if not set(flag_ids).issubset(set(self.evaluated_game_ids)):
            raise ValueError("every flagged game must also be listed as evaluated")
        for flag in self.flags:
            if flag.worker_id != self.worker_id:
                raise ValueError("flag.worker_id must match result worker_id")
        return self


class MonitorAggregate(VersionedModel):
    """Aggregated, deduplicated monitoring output for the whole slate."""

    run_id: UUID
    flags: list[GameFlag] = Field(default_factory=list)
    evaluated_game_ids: list[str] = Field(default_factory=list)
    failures: list[AgentError] = Field(default_factory=list)
    generated_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _aggregate_is_consistent(self) -> Self:
        flag_ids = [flag.game_id for flag in self.flags]
        if len(flag_ids) != len(set(flag_ids)):
            raise ValueError("monitor aggregate contains duplicate flags for a game")
        if not set(flag_ids).issubset(set(self.evaluated_game_ids)):
            raise ValueError("every flagged game must also be listed as evaluated")
        return self

    @property
    def has_flags(self) -> bool:
        """Return whether any game was flagged."""
        return bool(self.flags)
