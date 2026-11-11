"""Cache-backed wrappers for daily slate retrieval and analysis reruns.

Requirement R23 asks the system to reuse work for matchups that have already
been analyzed. The cache itself lives in SQLite (``cache_entries``); this module
owns the cache keys and the typed read/write behavior so neither the UI nor the
monitoring pipeline talks to raw cache strings.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from upset_profiler.agents.monitor_orchestrator import PreconditionSource
from upset_profiler.context import RunContext
from upset_profiler.models.games import DailySlate
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.models.scoring import UpsetRanking
from upset_profiler.orchestration.progress import ProgressSink
from upset_profiler.services.monitoring import MonitoringService
from upset_profiler.services.slate import SlateProvider
from upset_profiler.storage.repositories import CacheRepository

ModelT = TypeVar("ModelT", bound=BaseModel)

_CACHE_NAMESPACE = "v1"


def _precondition_fingerprint(preconditions: Sequence[ValidatedPrecondition]) -> str:
    payload = [
        {
            "id": precondition.precondition_id,
            "version": precondition.validation_version,
            "feature": precondition.candidate.feature,
            "comparison": precondition.candidate.comparison.value,
            "threshold": precondition.candidate.threshold,
        }
        for precondition in sorted(
            preconditions,
            key=lambda item: item.precondition_id,
        )
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class CacheService:
    """Typed access to the SQLite cache with a configurable TTL."""

    def __init__(self, repository: CacheRepository, *, ttl_seconds: int) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._repository = repository
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def slate_key(slate_date: date) -> str:
        """Return the cache key for one day's slate."""
        return f"{_CACHE_NAMESPACE}:slate:{slate_date.isoformat()}"

    @staticmethod
    def analysis_key(
        *,
        slate_date: date,
        game_ids: Sequence[str],
        worker_count: int,
        precondition_fingerprint: str,
    ) -> str:
        """Return the cache key for one exact analysis request."""
        ids = ",".join(sorted(set(game_ids)))
        return (
            f"{_CACHE_NAMESPACE}:analysis:{slate_date.isoformat()}:"
            f"{worker_count}:{precondition_fingerprint}:{ids}"
        )

    def get_model(self, model: type[ModelT], cache_key: str) -> ModelT | None:
        """Return a cached contract, or ``None`` when missing or expired."""
        value = self._repository.get(cache_key)
        if value is None:
            return None
        try:
            return model.model_validate_json(value)
        except ValidationError:
            self._repository.delete(cache_key)
            return None

    def set_model(
        self,
        cache_key: str,
        model: BaseModel,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Store a contract as JSON in the cache."""
        self._repository.set(
            cache_key,
            model.model_dump_json(),
            ttl_seconds=self._ttl_seconds,
            metadata=metadata,
        )

    def delete(self, cache_key: str) -> None:
        """Invalidate one cache entry."""
        self._repository.delete(cache_key)

    def purge_expired(self) -> int:
        """Remove expired entries and return how many were removed."""
        return self._repository.purge_expired()


class CachedSlateProvider:
    """Slate provider that reuses a stored slate for the same date."""

    def __init__(self, provider: SlateProvider, cache: CacheService) -> None:
        self._provider = provider
        self._cache = cache

    def get_slate(self, slate_date: date) -> DailySlate:
        """Return a cached slate or fetch and cache a fresh one."""
        key = CacheService.slate_key(slate_date)
        cached = self._cache.get_model(DailySlate, key)
        if cached is not None:
            return cached
        slate = self._provider.get_slate(slate_date)
        self._cache.set_model(key, slate, metadata={"kind": "slate"})
        return slate

    def invalidate(self, slate_date: date) -> None:
        """Drop the cached slate for one date."""
        self._cache.delete(CacheService.slate_key(slate_date))


@dataclass(frozen=True)
class AnalysisOutcome:
    """A ranking plus whether it came from the cache."""

    ranking: UpsetRanking
    from_cache: bool


class CachingMonitoringService:
    """Monitoring service wrapper that reuses identical analysis runs."""

    def __init__(
        self,
        service: MonitoringService,
        cache: CacheService,
        precondition_source: PreconditionSource,
    ) -> None:
        self._service = service
        self._cache = cache
        self._precondition_source = precondition_source

    def analyze(
        self,
        *,
        slate_date: date,
        selected_game_ids: Sequence[str] | None = None,
        run_context: RunContext | None = None,
        worker_count: int = 2,
        progress_sink: ProgressSink | None = None,
        use_cache: bool = True,
    ) -> AnalysisOutcome:
        """Return a cached ranking when the request matches an earlier run."""
        if selected_game_ids is None:
            raise ValueError("cached analysis requires explicit selected game ids")
        key = CacheService.analysis_key(
            slate_date=slate_date,
            game_ids=selected_game_ids,
            worker_count=worker_count,
            precondition_fingerprint=_precondition_fingerprint(
                self._precondition_source.list_all()
            ),
        )
        if use_cache:
            cached = self._cache.get_model(UpsetRanking, key)
            if cached is not None:
                return AnalysisOutcome(ranking=cached, from_cache=True)

        ranking = self._service.analyze(
            slate_date=slate_date,
            selected_game_ids=selected_game_ids,
            run_context=run_context,
            worker_count=worker_count,
            progress_sink=progress_sink,
        )
        self._cache.set_model(key, ranking, metadata={"kind": "analysis"})
        return AnalysisOutcome(ranking=ranking, from_cache=False)

    def invalidate(
        self,
        *,
        slate_date: date,
        selected_game_ids: Sequence[str],
        worker_count: int,
    ) -> None:
        """Drop one cached analysis request."""
        key = CacheService.analysis_key(
            slate_date=slate_date,
            game_ids=selected_game_ids,
            worker_count=worker_count,
            precondition_fingerprint=_precondition_fingerprint(
                self._precondition_source.list_all()
            ),
        )
        self._cache.delete(key)
