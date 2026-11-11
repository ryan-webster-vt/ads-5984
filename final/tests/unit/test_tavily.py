"""Tests for the Tavily Context-Research provider (ADR-006 stage A)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from pydantic import SecretStr

from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.integrations.base import (
    BaseHttpClient,
    HttpClientConfig,
    create_http_client,
)
from upset_profiler.integrations.tavily import (
    TavilyContextResearchProvider,
    TavilySearchConfig,
    parse_tavily_published_date,
    parse_tavily_results,
)
from upset_profiler.models.common import TeamRef
from upset_profiler.models.games import HistoricalGame
from upset_profiler.security.source_whitelist import SourcePolicy, SourceWhitelist
from upset_profiler.services.search_cache import SearchCache
from upset_profiler.storage.repositories import bootstrap

_BASE_URL = "https://api.tavily.test"
_SEARCH_URL = f"{_BASE_URL}/search"
_API_KEY = "tvly-test-key"


def _whitelist(*domains: str) -> SourceWhitelist:
    return SourceWhitelist(SourcePolicy(allowed_domains=list(domains or ("espn.com",))))


def _client() -> BaseHttpClient:
    return create_http_client(HttpClientConfig(base_url=_BASE_URL))


def _game(season: str = "2024-2025") -> HistoricalGame:
    return HistoricalGame(
        game_id="401000001",
        season=season,
        game_date=date(2024, 12, 10),
        tipoff_at=datetime(2024, 12, 10, tzinfo=UTC),
        home_team=TeamRef(team_id="150", name="Duke Blue Devils"),
        away_team=TeamRef(team_id="228", name="Clemson Tigers"),
        completed=True,
        home_score=70,
        away_score=60,
        winner_team_id="150",
    )


def _results(*results: dict[str, object]) -> dict[str, object]:
    return {"request_id": "req-1", "response_time": 0.4, "results": list(results)}


def _provider(
    client: BaseHttpClient,
    *,
    whitelist: SourceWhitelist | None = None,
    cache: SearchCache | None = None,
    game_lookup: object | None = None,
    config: TavilySearchConfig | None = None,
    api_key: str | None = _API_KEY,
) -> TavilyContextResearchProvider:
    return TavilyContextResearchProvider(
        client,
        whitelist=whitelist or _whitelist(),
        config=config or TavilySearchConfig(include_domains=("espn.com",)),
        api_key=None if api_key is None else SecretStr(api_key),
        cache=cache,
        game_lookup=game_lookup,  # type: ignore[arg-type]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-12-09", datetime(2024, 12, 9, tzinfo=UTC)),
        ("2024-12-09T15:30:00Z", datetime(2024, 12, 9, 15, 30, tzinfo=UTC)),
        ("2024-12-09T15:30:00-05:00", datetime(2024, 12, 9, 20, 30, tzinfo=UTC)),
        ("Mon, 09 Dec 2024 15:30:00 GMT", datetime(2024, 12, 9, 15, 30, tzinfo=UTC)),
        (1733758200, datetime(2024, 12, 9, 15, 30, tzinfo=UTC)),
    ],
)
def test_parse_tavily_published_date_accepts_common_formats(
    value: object,
    expected: datetime,
) -> None:
    assert parse_tavily_published_date(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", True, [], {}])
def test_parse_tavily_published_date_ignores_unparseable_values(value: object) -> None:
    assert parse_tavily_published_date(value) is None


@pytest.mark.unit
def test_parse_tavily_results_prefers_raw_content_and_deduplicates() -> None:
    hits = parse_tavily_results(
        _results(
            {
                "url": "https://www.espn.com/story#fragment",
                "title": "Preview",
                "content": "snippet",
                "raw_content": "full pregame report",
                "score": 0.8,
                "published_date": "2024-12-09",
            },
            {
                "url": "https://www.espn.com/story",
                "content": "duplicate canonical URL",
            },
            {
                "url": "https://www.espn.com/recap",
                "content": "second report",
            },
        ),
        limit=5,
    )

    assert [hit.canonical_url for hit in hits] == [
        "https://www.espn.com/story",
        "https://www.espn.com/recap",
    ]
    assert hits[0].content == "full pregame report"
    assert hits[0].title == "Preview"
    assert hits[0].score == 0.8
    assert hits[0].published_at == datetime(2024, 12, 9, tzinfo=UTC)
    assert hits[0].source_domain == "www.espn.com"
    assert hits[0].content_hash is not None


@pytest.mark.unit
def test_parse_tavily_results_skips_invalid_and_caps_limit() -> None:
    hits = parse_tavily_results(
        _results(
            {"url": "ftp://espn.com/a", "content": "bad scheme"},
            {"url": "", "content": "no url"},
            {"url": "https://espn.com/a", "content": "   "},
            {"url": "https://espn.com/b", "content": "one"},
            {"url": "https://espn.com/c", "content": "two"},
        ),
        limit=1,
    )
    assert [hit.canonical_url for hit in hits] == ["https://espn.com/b"]


@pytest.mark.unit
def test_parse_tavily_results_returns_nothing_for_a_zero_limit() -> None:
    payload = _results({"url": "https://espn.com/a", "content": "one"})
    assert parse_tavily_results(payload, limit=0) == []


@pytest.mark.unit
def test_parse_tavily_results_rejects_non_object_payload() -> None:
    with pytest.raises(IntegrationError):
        parse_tavily_results([], limit=5)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"search_depth": "deep"},
        {"topic": "sports"},
        {"max_results": 0},
        {"max_results": 21},
        {"include_domains": tuple(f"d{i}.com" for i in range(301))},
    ],
)
def test_tavily_search_config_rejects_invalid_parameters(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ConfigurationError):
        TavilySearchConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_tavily_search_config_normalizes_include_domains() -> None:
    config = TavilySearchConfig(include_domains=(" ESPN.com ", "", "espn.com", "ncaa.com"))
    assert config.include_domains == ("espn.com", "ncaa.com")


@pytest.mark.unit
def test_provider_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="TAVILY_API_KEY"):
        _provider(_client(), api_key=None)


@pytest.mark.unit
def test_provider_allows_keyless_mode() -> None:
    provider = _provider(
        _client(),
        config=TavilySearchConfig(keyless=True),
        api_key=None,
    )
    assert provider._headers() == {"X-Tavily-Access-Mode": "keyless"}


@pytest.mark.unit
@respx.mock
def test_provider_sends_leakage_bounds_and_restricted_domains() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_results({"url": "https://espn.com/story", "raw_content": "report"}),
        )
    )
    client = _client()
    provider = _provider(
        client,
        game_lookup=lambda game_id, season: _game(season),
    )
    try:
        documents = provider.get_documents("401000001", "2024-2025")
    finally:
        provider.close()

    assert route.called
    assert len(documents) == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == f"Bearer {_API_KEY}"
    body = json.loads(request.content)
    assert body["query"] == (
        "Clemson Tigers at Duke Blue Devils 2024-12-10 college basketball preview"
    )
    assert body["include_domains"] == ["espn.com"]
    assert body["include_domains_mode"] == "restrict"
    assert body["end_date"] == "2024-12-10"
    assert body["filter_by_published_date"] is True
    assert body["include_raw_content"] is True
    assert body["include_published_date"] is True


@pytest.mark.unit
@respx.mock
def test_provider_omits_leakage_bounds_when_tipoff_is_unknown() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_results({"url": "https://espn.com/story", "raw_content": "report"}),
        )
    )
    client = _client()
    provider = _provider(
        client,
        game_lookup=lambda game_id, season: _game("2025-2026"),
    )
    try:
        provider.get_documents("401000001", "2024-2025")
    finally:
        provider.close()

    body = json.loads(route.calls[0].request.content)
    assert "end_date" not in body
    assert "filter_by_published_date" not in body
    assert body["query"] == "401000001 2024-2025 college basketball pregame preview"


@pytest.mark.unit
@respx.mock
def test_provider_applies_whitelist_and_document_cap() -> None:
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_results(
                {
                    "url": "https://www.espn.com/story",
                    "title": "Preview",
                    "raw_content": "pregame report",
                },
                {"url": "https://example.com/untrusted", "raw_content": "untrusted"},
                {"url": "https://espn.com/recap", "raw_content": "second report"},
            ),
        )
    )
    client = _client()
    provider = _provider(client, whitelist=_whitelist("espn.com"))
    try:
        documents = provider.get_documents("401000001", "2024-2025")
    finally:
        provider.close()

    assert [str(document.url) for document in documents] == [
        "https://www.espn.com/story",
        "https://espn.com/recap",
    ]
    assert all(document.source_domain.endswith("espn.com") for document in documents)
    assert documents[0].content == "pregame report"
    assert documents[0].metadata["kind"] == "tavily-search"
    assert documents[0].metadata["search_depth"] == "basic"


@pytest.mark.unit
@respx.mock
def test_provider_reuses_cached_response_without_a_second_request() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_results({"url": "https://espn.com/story", "raw_content": "report"}),
        )
    )
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        client = _client()
        provider = _provider(
            client,
            cache=cache,
            game_lookup=lambda game_id, season: _game(season),
        )
        try:
            first = provider.get_documents("401000001", "2024-2025")
            second = provider.get_documents("401000001", "2024-2025")
        finally:
            provider.close()

        assert [str(document.url) for document in first] == [
            str(document.url) for document in second
        ]
        assert route.call_count == 1
        assert repositories.search_cache.count_queries() == 1
        assert repositories.search_cache.total_credits() == 1.0
        stored = repositories.search_cache.list_queries(game_id="401000001")[0]
        assert stored.expires_at is None
        assert stored.result_count == 1
    finally:
        repositories.close()


@pytest.mark.unit
@respx.mock
def test_provider_caches_fresh_live_queries_with_a_ttl() -> None:
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_results({"url": "https://espn.com/story", "raw_content": "report"}),
        )
    )
    repositories = bootstrap(":memory:")
    try:
        cache = SearchCache(repositories.search_cache, ttl_seconds=60)
        client = _client()
        provider = _provider(client, cache=cache)
        try:
            provider.get_documents("401000099", "2026-2027")
        finally:
            provider.close()

        stored = repositories.search_cache.list_queries()[0]
        assert stored.expires_at is not None
        assert stored.game_id == "401000099"
    finally:
        repositories.close()
