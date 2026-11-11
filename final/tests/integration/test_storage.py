"""Integration tests for SQLite migrations and repositories."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tests.factories import (
    FIXED_TIME,
    make_candidate,
    make_daily_slate,
    make_historical_game,
    make_monitor_aggregate,
    make_score,
    make_validated_precondition,
)
from upset_profiler.errors import StorageError
from upset_profiler.models import (
    BacktestPrediction,
    SourceDocument,
    UpsetRanking,
    ValidationIssue,
)
from upset_profiler.storage import (
    Repositories,
    applied_versions,
    bootstrap,
    initialize_database,
    transaction,
)

_EXPECTED_TABLES = {
    "analysis_runs",
    "backtest_predictions",
    "cache_entries",
    "candidate_preconditions",
    "games",
    "monitor_results",
    "quarantine",
    "research_runs",
    "schema_migrations",
    "source_documents",
    "upset_scores",
    "validated_preconditions",
}


@pytest.fixture
def repositories(tmp_path: Path) -> Repositories:
    repos = bootstrap(tmp_path / "upset_profiler.db")
    try:
        yield repos
    finally:
        repos.close()


@pytest.mark.integration
def test_migrations_create_expected_tables(repositories: Repositories) -> None:
    rows = repositories.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    table_names = {str(row["name"]) for row in rows}
    assert _EXPECTED_TABLES.issubset(table_names)


@pytest.mark.integration
def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    first = initialize_database(database)
    assert applied_versions(first) == (1, 2)
    first.close()

    second = initialize_database(database)
    assert applied_versions(second) == (1, 2)
    second.close()


@pytest.mark.integration
def test_bootstrap_creates_parent_directories(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "dir" / "db.sqlite"
    repos = bootstrap(database)
    try:
        assert database.exists()
    finally:
        repos.close()


@pytest.mark.integration
def test_historical_game_round_trip(repositories: Repositories) -> None:
    game = make_historical_game()
    repositories.games.save_historical(game)

    assert repositories.games.get_historical(game.game_id) == game
    assert repositories.games.list_historical("2024-2025") == [game]
    assert repositories.games.list_historical("2023-2024") == []


@pytest.mark.integration
def test_daily_slate_round_trip(repositories: Repositories) -> None:
    slate = make_daily_slate()
    for game in slate.games:
        repositories.games.save_daily(game)

    stored = repositories.games.list_daily("2025-01-15")
    assert stored == slate.games
    assert repositories.games.get_daily(slate.games[0].game_id) == slate.games[0]


@pytest.mark.integration
def test_source_documents_round_trip(repositories: Repositories) -> None:
    game = make_historical_game()
    repositories.games.save_historical(game)
    document = SourceDocument(
        document_id="doc-1",
        game_id=game.game_id,
        url="https://example.com/report",
        title="Report",
        source_domain="example.com",
        published_at=FIXED_TIME,
        retrieved_at=FIXED_TIME,
        content="A pregame report.",
        content_hash="hash-1",
    )

    repositories.source_documents.upsert(document)

    assert repositories.source_documents.get("doc-1") == document
    assert repositories.source_documents.list_for_game(game.game_id) == [document]


@pytest.mark.integration
def test_source_document_requires_existing_game(repositories: Repositories) -> None:
    document = SourceDocument(
        document_id="doc-orphan",
        game_id="missing-game",
        url="https://example.com/report",
        source_domain="example.com",
        retrieved_at=FIXED_TIME,
        content="A report.",
    )
    with pytest.raises(StorageError):
        repositories.source_documents.upsert(document)


@pytest.mark.integration
def test_quarantine_entries_are_readable(repositories: Repositories) -> None:
    issue = ValidationIssue(location="game.home_score", message="missing score")
    repositories.quarantine.add_issues(
        stage="research",
        issues=[issue],
        run_id="run-1",
        game_id="401000001",
    )

    entries = repositories.quarantine.list_for_run("run-1")
    assert len(entries) == 1
    assert entries[0]["errors"] == [issue.model_dump(mode="json")]
    assert entries[0]["game_id"] == "401000001"


@pytest.mark.integration
def test_preconditions_round_trip(repositories: Repositories) -> None:
    candidate = make_candidate()
    repositories.candidate_preconditions.upsert(candidate)
    assert repositories.candidate_preconditions.get(candidate.precondition_id) == candidate

    validated = make_validated_precondition()
    repositories.validated_preconditions.upsert(validated)
    assert repositories.validated_preconditions.get(validated.precondition_id) == validated
    assert repositories.validated_preconditions.list_all() == [validated]


@pytest.mark.integration
def test_research_and_analysis_runs(repositories: Repositories) -> None:
    repositories.research_runs.create("run-1", payload={"games": 3})
    repositories.research_runs.update_status(
        "run-1", "completed", completed=True, summary="done"
    )
    research = repositories.research_runs.get("run-1")
    assert research is not None
    assert research["status"] == "completed"
    assert research["summary"] == "done"
    assert research["completed_at"] is not None
    assert research["payload"] == {"games": 3}

    repositories.analysis_runs.create("analysis-1", slate_date="2025-01-15")
    repositories.analysis_runs.update_status(
        "analysis-1", "completed", completed=True, payload={"flags": 1}
    )
    analysis = repositories.analysis_runs.get("analysis-1")
    assert analysis is not None
    assert analysis["status"] == "completed"
    assert analysis["payload"] == {"flags": 1}


@pytest.mark.integration
def test_monitor_results_and_scores(repositories: Repositories) -> None:
    run_id = uuid4()
    aggregate = make_monitor_aggregate(run_id)
    repositories.monitor_results.save_aggregate(aggregate)

    assert repositories.monitor_results.get_flag(str(run_id), "401000001") == aggregate.flags[0]
    assert repositories.monitor_results.list_flags(str(run_id)) == aggregate.flags

    ranking = UpsetRanking(
        run_id=run_id,
        model_name="d3-mini",
        scores=[make_score("401000001", rank=1), make_score("401000002", rank=2)],
    )
    repositories.upset_scores.save_ranking(ranking)
    assert repositories.upset_scores.list_for_run(str(run_id)) == ranking.scores


@pytest.mark.integration
def test_cache_ttl_and_purge(repositories: Repositories) -> None:
    repositories.cache.set("slate:2025-01-15", "payload", ttl_seconds=60, now=FIXED_TIME)
    assert repositories.cache.get("slate:2025-01-15", now=FIXED_TIME) == "payload"

    later = FIXED_TIME + timedelta(seconds=61)
    assert repositories.cache.get("slate:2025-01-15", now=later) is None
    assert repositories.cache.count() == 0

    repositories.cache.set("a", "1", ttl_seconds=10, now=FIXED_TIME)
    repositories.cache.set("b", "2", ttl_seconds=600, now=FIXED_TIME)
    assert repositories.cache.purge_expired(now=later) == 1
    assert repositories.cache.get("b", now=later) == "2"


@pytest.mark.integration
def test_transaction_rolls_back(repositories: Repositories) -> None:
    with pytest.raises(RuntimeError), transaction(repositories.connection):
        repositories.connection.execute(
            """
                INSERT INTO cache_entries (cache_key, value, created_at, expires_at)
                VALUES ('rollback', 'value', ?, ?)
                """,
            (FIXED_TIME.isoformat(), (FIXED_TIME + timedelta(hours=1)).isoformat()),
        )
        raise RuntimeError("abort")

    assert repositories.cache.count() == 0


@pytest.mark.integration
def test_backtest_predictions_round_trip(repositories: Repositories) -> None:
    prediction = BacktestPrediction(
        run_id=uuid4(),
        game_id="401000001",
        season="2024-2025",
        tipoff_at=FIXED_TIME,
        predicted_probability=0.62,
        actual_upset=True,
        model_name="d3-mini",
        features={"rest_days_difference": 3.0},
        predicted_at=FIXED_TIME - timedelta(hours=2),
    )
    repositories.backtest_predictions.upsert(prediction)

    assert repositories.backtest_predictions.list_for_run(str(prediction.run_id)) == [prediction]
