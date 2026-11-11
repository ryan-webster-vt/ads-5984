"""Upset-Scorer: score flagged games and assign stable ranks."""

from __future__ import annotations

from typing import Protocol

from upset_profiler.agents.base import BaseAgent
from upset_profiler.models.monitoring import GameFlag
from upset_profiler.models.progress import ProgressStatus
from upset_profiler.models.scoring import ScoringRequest, UpsetRanking, UpsetScore
from upset_profiler.orchestration.progress import ProgressReporter


class DecisionModel(Protocol):
    """Decision-model adapter that returns a raw upset-risk score."""

    model_name: str

    def score(self, flag: GameFlag) -> float:
        """Return a deterministic raw score for one flag."""
        ...


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


class UpsetScorer(BaseAgent):
    """Score every flag, normalize scores, and rank by score then game id."""

    name = "upset-scorer"

    def __init__(
        self,
        *,
        decision_model: DecisionModel,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        self._decision_model = decision_model

    @property
    def model_name(self) -> str:
        """Return the decision model name used for scoring."""
        return self._decision_model.model_name

    def score(self, request: ScoringRequest) -> UpsetRanking:
        """Return a stable ranking for every flagged game."""
        self._report(
            f"scoring {len(request.flags)} flagged games",
            status=ProgressStatus.STARTED,
            completed=0,
            total=len(request.flags),
        )
        scored: list[tuple[GameFlag, float, float]] = []
        for flag in request.flags:
            raw_score = float(self._decision_model.score(flag))
            scored.append((flag, round(raw_score, 6), _clamp_score(raw_score)))

        scored.sort(key=lambda item: (-item[2], item[0].game_id))
        scores = [
            UpsetScore(
                game_id=flag.game_id,
                score=score,
                rank=rank,
                model_name=request.model_name,
                rationale=(
                    f"{flag.rationale} Upset risk score {score:.3f} from "
                    f"{request.model_name}."
                ),
                citations=flag.citations,
                calibrated=False,
                raw_score=raw_score,
            )
            for rank, (flag, raw_score, score) in enumerate(scored, start=1)
        ]
        self._report(
            f"scoring complete: {len(scores)} ranked games",
            status=ProgressStatus.COMPLETED,
            completed=len(request.flags),
            total=len(request.flags),
        )
        return UpsetRanking(
            run_id=request.run_id,
            model_name=request.model_name,
            scores=scores,
        )
