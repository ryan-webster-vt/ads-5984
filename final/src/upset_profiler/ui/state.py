"""Service construction and fixture seeding for the Streamlit app."""

from __future__ import annotations

from dataclasses import dataclass, field

from upset_profiler.agents.upset_scorer import UpsetScorer
from upset_profiler.config import Settings
from upset_profiler.errors import ConfigurationError
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.espn import (
    EspnContextProvider,
    EspnSlateProvider,
    build_espn_context_provider,
    build_espn_slate_provider,
)
from upset_profiler.integrations.fakes import (
    FixtureContextProvider,
    FixtureDecisionModel,
    FixtureSlateProvider,
)
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.services.cache import (
    CachedSlateProvider,
    CacheService,
    CachingMonitoringService,
)
from upset_profiler.services.monitoring import MonitoringService
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateProvider, SlateService
from upset_profiler.storage.repositories import Repositories, bootstrap

FIXTURE_SOURCE = "Fixture (offline)"
LIVE_SOURCE = "Live ESPN"
SOURCE_OPTIONS = (FIXTURE_SOURCE, LIVE_SOURCE)
FIXTURE_SLATE_DATE = "2025-01-15"


@dataclass
class UiServices:
    """Everything one Streamlit run needs to fetch a slate and monitor games."""

    slate_provider: CachedSlateProvider
    context_provider: FixtureContextProvider | EspnContextProvider
    monitoring: CachingMonitoringService
    repositories: Repositories
    cache: CacheService
    espn_slate: EspnSlateProvider | None = None
    espn_context: EspnContextProvider | None = None
    warnings: list[str] = field(default_factory=list)

    def close(self) -> None:
        """Close network clients and the SQLite connection."""
        if self.espn_slate is not None:
            self.espn_slate.close()
        if self.espn_context is not None:
            self.espn_context.close()
        self.repositories.close()


def ensure_fixture_preconditions(repositories: Repositories) -> None:
    """Seed validated fixture preconditions only when the table is empty."""
    if repositories.validated_preconditions.list_all():
        return
    payloads = load_json("historical", "validated_preconditions.json")
    if not isinstance(payloads, list):
        raise ConfigurationError("validated preconditions fixture must be a JSON list")
    for payload in payloads:
        repositories.validated_preconditions.upsert(
            ValidatedPrecondition.model_validate(payload)
        )


def build_ui_services(settings: Settings, source: str) -> UiServices:
    """Build the fixture or live service graph for one app run."""
    if source not in SOURCE_OPTIONS:
        raise ConfigurationError(f"unsupported UI source {source!r}")

    repositories = bootstrap(settings.storage.sqlite_path)
    try:
        ensure_fixture_preconditions(repositories)
        cache = CacheService(
            repositories.cache,
            ttl_seconds=settings.storage.cache_ttl_seconds,
        )

        espn_slate: EspnSlateProvider | None = None
        espn_context: EspnContextProvider | None = None
        slate_provider: SlateProvider
        context_provider: FixtureContextProvider | EspnContextProvider
        warnings: list[str] = []
        if source == FIXTURE_SOURCE:
            slate_provider = FixtureSlateProvider()
            context_provider = FixtureContextProvider()
        else:
            espn_slate = build_espn_slate_provider(settings)
            espn_context = build_espn_context_provider(settings)
            slate_provider = espn_slate
            context_provider = espn_context
            warnings.append(
                "Live mode uses ESPN schedules for rest/back-to-back features. "
                "Travel and MCP context arrive in later phases."
            )

        cached_slate = CachedSlateProvider(slate_provider, cache)
        monitoring = MonitoringService(
            slate_service=SlateService(cached_slate),
            context_provider=context_provider,
            precondition_source=repositories.validated_preconditions,
            scoring_service=ScoringService(
                UpsetScorer(decision_model=FixtureDecisionModel())
            ),
            repositories=repositories,
        )
        return UiServices(
            slate_provider=cached_slate,
            context_provider=context_provider,
            monitoring=CachingMonitoringService(
                monitoring,
                cache,
                repositories.validated_preconditions,
            ),
            repositories=repositories,
            cache=cache,
            espn_slate=espn_slate,
            espn_context=espn_context,
            warnings=warnings,
        )
    except Exception:
        repositories.close()
        raise
