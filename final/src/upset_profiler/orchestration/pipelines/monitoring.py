"""Phase composition for the daily monitoring pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from upset_profiler.agents.monitor_orchestrator import PreconditionSource
from upset_profiler.agents.monitor_worker import GameContextProvider
from upset_profiler.context import RunContext
from upset_profiler.models.scoring import UpsetRanking
from upset_profiler.orchestration.progress import ProgressSink
from upset_profiler.services.monitoring import MonitoringService
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateService
from upset_profiler.storage.repositories import Repositories


def run_daily_monitoring_pipeline(
    *,
    slate_service: SlateService,
    context_provider: GameContextProvider,
    precondition_source: PreconditionSource,
    scoring_service: ScoringService,
    slate_date: date,
    selected_game_ids: Sequence[str] | None,
    run_context: RunContext,
    worker_count: int,
    progress_sink: ProgressSink | None = None,
    repositories: Repositories | None = None,
) -> UpsetRanking:
    """Compose and run the fixture-or-live daily monitoring pipeline."""
    return MonitoringService(
        slate_service=slate_service,
        context_provider=context_provider,
        precondition_source=precondition_source,
        scoring_service=scoring_service,
        repositories=repositories,
    ).analyze(
        slate_date=slate_date,
        selected_game_ids=selected_game_ids,
        run_context=run_context,
        worker_count=worker_count,
        progress_sink=progress_sink,
    )
