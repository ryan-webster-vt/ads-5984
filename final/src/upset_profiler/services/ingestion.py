"""Historical ingestion use case."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from upset_profiler.agents.research_orchestrator import ResearchOrchestrator
from upset_profiler.context import utc_now
from upset_profiler.models.research import IngestionResult


class IngestionService:
    """Run the historical research orchestrator and summarize the result."""

    def __init__(self, orchestrator: ResearchOrchestrator) -> None:
        self._orchestrator = orchestrator

    def ingest(
        self,
        *,
        season: str,
        game_ids: Sequence[str],
        run_id: UUID,
    ) -> IngestionResult:
        """Ingest selected historical games and return a typed run summary."""
        started_at = utc_now()
        aggregate = self._orchestrator.run(
            season=season,
            game_ids=game_ids,
            run_id=run_id,
        )
        return IngestionResult(
            run_id=run_id,
            season=season,
            aggregate=aggregate,
            source=self._orchestrator.source,
            started_at=started_at,
            completed_at=utc_now(),
        )
