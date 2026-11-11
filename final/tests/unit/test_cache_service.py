"""Unit tests for typed cache access and cache-key construction."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from upset_profiler.context import utc_now
from upset_profiler.models.games import DailySlate
from upset_profiler.services.cache import CacheService
from upset_profiler.storage.repositories import bootstrap


@pytest.mark.unit
def test_cache_service_round_trips_models() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = CacheService(repositories.cache, ttl_seconds=60)
        slate = DailySlate(slate_date=date(2026, 11, 10), games=[], source="test")
        key = CacheService.slate_key(slate.slate_date)

        assert cache.get_model(DailySlate, key) is None
        cache.set_model(key, slate, metadata={"kind": "slate"})
        cached = cache.get_model(DailySlate, key)

        assert cached is not None
        assert cached.slate_date == date(2026, 11, 10)
        assert cached.source == "test"

        cache.delete(key)
        assert cache.get_model(DailySlate, key) is None
    finally:
        repositories.close()


@pytest.mark.unit
def test_cache_service_expires_entries() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = CacheService(repositories.cache, ttl_seconds=30)
        slate = DailySlate(slate_date=date(2026, 11, 10), games=[], source="test")
        key = CacheService.slate_key(slate.slate_date)
        cache.set_model(key, slate)

        future = utc_now() + timedelta(seconds=31)
        assert repositories.cache.get(key, now=future) is None
    finally:
        repositories.close()


@pytest.mark.unit
def test_analysis_key_is_order_independent_and_scoped() -> None:
    first = CacheService.analysis_key(
        slate_date=date(2026, 11, 10),
        game_ids=["b", "a"],
        worker_count=2,
        precondition_fingerprint="abc",
    )
    second = CacheService.analysis_key(
        slate_date=date(2026, 11, 10),
        game_ids=["a", "b"],
        worker_count=2,
        precondition_fingerprint="abc",
    )
    different = CacheService.analysis_key(
        slate_date=date(2026, 11, 10),
        game_ids=["a", "b"],
        worker_count=1,
        precondition_fingerprint="abc",
    )

    assert first == second
    assert first != different
