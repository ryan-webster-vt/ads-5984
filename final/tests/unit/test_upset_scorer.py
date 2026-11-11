"""Tests for deterministic scoring and stable ranking."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.factories import make_citation, make_flag, make_signal_match
from upset_profiler.agents.upset_scorer import UpsetScorer
from upset_profiler.integrations.fakes import FixtureDecisionModel
from upset_profiler.models.monitoring import GameFlag
from upset_profiler.models.scoring import ScoringRequest


def _flag(game_id: str, matches: int) -> GameFlag:
    return GameFlag(
        game_id=game_id,
        worker_id="monitor-worker-1",
        signals=[make_signal_match() for _ in range(matches)],
        rationale=f"{game_id} matches validated preconditions.",
        citations=[make_citation()],
    )


@pytest.mark.unit
def test_scorer_ranks_by_score_then_game_id() -> None:
    scorer = UpsetScorer(decision_model=FixtureDecisionModel())
    ranking = scorer.score(
        ScoringRequest(
            run_id=uuid4(),
            flags=[_flag("b", 1), _flag("a", 1), _flag("c", 3)],
            model_name="d3-mini",
        )
    )
    assert [score.game_id for score in ranking.scores] == ["c", "a", "b"]
    assert [score.rank for score in ranking.scores] == [1, 2, 3]
    assert all(0.0 <= score.score <= 1.0 for score in ranking.scores)


@pytest.mark.unit
def test_scorer_is_reproducible_for_same_input() -> None:
    scorer = UpsetScorer(decision_model=FixtureDecisionModel())
    request = ScoringRequest(
        run_id=uuid4(),
        flags=[_flag("b", 1), _flag("a", 2)],
        model_name="d3-mini",
    )
    first = scorer.score(request)
    second = scorer.score(request)
    stable_first = [
        (score.game_id, score.score, score.rank, score.rationale, score.citations)
        for score in first.scores
    ]
    stable_second = [
        (score.game_id, score.score, score.rank, score.rationale, score.citations)
        for score in second.scores
    ]
    assert stable_first == stable_second


@pytest.mark.unit
def test_scorer_preserves_citations_and_model_name() -> None:
    scorer = UpsetScorer(decision_model=FixtureDecisionModel())
    flag = make_flag("401000001")
    ranking = scorer.score(
        ScoringRequest(run_id=uuid4(), flags=[flag], model_name="d3-mini")
    )
    assert ranking.model_name == "d3-mini"
    assert ranking.scores[0].citations == flag.citations
