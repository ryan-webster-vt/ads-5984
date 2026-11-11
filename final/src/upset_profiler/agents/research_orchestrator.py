"""Research-Orchestrator: batch fan-out, join, validation, and persistence.

The orchestrator decomposes a season's game ids into deterministic batches,
runs Historical-Stats and Context-Research in parallel for each batch, joins on
``game_id``, enforces the source policy and leakage filter, then persists
validated records to SQLite and chunks/embeds documents into the vector store.
Invalid or unsafe records are quarantined with their errors instead of aborting
the whole run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, Literal
from uuid import UUID

from upset_profiler.agents.base import BaseAgent
from upset_profiler.agents.context_research import (
    ContextResearchAgent,
    ContextResearchProvider,
)
from upset_profiler.agents.historical_stats import HistoricalStatsAgent
from upset_profiler.errors import PipelineError
from upset_profiler.integrations.college_basketball import CollegeBasketballProvider
from upset_profiler.integrations.embeddings import EmbeddingProvider
from upset_profiler.models.common import (
    AgentError as AgentErrorRecord,
)
from upset_profiler.models.common import Provenance, ValidationIssue
from upset_profiler.models.games import HistoricalGame
from upset_profiler.models.progress import ProgressStatus
from upset_profiler.models.research import (
    ContextResearchResult,
    HistoricalStatsResult,
    ResearchAggregate,
    ResearchBatch,
    ResearchRecord,
    SourceDocument,
)
from upset_profiler.orchestration.progress import ProgressReporter
from upset_profiler.orchestration.runtime import run_parallel
from upset_profiler.rag.chunking import RecursiveChunker
from upset_profiler.security.leakage import LeakageFilter
from upset_profiler.security.source_whitelist import (
    SourceWhitelist,
    canonicalize_url,
)
from upset_profiler.storage.chroma import DocumentVectorStore
from upset_profiler.storage.repositories import Repositories


def build_research_batches(
    run_id: UUID,
    season: str,
    game_ids: Sequence[str],
    batch_size: int,
) -> list[ResearchBatch]:
    """Split game ids into deterministic, duplicate-free research batches."""
    if batch_size < 1:
        raise PipelineError("research batch size must be at least 1")
    unique = list(dict.fromkeys(game_ids))
    if not unique:
        raise PipelineError("historical ingestion requires at least one game id")
    return [
        ResearchBatch(
            batch_id=f"{season}-batch-{index // batch_size + 1:03d}",
            run_id=run_id,
            game_ids=unique[index : index + batch_size],
            season=season,
        )
        for index in range(0, len(unique), batch_size)
    ]


class ResearchOrchestrator(BaseAgent):
    """Coordinate historical statistics and contextual research ingestion."""

    name = "research-orchestrator"

    def __init__(
        self,
        *,
        historical_provider: CollegeBasketballProvider,
        context_provider: ContextResearchProvider,
        source_whitelist: SourceWhitelist,
        leakage_filter: LeakageFilter,
        repositories: Repositories,
        vector_store: DocumentVectorStore,
        embedding_provider: EmbeddingProvider,
        chunker: RecursiveChunker,
        batch_size: int = 10,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        if batch_size < 1:
            raise PipelineError("research batch size must be at least 1")
        self._historical_provider = historical_provider
        self._context_provider = context_provider
        self._source_whitelist = source_whitelist
        self._leakage_filter = leakage_filter
        self._repositories = repositories
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._chunker = chunker
        self._batch_size = batch_size

    @property
    def source(self) -> str:
        """Return the historical provider source name."""
        return self._historical_provider.source

    def run(
        self,
        *,
        season: str,
        game_ids: Sequence[str],
        run_id: UUID,
    ) -> ResearchAggregate:
        """Ingest one season's selected games and return a validated aggregate."""
        self._repositories.research_runs.create(
            str(run_id),
            status="running",
            payload={"season": season, "game_ids": list(game_ids)},
        )
        self._report(
            f"starting historical ingestion for {len(game_ids)} games",
            status=ProgressStatus.STARTED,
            completed=0,
            total=len(game_ids),
        )
        try:
            aggregate = self._execute(
                season=season,
                game_ids=game_ids,
                run_id=run_id,
            )
        except Exception as exc:
            self._repositories.research_runs.update_status(
                str(run_id),
                "failed",
                summary=str(exc),
            )
            raise
        self._repositories.research_runs.update_status(
            str(run_id),
            "completed",
            completed=True,
            summary=(
                f"{len(aggregate.records)} records, "
                f"{len(aggregate.quarantined)} quarantine issues"
            ),
            payload={
                "records": len(aggregate.records),
                "quarantined": len(aggregate.quarantined),
                "documents_stored": aggregate.documents_stored,
                "chunks_indexed": aggregate.chunks_indexed,
            },
        )
        self._report(
            f"historical ingestion complete: {len(aggregate.records)} records, "
            f"{len(aggregate.quarantined)} quarantine issues",
            status=ProgressStatus.COMPLETED,
            completed=len(game_ids),
            total=len(game_ids),
        )
        return aggregate

    def _execute(
        self,
        *,
        season: str,
        game_ids: Sequence[str],
        run_id: UUID,
    ) -> ResearchAggregate:
        unique_ids = list(dict.fromkeys(game_ids))
        batches = build_research_batches(run_id, season, unique_ids, self._batch_size)
        historical_by_game: dict[str, HistoricalGame] = {}
        documents_by_game: dict[str, list[SourceDocument]] = {}
        historical_error_ids: set[str] = set()
        issues: list[ValidationIssue] = []

        for batch in batches:
            self._report(
                f"researching batch {batch.batch_id}",
                status=ProgressStatus.STARTED,
                total=len(unique_ids),
            )
            historical_worker = HistoricalStatsAgent(
                self._historical_provider,
                progress=self.progress,
            )
            context_worker = ContextResearchAgent(
                self._context_provider,
                progress=self.progress,
            )
            tasks: dict[str, Callable[[], Any]] = {
                "historical-stats": partial(historical_worker.run, batch),
                "context-research": partial(context_worker.run, batch),
            }
            for outcome in run_parallel(tasks, max_workers=len(tasks)):
                if outcome.error is not None:
                    issues.append(
                        self._issue_from_exception(
                            outcome.error,
                            stage=outcome.key,
                        )
                    )
                    continue
                value = outcome.value
                if isinstance(value, HistoricalStatsResult):
                    for error in value.errors:
                        issues.append(self._issue_from_agent_error(error))
                        game_id = error.context.get("game_id")
                        if isinstance(game_id, str):
                            historical_error_ids.add(game_id)
                    for game in value.games:
                        historical_by_game[game.game_id] = game
                elif isinstance(value, ContextResearchResult):
                    for error in value.errors:
                        issues.append(self._issue_from_agent_error(error))
                    for document in value.documents:
                        if document.game_id not in unique_ids:
                            issues.append(
                                self._issue(
                                    location=f"documents[{document.document_id}]",
                                    message="document references an unrequested game",
                                    code="unexpected_game_document",
                                    game_id=document.game_id,
                                )
                            )
                            continue
                        documents_by_game.setdefault(document.game_id, []).append(
                            document
                        )

        records: list[ResearchRecord] = []
        for game_id in unique_ids:
            historical_game = historical_by_game.get(game_id)
            if historical_game is None:
                if game_id not in historical_error_ids:
                    issues.append(
                        self._issue(
                            location=f"games[{game_id}]",
                            message="no validated historical game was returned",
                            code="missing_historical_game",
                            game_id=game_id,
                        )
                    )
                continue
            visible, document_issues = self._apply_document_policy(
                documents_by_game.get(game_id, []),
                historical_game,
            )
            issues.extend(document_issues)
            records.append(
                ResearchRecord(
                    game_id=game_id,
                    historical_game=historical_game,
                    documents=visible,
                    provenance=Provenance(
                        run_id=run_id,
                        actor=self.name,
                        source=self.source,
                        metadata={"season": season},
                    ),
                )
            )

        documents_stored = self._persist(records, issues, run_id)
        chunks_indexed = self._index_documents(records, issues, season, run_id)
        return ResearchAggregate(
            run_id=run_id,
            records=records,
            quarantined=issues,
            documents_stored=documents_stored,
            chunks_indexed=chunks_indexed,
        )

    def _apply_document_policy(
        self,
        documents: Sequence[SourceDocument],
        game: HistoricalGame,
    ) -> tuple[list[SourceDocument], list[ValidationIssue]]:
        visible: list[SourceDocument] = []
        issues: list[ValidationIssue] = []
        for document in documents:
            try:
                canonical = canonicalize_url(str(document.url))
            except ValueError:
                issues.append(
                    self._issue(
                        location=f"documents[{document.document_id}]",
                        message="document URL is not canonicalizable",
                        code="invalid_source_url",
                        game_id=game.game_id,
                    )
                )
                continue
            if not self._source_whitelist.is_allowed_url(canonical):
                issues.append(
                    self._issue(
                        location=f"documents[{document.document_id}]",
                        message="document domain is not on the source whitelist",
                        code="source_not_whitelisted",
                        game_id=game.game_id,
                        url=canonical,
                    )
                )
                continue
            if not self._leakage_filter.is_visible(document.published_at, game.tipoff_at):
                code = (
                    "undated_document"
                    if document.published_at is None
                    else "post_tipoff_document"
                )
                issues.append(
                    self._issue(
                        location=f"documents[{document.document_id}]",
                        message="document is not knowable before tip-off",
                        code=code,
                        game_id=game.game_id,
                        url=canonical,
                    )
                )
                continue
            visible.append(document)

        cap = self._source_whitelist.max_documents_per_game
        if len(visible) > cap:
            issues.append(
                self._issue(
                    location=f"games[{game.game_id}]",
                    message="document cap applied",
                    code="document_cap_exceeded",
                    game_id=game.game_id,
                    kept=cap,
                    discarded=len(visible) - cap,
                )
            )
            visible = visible[:cap]
        return visible, issues

    def _persist(
        self,
        records: Sequence[ResearchRecord],
        issues: list[ValidationIssue],
        run_id: UUID,
    ) -> int:
        documents_stored = 0
        for record in records:
            if record.historical_game is not None:
                self._repositories.games.save_historical(record.historical_game)
            for document in record.documents:
                self._repositories.source_documents.upsert(document)
                documents_stored += 1
        for issue in issues:
            game_id = issue.context.get("game_id")
            self._repositories.quarantine.add_issues(
                stage="research",
                issues=[issue],
                run_id=str(run_id),
                game_id=game_id if isinstance(game_id, str) else None,
            )
        return documents_stored

    def _index_documents(
        self,
        records: Sequence[ResearchRecord],
        issues: list[ValidationIssue],
        season: str,
        run_id: UUID,
    ) -> int:
        chunks = []
        for record in records:
            if record.historical_game is None:
                continue
            for document in record.documents:
                chunks.extend(
                    self._chunker.chunk_document(
                        document,
                        season=record.historical_game.season,
                    )
                )
        if not chunks:
            return 0
        try:
            embeddings = self._embedding_provider.embed_documents(
                [chunk.text for chunk in chunks]
            )
            return self._vector_store.upsert(chunks, embeddings)
        except Exception as exc:
            issues.append(
                self._issue(
                    location="vector_store",
                    message=f"document indexing failed: {exc}",
                    code="embedding_failed",
                    severity="error",
                    run_id=str(run_id),
                    season=season,
                )
            )
            return 0

    def _issue_from_agent_error(self, error: AgentErrorRecord) -> ValidationIssue:
        return ValidationIssue(
            location=f"{error.stage or 'research'}:{error.agent or 'worker'}",
            message=error.message,
            code=error.error_type,
            severity="error",
            context=dict(error.context),
        )

    def _issue_from_exception(
        self,
        error: Exception,
        *,
        stage: str,
    ) -> ValidationIssue:
        return ValidationIssue(
            location=stage,
            message=str(error) or type(error).__name__,
            code=type(error).__name__,
            severity="error",
        )

    def _issue(
        self,
        *,
        location: str,
        message: str,
        code: str,
        game_id: str | None = None,
        severity: Literal["warning", "error"] = "warning",
        **context: object,
    ) -> ValidationIssue:
        merged: dict[str, Any] = dict(context)
        if game_id is not None:
            merged["game_id"] = game_id
        return ValidationIssue(
            location=location,
            message=message,
            code=code,
            severity=severity,
            context=merged,
        )
