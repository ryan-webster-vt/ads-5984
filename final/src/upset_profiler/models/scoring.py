"""Contracts for upset scoring and ranked alerts."""

from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import Citation, UtcDatetime, VersionedModel
from upset_profiler.models.monitoring import GameFlag


class ScoringRequest(VersionedModel):
    """Flagged games submitted to the Upset-Scorer."""

    run_id: UUID
    flags: list[GameFlag] = Field(min_length=1)
    model_name: str = Field(min_length=1)
    requested_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _flags_are_unique(self) -> Self:
        game_ids = [flag.game_id for flag in self.flags]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("scoring request contains duplicate flags for a game")
        return self


class UpsetScore(VersionedModel):
    """An upset-risk score for one flagged game."""

    game_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    model_name: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    calibrated: bool = False
    raw_score: float | None = None
    scored_at: UtcDatetime = Field(default_factory=utc_now)


class UpsetRanking(VersionedModel):
    """Final ranked upset alerts returned to the UI."""

    run_id: UUID
    model_name: str = Field(min_length=1)
    scores: list[UpsetScore] = Field(default_factory=list)
    generated_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _ranking_is_complete_and_stable(self) -> Self:
        if not self.scores:
            return self
        game_ids = [score.game_id for score in self.scores]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("ranking contains duplicate game_id values")
        ranks = sorted(score.rank for score in self.scores)
        if ranks != list(range(1, len(self.scores) + 1)):
            raise ValueError("ranks must be unique and consecutive starting at 1")
        if any(score.model_name != self.model_name for score in self.scores):
            raise ValueError("every score model_name must match the ranking model_name")
        return self
