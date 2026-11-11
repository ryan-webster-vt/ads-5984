"""Historical research ingestion pipeline composition."""

from __future__ import annotations

from collections.abc import Sequence

from upset_profiler.agents.research_orchestrator import ResearchOrchestrator
from upset_profiler.context import RunContext
from upset_profiler.models.research import IngestionResult
from upset_profiler.services.ingestion import IngestionService


def run_historical_ingestion_pipeline(
    *,
    orchestrator: ResearchOrchestrator,
    season: str,
    game_ids: Sequence[str],
    run_context: RunContext | None = None,
) -> IngestionResult:
    """Ingest a historical season through the Research-Orchestrator."""
    context = run_context or RunContext.create(actor="ingest")
    return IngestionService(orchestrator).ingest(
        season=season,
        game_ids=game_ids,
        run_id=context.run_id,
    )
