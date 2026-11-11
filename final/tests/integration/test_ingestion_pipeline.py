"""Integration test for the Phase 4 historical ingestion pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from upset_profiler.agents.context_research import ContextResearchAgent
from upset_profiler.agents.historical_stats import HistoricalStatsAgent
from upset_profiler.agents.research_orchestrator import (
    ResearchOrchestrator,
    build_research_batches,
)
from upset_profiler.context import RunContext
from upset_profiler.integrations.college_basketball import FixtureHistoricalStatsProvider
from upset_profiler.integrations.embeddings import DeterministicEmbeddingProvider
from upset_profiler.integrations.fakes import FixtureContextResearchProvider
from upset_profiler.models.progress import ProgressStage
from upset_profiler.models.research import SourceDocument
from upset_profiler.orchestration.pipelines.ingestion import (
    run_historical_ingestion_pipeline,
)
from upset_profiler.orchestration.progress import (
    InMemoryProgressSink,
    ProgressReporter,
)
from upset_profiler.rag.chunking import ChunkingConfig, RecursiveChunker
from upset_profiler.security.leakage import LeakageFilter
from upset_profiler.security.source_whitelist import SourceWhitelist
from upset_profiler.storage.chroma import InMemoryVectorStore
from upset_profiler.storage.repositories import Repositories, bootstrap

SEASON = "2024-2025"
GAME_IDS = ["401000001", "401000002", "401000003", "401000004"]


def _orchestrator(
    project_root: Path,
    repositories: Repositories,
    store: InMemoryVectorStore,
    chunker: RecursiveChunker,
    progress: ProgressReporter,
    embeddings: DeterministicEmbeddingProvider,
) -> ResearchOrchestrator:
    return ResearchOrchestrator(
        historical_provider=FixtureHistoricalStatsProvider(),
        context_provider=FixtureContextResearchProvider(),
        source_whitelist=SourceWhitelist.from_path(project_root / "config" / "sources.yaml"),
        leakage_filter=LeakageFilter(strict=True),
        repositories=repositories,
        vector_store=store,
        embedding_provider=embeddings,
        chunker=chunker,
        batch_size=2,
        progress=progress,
    )


def test_fixture_season_ingestion_quarantines_and_retrieves(project_root: Path) -> None:
    repositories = bootstrap(":memory:")
    store = InMemoryVectorStore()
    embeddings = DeterministicEmbeddingProvider()
    chunker = RecursiveChunker(ChunkingConfig(chunk_size_tokens=30, overlap_tokens=5))
    sink = InMemoryProgressSink()
    run_context = RunContext.create(actor="test")
    progress = ProgressReporter(
        run_id=run_context.run_id,
        sink=sink,
        stage=ProgressStage.INGEST,
        agent="research-orchestrator",
        total=len(GAME_IDS),
    )
    orchestrator = _orchestrator(
        project_root,
        repositories,
        store,
        chunker,
        progress,
        embeddings,
    )

    try:
        result = run_historical_ingestion_pipeline(
            orchestrator=orchestrator,
            season=SEASON,
            game_ids=GAME_IDS,
            run_context=run_context,
        )

        assert [record.game_id for record in result.aggregate.records] == [
            "401000001",
            "401000002",
            "401000003",
        ]
        assert result.record_count == 3
        assert result.source == "fixture"
        assert result.aggregate.documents_stored == 4
        assert result.aggregate.chunks_indexed > 0

        codes = {
            error["code"]
            for entry in repositories.quarantine.list_for_run(str(run_context.run_id))
            for error in entry["errors"]
        }
        assert "post_tipoff_document" in codes
        assert "undated_document" in codes
        assert "source_not_whitelisted" in codes
        assert "ValidationError" in codes

        stored_game = repositories.games.get_historical("401000001")
        assert stored_game is not None
        assert len(repositories.source_documents.list_for_game("401000001")) == 2
        assert len(repositories.source_documents.list_for_game("401000002")) == 1

        document = repositories.source_documents.list_for_game("401000001")[0]
        chunk = chunker.chunk_document(document, season=SEASON)[0]
        evidence = store.query(
            embeddings.embed_query(chunk.text),
            query_text="rest advantage",
            top_k=1,
            game_id="401000001",
        )
        assert evidence
        assert str(evidence[0].chunk.source_url) == str(document.url)
        assert evidence[0].chunk.published_at is not None

        run = repositories.research_runs.get(str(run_context.run_id))
        assert run is not None
        assert run["status"] == "completed"

        events = sink.events
        assert events
        assert all(event.stage is ProgressStage.INGEST for event in events)
        assert events[-1].status.value == "completed"

        first_chunk_count = store.count()
        second_context = RunContext.create(actor="test")
        second_progress = ProgressReporter(
            run_id=second_context.run_id,
            sink=sink,
            stage=ProgressStage.INGEST,
            agent="research-orchestrator",
            total=len(GAME_IDS),
        )
        second_orchestrator = _orchestrator(
            project_root,
            repositories,
            store,
            chunker,
            second_progress,
            embeddings,
        )
        second = run_historical_ingestion_pipeline(
            orchestrator=second_orchestrator,
            season=SEASON,
            game_ids=GAME_IDS,
            run_context=second_context,
        )
        assert second.record_count == 3
        assert store.count() == first_chunk_count
        assert len(repositories.source_documents.list_for_game("401000001")) == 2
    finally:
        repositories.close()


def test_context_research_sees_tipoff_before_searching_on_a_cold_run(
    project_root: Path,
) -> None:
    """The tip-off must be persisted before Context-Research searches.

    Context-Research dates its search with the game's tip-off, and the tip-off is
    part of the search-cache key. Writing the ``games`` table only after the
    workers join meant a cold run searched without it, cached the response under
    a different key with a TTL, and paid for the same game again on a re-run.
    """

    class _TipoffRecordingProvider:
        """Context provider that records the tip-off visible at search time."""

        source = "recording"

        def __init__(self, repositories: Repositories) -> None:
            self._repositories = repositories
            self.observed: dict[str, datetime | None] = {}

        def get_documents(self, game_id: str, season: str) -> list[SourceDocument]:
            game = self._repositories.games.get_historical(game_id)
            self.observed[game_id] = None if game is None else game.tipoff_at
            return []

    repositories = bootstrap(":memory:")
    store = InMemoryVectorStore()
    embeddings = DeterministicEmbeddingProvider()
    chunker = RecursiveChunker(ChunkingConfig(chunk_size_tokens=30, overlap_tokens=5))
    run_context = RunContext.create(actor="test")
    progress = ProgressReporter(
        run_id=run_context.run_id,
        sink=InMemoryProgressSink(),
        stage=ProgressStage.INGEST,
        agent="research-orchestrator",
        total=len(GAME_IDS),
    )
    provider = _TipoffRecordingProvider(repositories)
    orchestrator = ResearchOrchestrator(
        historical_provider=FixtureHistoricalStatsProvider(),
        context_provider=provider,
        source_whitelist=SourceWhitelist.from_path(project_root / "config" / "sources.yaml"),
        leakage_filter=LeakageFilter(strict=True),
        repositories=repositories,
        vector_store=store,
        embedding_provider=embeddings,
        chunker=chunker,
        batch_size=2,
        progress=progress,
    )

    try:
        run_historical_ingestion_pipeline(
            orchestrator=orchestrator,
            season=SEASON,
            game_ids=GAME_IDS,
            run_context=run_context,
        )

        # A cold run: every game the fixture can serve is already stored by the
        # time its documents are researched.
        fixture = FixtureHistoricalStatsProvider()
        for game_id in ("401000001", "401000002", "401000003"):
            assert provider.observed[game_id] == fixture.get_game(game_id, SEASON).tipoff_at
            assert provider.observed[game_id] is not None

        # A game whose statistics cannot be resolved is never searched for: the
        # record would be quarantined as missing, so a credit spent here is wasted.
        assert "401000004" not in provider.observed

        codes_for_failed_game = {
            error["code"]
            for entry in repositories.quarantine.list_for_run(str(run_context.run_id))
            for error in entry["errors"]
            if error.get("context", {}).get("game_id") == "401000004"
        }
        assert codes_for_failed_game
    finally:
        repositories.close()


def test_build_research_batches_is_deduplicated_and_deterministic() -> None:
    batches = build_research_batches(
        RunContext.create().run_id,
        SEASON,
        ["b", "a", "b", "c"],
        batch_size=2,
    )
    assert [batch.game_ids for batch in batches] == [["b", "a"], ["c"]]
    assert [batch.batch_id for batch in batches] == [
        "2024-2025-batch-001",
        "2024-2025-batch-002",
    ]


def test_workers_isolate_per_game_failures() -> None:
    historical = HistoricalStatsAgent(FixtureHistoricalStatsProvider())
    batch = build_research_batches(
        RunContext.create().run_id,
        SEASON,
        ["401000001", "401000004"],
        batch_size=2,
    )[0]
    result = historical.run(batch)
    assert [game.game_id for game in result.games] == ["401000001"]
    assert result.errors[0].context["game_id"] == "401000004"

    context = ContextResearchAgent(FixtureContextResearchProvider())
    context_result = context.run(batch)
    assert len(context_result.documents) == 2
    assert context_result.errors == []
