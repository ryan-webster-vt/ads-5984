"""Integration tests for the SQLite search-response cache (ADR-006 stage B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from upset_profiler.models.research import SearchHitRecord
from upset_profiler.services.search_cache import (
    SearchCache,
    search_params_fingerprint,
    search_query_hash,
)
from upset_profiler.storage.repositories import bootstrap
from upset_profiler.storage.sqlite import applied_versions


def _hit(url: str, *, content: str = "pregame report") -> SearchHitRecord:
    return SearchHitRecord(
        url=url,
        canonical_url=url,
        source_domain="espn.com",
        title=f"Report from {url}",
        score=0.9,
        published_at=datetime(2024, 12, 9, 15, tzinfo=UTC),
        retrieved_at=datetime(2024, 12, 9, 18, tzinfo=UTC),
        content=content,
        content_hash=f"hash-{url}",
        metadata={"kind": "tavily-search", "search_depth": "basic"},
    )


def _put_historical(cache: SearchCache, *, hits: list[SearchHitRecord] | None = None):
    fingerprint = search_params_fingerprint({"search_depth": "basic"})
    query_hash = search_query_hash(
        "duke at clemson", provider="tavily", params_hash=fingerprint
    )
    return cache.put(
        query_hash=query_hash,
        normalized_query="duke at clemson",
        provider="tavily",
        params_hash=fingerprint,
        hits=hits if hits is not None else [_hit("https://espn.com/a")],
        game_id="401000001",
        season="2024-2025",
        credits_used=1,
        tipoff_at=datetime(2024, 12, 10, tzinfo=UTC),
        completed=True,
        payload={"request_id": "req-1"},
    )


@pytest.mark.integration
def test_migration_creates_search_cache_tables_once() -> None:
    repositories = bootstrap(":memory:")
    try:
        versions = applied_versions(repositories.connection)
        assert versions == (1, 2)
        tables = {
            str(row["name"])
            for row in repositories.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"search_queries", "search_hits"} <= tables
        assert repositories.search_cache.count_queries() == 0
    finally:
        repositories.close()


@pytest.mark.integration
def test_cache_round_trip_preserves_hits_and_payload() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        stored = _put_historical(
            cache,
            hits=[
                _hit("https://espn.com/a"),
                _hit("https://www.ncaa.com/b", content="second report"),
            ],
        )
        assert stored.expires_at is None

        loaded = cache.get(stored.query_hash)
        assert loaded is not None
        assert loaded.normalized_query == "duke at clemson"
        assert loaded.provider == "tavily"
        assert loaded.game_id == "401000001"
        assert loaded.season == "2024-2025"
        assert loaded.credits_used == 1
        assert loaded.payload == {"request_id": "req-1"}
        assert loaded.expires_at is None
        assert [hit.canonical_url for hit in loaded.hits] == [
            "https://espn.com/a",
            "https://www.ncaa.com/b",
        ]
        assert loaded.hits[1].content == "second report"
        assert loaded.hits[0].metadata["search_depth"] == "basic"
        assert repositories.search_cache.total_credits() == 1.0
    finally:
        repositories.close()


@pytest.mark.integration
def test_cache_upsert_replaces_hits_without_duplicating() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        stored = _put_historical(
            cache,
            hits=[_hit("https://espn.com/a"), _hit("https://espn.com/b")],
        )
        cache.put(
            query_hash=stored.query_hash,
            normalized_query=stored.normalized_query,
            provider=stored.provider,
            params_hash=stored.params_hash,
            hits=[_hit("https://espn.com/a")],
            game_id="401000001",
            season="2024-2025",
            credits_used=1,
            tipoff_at=datetime(2024, 12, 10, tzinfo=UTC),
            completed=True,
        )
        loaded = repositories.search_cache.get(stored.query_hash)
        assert loaded is not None
        assert loaded.result_count == 1
        assert [hit.canonical_url for hit in loaded.hits] == ["https://espn.com/a"]
        assert repositories.search_cache.count_queries() == 1
    finally:
        repositories.close()


@pytest.mark.integration
def test_expired_live_entries_are_ignored_and_purged() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        fingerprint = search_params_fingerprint({"topic": "news"})
        query_hash = search_query_hash(
            "duke at unc", provider="tavily", params_hash=fingerprint
        )
        stored = cache.put(
            query_hash=query_hash,
            normalized_query="duke at unc",
            provider="tavily",
            params_hash=fingerprint,
            hits=[_hit("https://espn.com/live")],
            game_id="401000009",
            season="2026-2027",
            credits_used=1,
            tipoff_at=datetime(2026, 12, 1, tzinfo=UTC),
            now=datetime(2026, 11, 10, 12, tzinfo=UTC),
        )
        assert stored.expires_at is not None

        later = stored.created_at + timedelta(seconds=61)
        assert repositories.search_cache.get(query_hash, now=later) is None
        assert repositories.search_cache.count_queries() == 0

        cache.put(
            query_hash=query_hash,
            normalized_query="duke at unc",
            provider="tavily",
            params_hash=fingerprint,
            hits=[_hit("https://espn.com/live")],
            game_id="401000009",
            season="2026-2027",
            credits_used=1,
            tipoff_at=datetime(2026, 12, 1, tzinfo=UTC),
            now=datetime(2026, 11, 10, tzinfo=UTC),
        )
        assert repositories.search_cache.purge_expired(
            now=datetime(2026, 11, 10, 0, 0, 30, tzinfo=UTC)
        ) == 0
        assert repositories.search_cache.purge_expired(
            now=datetime(2026, 11, 10, 0, 2, tzinfo=UTC)
        ) == 1
    finally:
        repositories.close()


@pytest.mark.integration
def test_historical_entries_survive_purge_and_list_by_game() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        stored = _put_historical(cache)
        assert repositories.search_cache.purge_expired(
            now=stored.created_at + timedelta(days=3650)
        ) == 0
        listed = repositories.search_cache.list_queries(game_id="401000001")
        assert [item.query_hash for item in listed] == [stored.query_hash]
        assert repositories.search_cache.list_queries(game_id="missing") == []

        repositories.search_cache.delete(stored.query_hash)
        assert repositories.search_cache.count_queries() == 0
        assert repositories.search_cache.list_queries() == []
    finally:
        repositories.close()


@pytest.mark.integration
def test_touch_updates_last_used_at() -> None:
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        stored = _put_historical(cache)
        later = stored.last_used_at + timedelta(minutes=5)
        repositories.search_cache.touch(stored.query_hash, now=later)
        loaded = repositories.search_cache.get(stored.query_hash)
        assert loaded is not None
        assert loaded.last_used_at == later
    finally:
        repositories.close()
