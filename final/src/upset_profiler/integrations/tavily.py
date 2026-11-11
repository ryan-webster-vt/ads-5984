"""Tavily-backed contextual research (default search backend, ADR-006).

One Tavily ``/search`` request with ``include_raw_content`` returns the result
text and a publication estimate, replacing the MCP search-then-fetch fanout.
The provider is deliberately additive: the ``ContextResearchProvider`` protocol
(``get_documents(game_id, season)``) and the research pipeline are unchanged.

Two deterministic checks still run here and, again, in the orchestrator:

* Tavily receives ``include_domains`` in restrict mode, and
  :class:`~upset_profiler.security.source_whitelist.SourceWhitelist` is applied
  to every returned URL.
* Tavily receives ``end_date`` plus ``filter_by_published_date`` when tip-off is
  known, and :class:`~upset_profiler.security.leakage.LeakageFilter` remains the
  authoritative client-side timestamp check.

Responses are cached in SQLite behind a normalized query key so a repeated or
gap-driven search never spends another credit. Chroma is not involved.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from pydantic import SecretStr

from upset_profiler.config import Settings
from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.integrations.base import (
    BaseHttpClient,
    HttpClientConfig,
    create_http_client,
)
from upset_profiler.models.games import HistoricalGame
from upset_profiler.models.research import SearchHitRecord, SourceDocument
from upset_profiler.security.source_whitelist import (
    SourceWhitelist,
    canonicalize_url,
    domain_from_url,
)
from upset_profiler.services.search_cache import (
    SearchCache,
    normalize_search_query,
    search_params_fingerprint,
    search_query_hash,
)

_SEARCH_PATH = "/search"
_MAX_INCLUDE_DOMAINS = 300
# Tavily credits: basic/fast searches cost one credit, advanced two.
_SEARCH_CREDITS: dict[str, float] = {
    "basic": 1.0,
    "advanced": 2.0,
    "fast": 1.0,
    "ultra-fast": 1.0,
}

GameLookup = Callable[[str, str], HistoricalGame | None]


@dataclass(frozen=True)
class TavilySearchConfig:
    """Validated Tavily search parameters sent with every request."""

    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    topic: Literal["general", "news"] = "general"
    max_results: int = 5
    include_raw_content: bool = True
    keyless: bool = False
    include_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.search_depth not in _SEARCH_CREDITS:
            raise ConfigurationError(
                "unsupported Tavily search depth",
                context={"search_depth": self.search_depth},
            )
        if self.topic not in {"general", "news"}:
            raise ConfigurationError(
                "unsupported Tavily topic",
                context={"topic": self.topic},
            )
        if not 1 <= self.max_results <= 20:
            raise ConfigurationError("TAVILY_MAX_RESULTS must be between 1 and 20")
        domains = tuple(
            dict.fromkeys(
                domain.strip().lower()
                for domain in self.include_domains
                if domain.strip()
            )
        )
        if len(domains) > _MAX_INCLUDE_DOMAINS:
            raise ConfigurationError(
                "Tavily accepts at most 300 include_domains",
                context={"count": len(domains)},
            )
        object.__setattr__(self, "include_domains", domains)


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_tavily_published_date(value: Any) -> datetime | None:
    """Parse Tavily's beta ``published_date`` hint into an aware UTC datetime.

    Tavily may return an ISO date, an ISO timestamp, an RFC 1123 string, or a
    Unix timestamp. Unparseable values are ignored; the deterministic client
    leakage filter still decides whether a document is knowable before tip-off.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return _ensure_utc(datetime.fromisoformat(candidate))
        except ValueError:
            continue
    try:
        return _ensure_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def _result_content(result: Mapping[str, Any]) -> str | None:
    for key in ("raw_content", "content"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _result_score(result: Mapping[str, Any]) -> float | None:
    value = result.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_tavily_results(payload: Any, *, limit: int) -> list[SearchHitRecord]:
    """Convert a Tavily search response into cacheable hit records.

    Hits are deduplicated by canonical URL and capped at ``limit``. Every hit
    with usable text is kept, including non-whitelisted domains: the whitelist
    is applied when documents are produced, and keeping the raw hit makes the
    rejection auditable from the cache.
    """
    if not isinstance(payload, dict):
        raise IntegrationError("Tavily response must be a JSON object", retryable=False)
    raw_results = payload.get("results", [])
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise IntegrationError(
            "Tavily response 'results' must be a list",
            retryable=False,
        )
    if limit < 1:
        return []

    hits: list[SearchHitRecord] = []
    seen: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            continue
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            canonical = canonicalize_url(url)
        except ValueError:
            continue
        if canonical in seen:
            continue
        content = _result_content(raw)
        if content is None:
            continue
        seen.add(canonical)
        title = raw.get("title")
        hits.append(
            SearchHitRecord(
                url=url.strip(),
                canonical_url=canonical,
                source_domain=domain_from_url(canonical),
                title=title if isinstance(title, str) and title.strip() else None,
                score=_result_score(raw),
                published_at=parse_tavily_published_date(
                    raw.get("published_date", raw.get("publishedDate"))
                ),
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                metadata={"kind": "tavily-search"},
            )
        )
        if len(hits) >= limit:
            break
    return hits


class TavilyContextResearchProvider:
    """Collect cited pregame documents through Tavily's search API."""

    source = "tavily"

    def __init__(
        self,
        client: BaseHttpClient,
        *,
        whitelist: SourceWhitelist,
        config: TavilySearchConfig,
        api_key: SecretStr | None = None,
        cache: SearchCache | None = None,
        game_lookup: GameLookup | None = None,
        max_documents: int | None = None,
    ) -> None:
        if not config.keyless and (
            api_key is None or not api_key.get_secret_value().strip()
        ):
            raise ConfigurationError(
                "TAVILY_API_KEY is required unless TAVILY_KEYLESS=true"
            )
        if max_documents is not None and max_documents < 0:
            raise ConfigurationError("max_documents must be non-negative")
        self._client = client
        self._whitelist = whitelist
        self._config = config
        self._api_key = api_key
        self._cache = cache
        self._game_lookup = game_lookup
        self._max_documents = (
            whitelist.max_documents_per_game if max_documents is None else max_documents
        )

    def get_documents(self, game_id: str, season: str) -> list[SourceDocument]:
        """Return whitelisted, cache-first documents for one game."""
        game = self._lookup_game(game_id, season)
        query = self._build_query(game_id, season, game)
        end_date: date | None = None if game is None else game.tipoff_at.date()
        params = self._request_params(end_date)
        normalized_query = normalize_search_query(query)
        params_hash = search_params_fingerprint(params)
        query_hash = search_query_hash(
            normalized_query,
            provider=self.source,
            params_hash=params_hash,
        )

        if self._cache is not None:
            cached = self._cache.get(query_hash)
            if cached is not None:
                return self._documents(cached.hits, game_id=game_id)

        payload = self._client.post_json(
            _SEARCH_PATH,
            json={"query": query, **params},
            headers=self._headers(),
        )
        hits = self._tag_hits(parse_tavily_results(payload, limit=self._config.max_results))
        if self._cache is not None:
            diagnostics = payload if isinstance(payload, dict) else {}
            self._cache.put(
                query_hash=query_hash,
                normalized_query=normalized_query,
                provider=self.source,
                params_hash=params_hash,
                hits=hits,
                game_id=game_id,
                season=season,
                credits_used=(
                    0.0
                    if self._config.keyless
                    else _SEARCH_CREDITS[self._config.search_depth]
                ),
                tipoff_at=None if game is None else game.tipoff_at,
                completed=False if game is None else game.completed,
                payload={
                    "request_id": diagnostics.get("request_id"),
                    "response_time": diagnostics.get("response_time"),
                    "search_depth": self._config.search_depth,
                    "topic": self._config.topic,
                    "end_date": None if end_date is None else end_date.isoformat(),
                },
            )
        return self._documents(hits, game_id=game_id)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _request_params(self, end_date: date | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "search_depth": self._config.search_depth,
            "topic": self._config.topic,
            "max_results": self._config.max_results,
            "include_raw_content": self._config.include_raw_content,
            "include_published_date": True,
        }
        if self._config.include_domains:
            params["include_domains"] = list(self._config.include_domains)
            params["include_domains_mode"] = "restrict"
        if end_date is not None:
            params["end_date"] = end_date.isoformat()
            params["filter_by_published_date"] = True
        return params

    def _headers(self) -> dict[str, str]:
        if self._config.keyless:
            return {"X-Tavily-Access-Mode": "keyless"}
        assert self._api_key is not None  # guarded in __init__
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    def _tag_hits(self, hits: Sequence[SearchHitRecord]) -> list[SearchHitRecord]:
        tagged: list[SearchHitRecord] = []
        for hit in hits:
            metadata = {
                **hit.metadata,
                "search_depth": self._config.search_depth,
                "topic": self._config.topic,
            }
            tagged.append(hit.model_copy(update={"metadata": metadata}))
        return tagged

    def _documents(
        self,
        hits: Sequence[SearchHitRecord],
        *,
        game_id: str,
    ) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for hit in hits:
            if len(documents) >= self._max_documents:
                break
            canonical = hit.canonical_url
            if canonical in seen:
                continue
            if not self._whitelist.is_allowed_url(canonical):
                continue
            content = (hit.content or "").strip()
            if not content:
                continue
            seen.add(canonical)
            digest = hit.content_hash or hashlib.sha256(content.encode("utf-8")).hexdigest()
            document_id = hashlib.sha256(f"{game_id}|{canonical}".encode()).hexdigest()
            documents.append(
                SourceDocument(
                    document_id=f"tavily-{document_id[:16]}",
                    game_id=game_id,
                    url=canonical,
                    title=hit.title,
                    source_domain=hit.source_domain,
                    published_at=hit.published_at,
                    retrieved_at=hit.retrieved_at,
                    content=content,
                    content_hash=digest,
                    metadata={**hit.metadata, "score": hit.score},
                )
            )
        return documents

    def _lookup_game(self, game_id: str, season: str) -> HistoricalGame | None:
        if self._game_lookup is None:
            return None
        try:
            game = self._game_lookup(game_id, season)
        except Exception:  # lookup failures must not abort research
            return None
        if game is None or game.season != season:
            return None
        return game

    @staticmethod
    def _build_query(
        game_id: str,
        season: str,
        game: HistoricalGame | None,
    ) -> str:
        if game is not None:
            return (
                f"{game.away_team.name} at {game.home_team.name} "
                f"{game.game_date.isoformat()} college basketball preview"
            )
        return f"{game_id} {season} college basketball pregame preview"


def build_tavily_context_provider(
    settings: Settings,
    *,
    whitelist: SourceWhitelist,
    cache: SearchCache | None = None,
    game_lookup: GameLookup | None = None,
) -> TavilyContextResearchProvider:
    """Build the Tavily provider from typed settings and the source policy."""
    tavily = settings.tavily
    client = create_http_client(
        HttpClientConfig(
            base_url=tavily.tavily_base_url,
            timeout_seconds=tavily.tavily_timeout_seconds,
        )
    )
    config = TavilySearchConfig(
        search_depth=tavily.tavily_search_depth,
        topic=tavily.tavily_topic,
        max_results=tavily.tavily_max_results,
        include_raw_content=tavily.tavily_include_raw_content,
        keyless=tavily.tavily_keyless,
        include_domains=tuple(whitelist.policy.allowed_domains),
    )
    return TavilyContextResearchProvider(
        client,
        whitelist=whitelist,
        config=config,
        api_key=tavily.tavily_api_key,
        cache=cache,
        game_lookup=game_lookup,
        max_documents=whitelist.max_documents_per_game,
    )
