"""Upset scoring use case over Monitor output."""

from __future__ import annotations

from upset_profiler.agents.upset_scorer import UpsetScorer
from upset_profiler.models.monitoring import MonitorAggregate
from upset_profiler.models.scoring import ScoringRequest, UpsetRanking


class ScoringService:
    """Turn monitor flags into a ranked upset list."""

    def __init__(self, scorer: UpsetScorer) -> None:
        self._scorer = scorer

    def rank(self, aggregate: MonitorAggregate) -> UpsetRanking:
        """Score and rank flagged games, returning an empty ranking if none."""
        if not aggregate.flags:
            return UpsetRanking(
                run_id=aggregate.run_id,
                model_name=self._scorer.model_name,
                scores=[],
            )
        return self._scorer.score(
            ScoringRequest(
                run_id=aggregate.run_id,
                flags=aggregate.flags,
                model_name=self._scorer.model_name,
            )
        )
