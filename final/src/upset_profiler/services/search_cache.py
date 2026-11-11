"""Normalized search-cache keys, TTL policy, and typed cache access.

ADR-006 Decision 2 separates two questions: SQLite answers "have I already seen
this exact query, and what came back?" while ChromaDB answers "which stored
passages are semantically relevant?". This module owns the deterministic half of
the first question:

* one pure normalization function (NFKC, casefold, whitespace collapse,
  surrounding punctuation stripped, token order preserved);
* a parameter fingerprint so depth/topic/domain/date changes are different
  cache entries;
* a query hash combining normalized text, provider, and fingerprint;
* the TTL policy (historical evidence is immutable, live evidence expires).

Keeping these pure makes them unit-testable without a database or network.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from upset_profiler.context import utc_now
from upset_profiler.models.research import CachedSearchResponse, SearchHitRecord
from upset_profiler.storage.repositories import SearchCacheRepository


def _strip_surrounding_punctuation(value: str) -> str:
    """Remove Unicode punctuation at both ends, preserving internal tokens."""
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end]


def normalize_search_query(text: str) -> str:
    """Return the canonical, deterministic form of a search query.

    Unicode NFKC and casefold make visually equivalent text identical, internal
    whitespace collapses to single spaces, and surrounding punctuation is
    stripped. Token order and stopwords are deliberately preserved: "duke at
    north carolina" and "north carolina at duke" are different questions.
    """
    if not isinstance(text, str):
        raise TypeError("search query must be a string")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = " ".join(normalized.split())
    normalized = _strip_surrounding_punctuation(normalized)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise ValueError("search query must contain at least one token")
    return normalized


def _canonical_param(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_param(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        items = [_canonical_param(item) for item in value]
        # Domain lists and other string sets are order-insensitive.
        if all(isinstance(item, str) for item in items):
            return sorted(items)
        return items
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported search parameter type: {type(value).__name__}")


def search_params_fingerprint(params: Mapping[str, Any]) -> str:
    """Return a stable fingerprint of the non-empty search parameters."""
    canonical = {
        str(key): _canonical_param(value)
        for key, value in params.items()
        if value is not None
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def search_query_hash(
    normalized_query: str,
    *,
    provider: str,
    params_hash: str,
) -> str:
    """Return the deterministic cache key for one normalized request."""
    payload = {
        "normalized_query": normalized_query,
        "provider": provider,
        "params_hash": params_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_search_expiry(
    requested_at: datetime,
    *,
    tipoff_at: datetime | None,
    completed: bool,
    ttl_seconds: int,
) -> datetime | None:
    """Return the cache expiry for a search response.

    A game whose tip-off is already in the past is historical: its evidence is
    immutable, so the row never expires. Live or future games expire after
    ``ttl_seconds`` because pregame news genuinely changes. When tip-off is
    unknown the conservative choice is a TTL, never a permanent row.
    """
    if ttl_seconds < 0:
        raise ValueError("ttl_seconds must be non-negative")
    if completed or (tipoff_at is not None and tipoff_at <= requested_at):
        return None
    return requested_at + timedelta(seconds=ttl_seconds)


class SearchCache:
    """Typed SQLite access plus the ADR-006 TTL policy."""

    def __init__(self, repository: SearchCacheRepository, *, ttl_seconds: int) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._repository = repository
        self._ttl_seconds = ttl_seconds

    def get(self, query_hash: str) -> CachedSearchResponse | None:
        """Return a cached response and record that it was reused."""
        response = self._repository.get(query_hash)
        if response is not None:
            self._repository.touch(query_hash)
        return response

    def put(
        self,
        *,
        query_hash: str,
        normalized_query: str,
        provider: str,
        params_hash: str,
        hits: Sequence[SearchHitRecord],
        game_id: str | None = None,
        season: str | None = None,
        credits_used: float = 0.0,
        tipoff_at: datetime | None = None,
        completed: bool = False,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> CachedSearchResponse:
        """Store one provider response under an immutable TTL policy."""
        current = now or utc_now()
        response = CachedSearchResponse(
            query_hash=query_hash,
            normalized_query=normalized_query,
            provider=provider,
            params_hash=params_hash,
            game_id=game_id,
            season=season,
            created_at=current,
            last_used_at=current,
            expires_at=resolve_search_expiry(
                current,
                tipoff_at=tipoff_at,
                completed=completed,
                ttl_seconds=self._ttl_seconds,
            ),
            result_count=len(hits),
            credits_used=credits_used,
            payload=payload or {},
            hits=list(hits),
        )
        self._repository.upsert(response)
        return response

    def purge_expired(self) -> int:
        """Remove expired live entries and return how many were removed."""
        return self._repository.purge_expired()
