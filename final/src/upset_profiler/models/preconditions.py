"""Contracts for candidate preconditions and statistical validation results."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import (
    Citation,
    SeasonStr,
    StrictModel,
    ThresholdValue,
    UtcDatetime,
    VersionedModel,
)


class Comparison(StrEnum):
    """Supported comparison operators for precondition thresholds."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    IN = "in"
    NOT_IN = "not_in"


class Direction(StrEnum):
    """Expected direction of a precondition's effect on upset risk."""

    HIGHER_INCREASES_UPSET_RISK = "higher_increases_upset_risk"
    LOWER_INCREASES_UPSET_RISK = "lower_increases_upset_risk"
    NON_DIRECTIONAL = "non_directional"


class CandidatePrecondition(VersionedModel):
    """A hypothesis that must pass deterministic statistical validation."""

    precondition_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    feature: str = Field(min_length=1)
    comparison: Comparison
    threshold: ThresholdValue
    direction: Direction = Direction.HIGHER_INCREASES_UPSET_RISK
    tags: list[str] = Field(default_factory=list)
    rationale: str | None = None
    source: str = "seed"
    min_sample_size: int | None = Field(default=None, ge=2)
    created_at: UtcDatetime = Field(default_factory=utc_now)


class EffectSize(StrictModel):
    """Estimated effect size with a confidence interval."""

    metric: str = Field(min_length=1)
    value: float
    ci_lower: float
    ci_upper: float
    method: str = Field(min_length=1)
    treatment_sample_size: int = Field(ge=0)
    control_sample_size: int = Field(ge=0)

    @model_validator(mode="after")
    def _confidence_interval_is_ordered(self) -> Self:
        if self.ci_lower > self.ci_upper:
            raise ValueError("ci_lower must not exceed ci_upper")
        if not self.ci_lower <= self.value <= self.ci_upper:
            raise ValueError("effect size value must lie within its confidence interval")
        return self


class StatisticalTest(StrictModel):
    """Result of a hypothesis test for a candidate precondition."""

    test_name: str = Field(min_length=1)
    statistic: float
    p_value: float = Field(ge=0.0, le=1.0)
    alpha: float = Field(gt=0.0, lt=1.0)
    significant: bool
    multiple_testing_corrected: bool = False
    adjusted_p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = None

    @model_validator(mode="after")
    def _significance_matches_p_value(self) -> Self:
        effective_p = (
            self.adjusted_p_value if self.adjusted_p_value is not None else self.p_value
        )
        if self.significant != (effective_p < self.alpha):
            raise ValueError("significant must equal (effective p-value < alpha)")
        return self


class OutOfSampleResult(StrictModel):
    """Out-of-sample predictive check for a candidate precondition."""

    train_seasons: list[SeasonStr] = Field(min_length=1)
    validation_season: SeasonStr
    metric_name: str = Field(min_length=1)
    value: float
    baseline_value: float
    passed: bool
    notes: str | None = None

    @model_validator(mode="after")
    def _validation_season_is_held_out(self) -> Self:
        if self.validation_season in self.train_seasons:
            raise ValueError("validation_season must not appear in train_seasons")
        return self


class PreconditionDecision(VersionedModel):
    """The Extractor's accept/reject decision with statistical justification."""

    precondition_id: str = Field(min_length=1)
    accepted: bool
    justification: str = Field(min_length=1)
    effect_size: EffectSize | None = None
    statistical_test: StatisticalTest | None = None
    out_of_sample: OutOfSampleResult | None = None
    citations: list[Citation] = Field(default_factory=list)
    decided_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _accepted_decisions_are_complete(self) -> Self:
        if not self.accepted:
            return self
        if (
            self.effect_size is None
            or self.statistical_test is None
            or self.out_of_sample is None
        ):
            raise ValueError(
                "accepted decisions require effect size, test, and out-of-sample result"
            )
        if not self.statistical_test.significant:
            raise ValueError("accepted decisions require a statistically significant test")
        if not self.out_of_sample.passed:
            raise ValueError("accepted decisions require a passing out-of-sample check")
        if not self.citations:
            raise ValueError("accepted decisions require at least one citation")
        return self


class ValidatedPrecondition(VersionedModel):
    """A precondition accepted by the Extractor and stored for monitoring."""

    precondition_id: str = Field(min_length=1)
    candidate: CandidatePrecondition
    effect_size: EffectSize
    statistical_test: StatisticalTest
    out_of_sample: OutOfSampleResult
    citations: list[Citation] = Field(min_length=1)
    justification: str = Field(min_length=1)
    validated_at: UtcDatetime = Field(default_factory=utc_now)
    validation_version: str = "1"

    @model_validator(mode="after")
    def _candidate_matches(self) -> Self:
        if self.candidate.precondition_id != self.precondition_id:
            raise ValueError("candidate.precondition_id must match precondition_id")
        if not self.statistical_test.significant:
            raise ValueError("validated precondition requires a significant statistical test")
        if not self.out_of_sample.passed:
            raise ValueError("validated precondition requires a passing out-of-sample check")
        return self


class ExtractionResult(VersionedModel):
    """Aggregate output of one precondition extraction run."""

    run_id: UUID
    decisions: list[PreconditionDecision] = Field(default_factory=list)
    accepted: list[ValidatedPrecondition] = Field(default_factory=list)
    rejected: list[PreconditionDecision] = Field(default_factory=list)
    completed_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _decision_sets_are_consistent(self) -> Self:
        accepted_decision_ids = {
            decision.precondition_id for decision in self.decisions if decision.accepted
        }
        rejected_decision_ids = {
            decision.precondition_id for decision in self.decisions if not decision.accepted
        }
        if accepted_decision_ids != {item.precondition_id for item in self.accepted}:
            raise ValueError("accepted list must match accepted decisions")
        if rejected_decision_ids != {item.precondition_id for item in self.rejected}:
            raise ValueError("rejected list must match rejected decisions")
        decision_ids = [decision.precondition_id for decision in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("extraction result contains duplicate precondition decisions")
        return self
