"""Integration tests for the fixture-backed daily monitoring pipeline."""

from __future__ import annotations

from datetime import date

import pytest

from upset_profiler.agents.upset_scorer import UpsetScorer
from upset_profiler.context import RunContext
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.fakes import (
    FixtureContextProvider,
    FixtureDecisionModel,
    FixtureSlateProvider,
)
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.models.scoring import UpsetRanking
from upset_profiler.orchestration.pipelines.monitoring import (
    run_daily_monitoring_pipeline,
)
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateService
from upset_profiler.storage.repositories import Repositories, bootstrap

SLATE_DATE = date(2025, 1, 15)


def _preconditions() -> list[ValidatedPrecondition]:
    return [
        ValidatedPrecondition.model_validate(item)
        for item in load_json("historical", "validated_preconditions.json")
    ]


def _repositories() -> Repositories:
    repositories = bootstrap(":memory:")
    for precondition in _preconditions():
        repositories.validated_preconditions.upsert(precondition)
    return repositories


def _run(
    repositories: Repositories,
    *,
    selected_game_ids: list[str] | None = None,
    worker_count: int = 2,
) -> tuple[UpsetRanking, RunContext]:
    context = RunContext.create(actor="integration-test")
    ranking = run_daily_monitoring_pipeline(
        slate_service=SlateService(FixtureSlateProvider()),
        context_provider=FixtureContextProvider(),
        precondition_source=repositories.validated_preconditions,
        scoring_service=ScoringService(
            UpsetScorer(decision_model=FixtureDecisionModel())
        ),
        slate_date=SLATE_DATE,
        selected_game_ids=selected_game_ids,
        run_context=context,
        worker_count=worker_count,
        repositories=repositories,
    )
    return ranking, context


def _stable(ranking: UpsetRanking) -> list[tuple[str, float, int, str, tuple[str, ...]]]:
    return [
        (
            score.game_id,
            score.score,
            score.rank,
            score.rationale,
            tuple(str(citation.url) for citation in score.citations),
        )
        for score in ranking.scores
    ]


@pytest.mark.integration
def test_fixture_pipeline_ranks_flagged_games_and_persists() -> None:
    repositories = _repositories()
    try:
        ranking, context = _run(repositories)

        assert [score.game_id for score in ranking.scores] == [
            "401000001",
            "401000003",
        ]
        assert [score.rank for score in ranking.scores] == [1, 2]
        assert ranking.scores[0].score > ranking.scores[1].score

        run = repositories.analysis_runs.get(str(context.run_id))
        assert run is not None
        assert run["status"] == "completed"
        assert len(repositories.monitor_results.list_flags(str(context.run_id))) == 2
        assert [
            score.rank
            for score in repositories.upset_scores.list_for_run(str(context.run_id))
        ] == [1, 2]
    finally:
        repositories.close()


@pytest.mark.integration
def test_two_worker_and_one_worker_runs_produce_same_ranking() -> None:
    repositories = _repositories()
    try:
        two_worker, _ = _run(repositories, worker_count=2)
        one_worker, _ = _run(repositories, worker_count=1)
        assert _stable(two_worker) == _stable(one_worker)
    finally:
        repositories.close()


@pytest.mark.integration
def test_repeated_runs_are_stable() -> None:
    repositories = _repositories()
    try:
        first, _ = _run(repositories)
        second, _ = _run(repositories)
        assert _stable(first) == _stable(second)
    finally:
        repositories.close()


@pytest.mark.integration
def test_selected_non_matching_game_produces_empty_ranking() -> None:
    repositories = _repositories()
    try:
        ranking, context = _run(repositories, selected_game_ids=["401000002"])
        assert ranking.scores == []
        run = repositories.analysis_runs.get(str(context.run_id))
        assert run is not None
        assert run["status"] == "completed"
    finally:
        repositories.close()
