"""Tests for deterministic Monitor rule evaluation."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.factories import FIXED_DATE, make_daily_game
from upset_profiler.agents.monitor_worker import MonitorWorker, evaluate_comparison
from upset_profiler.fixtures import load_json, load_model
from upset_profiler.integrations.fakes import FixtureContextProvider, FixtureSlateProvider
from upset_profiler.models.games import DailyGame, DailySlate
from upset_profiler.models.monitoring import GameContext, MonitorAssignment
from upset_profiler.models.preconditions import Comparison, ValidatedPrecondition


def _preconditions() -> list[ValidatedPrecondition]:
    return [
        ValidatedPrecondition.model_validate(item)
        for item in load_json("historical", "validated_preconditions.json")
    ]


class _StaticContextProvider:
    def __init__(self, context: GameContext) -> None:
        self._context = context

    def get_context(self, game: DailyGame) -> GameContext:
        return self._context


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observed", "comparison", "threshold", "expected"),
    [
        (3, Comparison.GREATER_THAN_OR_EQUAL, 2, True),
        (1, Comparison.GREATER_THAN_OR_EQUAL, 2, False),
        (True, Comparison.EQUALS, True, True),
        (False, Comparison.EQUALS, True, False),
        (2.5, Comparison.LESS_THAN, 3, True),
        ("a", Comparison.NOT_EQUALS, "b", True),
    ],
)
def test_evaluate_comparison(
    observed: bool | int | float | str,
    comparison: Comparison,
    threshold: bool | int | float | str,
    expected: bool,
) -> None:
    assert evaluate_comparison(observed, comparison, threshold) is expected


@pytest.mark.unit
def test_evaluate_comparison_rejects_non_numeric_ordering() -> None:
    with pytest.raises(TypeError, match="numeric"):
        evaluate_comparison("high", Comparison.GREATER_THAN, 2)


@pytest.mark.unit
def test_evaluate_comparison_rejects_in_operator() -> None:
    with pytest.raises(ValueError, match="in/not_in"):
        evaluate_comparison("a", Comparison.IN, "a,b")


@pytest.mark.unit
def test_worker_flags_matching_game_with_citations() -> None:
    slate = FixtureSlateProvider().get_slate(FIXED_DATE)
    game = slate.games[0]
    worker = MonitorWorker(
        worker_id="monitor-worker-1",
        context_provider=FixtureContextProvider(),
    )
    result = worker.evaluate(
        MonitorAssignment(
            run_id=uuid4(),
            worker_id="monitor-worker-1",
            games=[game],
            preconditions=_preconditions(),
        )
    )
    assert result.evaluated_game_ids == [game.game_id]
    assert len(result.flags) == 1
    flag = result.flags[0]
    assert flag.game_id == game.game_id
    assert {signal.precondition_id for signal in flag.signals if signal.matched} == {
        "rest_disadvantage",
        "back_to_back",
        "travel_distance",
    }
    assert flag.citations


@pytest.mark.unit
def test_worker_does_not_flag_non_matching_game() -> None:
    slate = FixtureSlateProvider().get_slate(FIXED_DATE)
    game = slate.games[1]
    worker = MonitorWorker(
        worker_id="monitor-worker-2",
        context_provider=FixtureContextProvider(),
    )
    result = worker.evaluate(
        MonitorAssignment(
            run_id=uuid4(),
            worker_id="monitor-worker-2",
            games=[game],
            preconditions=_preconditions(),
        )
    )
    assert result.evaluated_game_ids == [game.game_id]
    assert result.flags == []


@pytest.mark.unit
def test_worker_records_context_failure_and_keeps_going() -> None:
    slate = FixtureSlateProvider().get_slate(FIXED_DATE)
    unknown = make_daily_game("999999999")
    worker = MonitorWorker(
        worker_id="monitor-worker-1",
        context_provider=FixtureContextProvider(),
    )
    result = worker.evaluate(
        MonitorAssignment(
            run_id=uuid4(),
            worker_id="monitor-worker-1",
            games=[unknown, slate.games[0]],
            preconditions=_preconditions(),
        )
    )
    assert result.evaluated_game_ids == [slate.games[0].game_id]
    assert len(result.flags) == 1
    assert len(result.failures) == 1
    assert result.failures[0].context["game_id"] == unknown.game_id


@pytest.mark.unit
def test_worker_reports_missing_features_as_unmatched() -> None:
    game = make_daily_game("401000001")
    context = GameContext(game=game, features={})
    worker = MonitorWorker(
        worker_id="monitor-worker-1",
        context_provider=_StaticContextProvider(context),
    )
    result = worker.evaluate(
        MonitorAssignment(
            run_id=uuid4(),
            worker_id="monitor-worker-1",
            games=[game],
            preconditions=_preconditions(),
        )
    )
    assert result.flags == []


@pytest.mark.unit
def test_daily_slate_fixture_is_contract_valid() -> None:
    assert isinstance(load_model(DailySlate, "slates", "2025-01-15.json"), DailySlate)
