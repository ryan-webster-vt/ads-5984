"""Contracts for offline backtesting, calibration, and evaluation reports."""

from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import (
    SeasonStr,
    StrictModel,
    UtcDatetime,
    VersionedModel,
)


class BacktestPrediction(VersionedModel):
    """One leakage-safe pregame prediction used in a backtest."""

    run_id: UUID
    game_id: str = Field(min_length=1)
    season: SeasonStr
    tipoff_at: UtcDatetime
    predicted_probability: float = Field(ge=0.0, le=1.0)
    actual_upset: bool
    model_name: str = Field(min_length=1)
    features: dict[str, float] = Field(default_factory=dict)
    predicted_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _prediction_precedes_tipoff(self) -> Self:
        if self.predicted_at > self.tipoff_at:
            raise ValueError("backtest predictions must be made before tip-off")
        return self


class CalibrationBin(StrictModel):
    """One bin of a predicted-probability reliability diagram."""

    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)
    count: int = Field(ge=0)
    mean_predicted: float = Field(ge=0.0, le=1.0)
    observed_upset_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _bin_bounds_are_ordered(self) -> Self:
        if self.lower >= self.upper:
            raise ValueError("calibration bin lower bound must be less than upper bound")
        if not self.lower <= self.mean_predicted <= self.upper:
            raise ValueError("mean_predicted must lie within the bin bounds")
        return self


class EvaluationMetrics(StrictModel):
    """Aggregate classification metrics for one evaluated season split."""

    log_loss: float = Field(ge=0.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    sample_size: int = Field(ge=1)
    positive_rate: float = Field(ge=0.0, le=1.0)
    baseline_log_loss: float | None = Field(default=None, ge=0.0)
    baseline_brier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration_error: float | None = Field(default=None, ge=0.0)


class BacktestReport(VersionedModel):
    """Reproducible backtest report with train/validation/test season splits."""

    run_id: UUID
    model_name: str = Field(min_length=1)
    train_seasons: list[SeasonStr] = Field(min_length=1)
    validation_season: SeasonStr
    test_season: SeasonStr
    validation_metrics: EvaluationMetrics
    test_metrics: EvaluationMetrics
    calibration_bins: list[CalibrationBin] = Field(default_factory=list)
    generated_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _splits_are_disjoint(self) -> Self:
        all_seasons = [*self.train_seasons, self.validation_season, self.test_season]
        if len(all_seasons) != len(set(all_seasons)):
            raise ValueError("train, validation, and test seasons must be disjoint")
        return self
