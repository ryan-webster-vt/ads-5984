"""Daily monitoring use case: slate selection, Monitor, and scoring."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from upset_profiler.agents.monitor_orchestrator import (
    MonitorOrchestrator,
    PreconditionSource,
)
from upset_profiler.agents.monitor_worker import GameContextProvider
from upset_profiler.context import RunContext
from upset_profiler.errors import ConfigurationError
from upset_profiler.models.monitoring import MonitorRequest
from upset_profiler.models.progress import ProgressStage
from upset_profiler.models.scoring import UpsetRanking
from upset_profiler.orchestration.progress import (
    NullProgressSink,
    ProgressReporter,
    ProgressSink,
)
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateService
from upset_profiler.storage.repositories import Repositories


class MonitoringService:
    """Compose the daily slate, parallel Monitor workers, and the scorer."""

    def __init__(
        self,
        *,
        slate_service: SlateService,
        context_provider: GameContextProvider,
        precondition_source: PreconditionSource,
        scoring_service: ScoringService,
        repositories: Repositories | None = None,
    ) -> None:
        self._slate_service = slate_service
        self._context_provider = context_provider
        self._precondition_source = precondition_source
        self._scoring_service = scoring_service
        self._repositories = repositories

    def analyze(
        self,
        *,
        slate_date: date,
        selected_game_ids: Sequence[str] | None = None,
        run_context: RunContext | None = None,
        worker_count: int = 2,
        progress_sink: ProgressSink | None = None,
    ) -> UpsetRanking:
        """Run the daily monitoring and scoring pipeline for one slate."""
        if worker_count < 1:
            raise ConfigurationError("worker_count must be at least 1")

        context = run_context or RunContext.create(actor="monitoring-service")
        slate = self._slate_service.get_slate(slate_date)
        games = self._slate_service.select_games(slate, selected_game_ids)
        if not games:
            raise ConfigurationError("no games selected for analysis")

        request = MonitorRequest(
            run_id=context.run_id,
            slate_date=slate.slate_date,
            games=games,
            worker_count=worker_count,
        )
        reporter = ProgressReporter(
            run_id=context.run_id,
            sink=progress_sink or NullProgressSink(),
            stage=ProgressStage.MONITOR,
            agent="monitor-orchestrator",
            total=len(games),
        )

        if self._repositories is not None:
            self._repositories.analysis_runs.create(
                str(context.run_id),
                slate_date=slate.slate_date.isoformat(),
                payload=request,
            )

        try:
            aggregate = MonitorOrchestrator(
                context_provider=self._context_provider,
                precondition_source=self._precondition_source,
                progress=reporter,
            ).run(request)
            ranking = self._scoring_service.rank(aggregate)
        except Exception:
            if self._repositories is not None:
                self._repositories.analysis_runs.update_status(
                    str(context.run_id),
                    "failed",
                    completed=True,
                )
            raise

        if self._repositories is not None:
            self._repositories.monitor_results.save_aggregate(aggregate)
            self._repositories.upset_scores.save_ranking(ranking)
            self._repositories.analysis_runs.update_status(
                str(context.run_id),
                "completed",
                completed=True,
                payload=ranking,
            )
        return ranking
