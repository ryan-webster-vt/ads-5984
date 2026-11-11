"""Integration tests for cache-backed slate and analysis reruns."""

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
from upset_profiler.models.games import DailySlate
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.services.cache import (
    CachedSlateProvider,
    CacheService,
    CachingMonitoringService,
)
from upset_profiler.services.monitoring import MonitoringService
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateService
from upset_profiler.storage.repositories import bootstrap

_DATE = date(2025, 1, 15)


class _CountingSlateProvider:
    def __init__(self) -> None:
        self.calls = 0
        self._inner = FixtureSlateProvider()

    def get_slate(self, slate_date: date) -> DailySlate:
        self.calls += 1
        return self._inner.get_slate(slate_date)


@pytest.mark.integration
def test_cached_slate_provider_reuses_one_fetch() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = CacheService(repositories.cache, ttl_seconds=60)
        counting = _CountingSlateProvider()
        provider = CachedSlateProvider(counting, cache)

        first = provider.get_slate(_DATE)
        second = provider.get_slate(_DATE)

        assert counting.calls == 1
        assert [game.game_id for game in first.games] == [
            game.game_id for game in second.games
        ]

        provider.invalidate(_DATE)
        provider.get_slate(_DATE)
        assert counting.calls == 2
    finally:
        repositories.close()


def _seed_preconditions(repositories: object) -> None:
    payloads = load_json("historical", "validated_preconditions.json")
    assert isinstance(payloads, list)
    for payload in payloads:
        repositories.validated_preconditions.upsert(  # type: ignore[attr-defined]
            ValidatedPrecondition.model_validate(payload)
        )


def _build_service(repositories: object, cache: CacheService) -> CachingMonitoringService:
    _seed_preconditions(repositories)
    inner = MonitoringService(
        slate_service=SlateService(FixtureSlateProvider()),
        context_provider=FixtureContextProvider(),
        precondition_source=repositories.validated_preconditions,  # type: ignore[attr-defined]
        scoring_service=ScoringService(UpsetScorer(decision_model=FixtureDecisionModel())),
        repositories=repositories,  # type: ignore[arg-type]
    )
    return CachingMonitoringService(
        inner,
        cache,
        repositories.validated_preconditions,  # type: ignore[attr-defined]
    )


@pytest.mark.integration
def test_caching_monitoring_service_reuses_identical_analysis() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = CacheService(repositories.cache, ttl_seconds=60)
        service = _build_service(repositories, cache)
        selected = ["401000001", "401000003"]

        first = service.analyze(
            slate_date=_DATE,
            selected_game_ids=selected,
            run_context=RunContext.create(actor="test"),
        )
        second = service.analyze(
            slate_date=_DATE,
            selected_game_ids=selected,
            run_context=RunContext.create(actor="test"),
        )

        assert first.from_cache is False
        assert second.from_cache is True
        assert [score.game_id for score in first.ranking.scores] == [
            score.game_id for score in second.ranking.scores
        ]
        assert [score.score for score in first.ranking.scores] == [
            score.score for score in second.ranking.scores
        ]

        service.invalidate(
            slate_date=_DATE,
            selected_game_ids=selected,
            worker_count=2,
        )
        third = service.analyze(
            slate_date=_DATE,
            selected_game_ids=selected,
            run_context=RunContext.create(actor="test"),
        )
        assert third.from_cache is False
    finally:
        repositories.close()
