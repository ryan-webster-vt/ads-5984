"""Context-Research worker: cited documents for assigned game ids."""

from __future__ import annotations

from typing import Protocol

from upset_profiler.agents.base import BaseAgent
from upset_profiler.errors import AgentError
from upset_profiler.models.common import AgentError as AgentErrorRecord
from upset_profiler.models.progress import ProgressStatus
from upset_profiler.models.research import (
    ContextResearchResult,
    ResearchBatch,
    SourceDocument,
)
from upset_profiler.orchestration.progress import ProgressReporter


class ContextResearchProvider(Protocol):
    """Source of cited documents for a game."""

    source: str

    def get_documents(self, game_id: str, season: str) -> list[SourceDocument]:
        """Return cited documents for one game."""
        ...


class NullContextResearchProvider:
    """Context-Research provider that returns no documents.

    Used by ``ingest --context-provider none`` for statistics-only runs and
    offline smoke tests. It keeps the provider protocol and pipeline shape.
    """

    source = "none"

    def get_documents(self, game_id: str, season: str) -> list[SourceDocument]:
        """Return no documents."""
        return []


class ContextResearchAgent(BaseAgent):
    """Collect cited pregame documents for one batch of game ids."""

    name = "context-research"

    def __init__(
        self,
        provider: ContextResearchProvider,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        self._provider = provider

    def run(self, batch: ResearchBatch) -> ContextResearchResult:
        """Fetch documents for every game, isolating per-game failures."""
        if batch.season is None:
            raise AgentError(
                "context research batch requires a season",
                context={"batch_id": batch.batch_id},
            )
        documents: list[SourceDocument] = []
        errors: list[AgentErrorRecord] = []
        self._report(
            f"researching context for {len(batch.game_ids)} games",
            status=ProgressStatus.STARTED,
            total=len(batch.game_ids),
        )
        for index, game_id in enumerate(batch.game_ids, start=1):
            try:
                game_documents = self._provider.get_documents(game_id, batch.season)
            except Exception as exc:
                errors.append(
                    AgentErrorRecord.from_exception(
                        exc,
                        agent=self.name,
                        stage="research",
                        context={"game_id": game_id, "batch_id": batch.batch_id},
                    )
                )
            else:
                documents.extend(game_documents)
            self._report(
                f"researched context for game {game_id}",
                completed=index,
                total=len(batch.game_ids),
                game_id=game_id,
            )
        self._report(
            f"context research complete: {len(documents)} documents, "
            f"{len(errors)} errors",
            status=ProgressStatus.COMPLETED,
            completed=len(batch.game_ids),
            total=len(batch.game_ids),
        )
        return ContextResearchResult(
            run_id=batch.run_id,
            batch_id=batch.batch_id,
            documents=documents,
            errors=errors,
        )
