"""End-to-end CLI test for the Phase 4 ingestion command."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from upset_profiler.cli import main
from upset_profiler.integrations.college_basketball import FixtureHistoricalStatsProvider
from upset_profiler.storage.repositories import bootstrap

_TAVILY_URL = "https://api.tavily.com/search"


def _base_args(database: Path) -> list[str]:
    return [
        "ingest",
        "--season",
        "2024-2025",
        "--database",
        str(database),
        "--vector-store",
        "memory",
        "--embedding-provider",
        "fixture",
        "--batch-size",
        "2",
    ]


def test_ingest_cli_runs_offline_and_is_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "upset.db"
    exit_code = main(_base_args(database))
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    aggregate = payload["aggregate"]
    assert len(aggregate["records"]) == 3
    assert aggregate["documents_stored"] == 4
    assert aggregate["chunks_indexed"] > 0
    codes = {issue["code"] for issue in aggregate["quarantined"]}
    assert "post_tipoff_document" in codes
    assert "undated_document" in codes
    assert "source_not_whitelisted" in codes

    repositories = bootstrap(database)
    try:
        assert repositories.games.get_historical("401000001") is not None
        assert len(repositories.source_documents.list_for_game("401000001")) == 2
        run = repositories.research_runs.get(payload["run_id"])
        assert run is not None
        assert run["status"] == "completed"
    finally:
        repositories.close()

    exit_code = main(_base_args(database))
    captured = capsys.readouterr()
    assert exit_code == 0
    second = json.loads(captured.out)
    assert len(second["aggregate"]["records"]) == 3

    repositories = bootstrap(database)
    try:
        assert len(repositories.source_documents.list_for_game("401000001")) == 2
    finally:
        repositories.close()


def test_ingest_cli_limit_and_invalid_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "limited.db"
    args = [*_base_args(database), "--limit", "1"]
    exit_code = main(args)
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert [record["game_id"] for record in payload["aggregate"]["records"]] == [
        "401000001"
    ]

    exit_code = main([*_base_args(database), "--limit", "0"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--limit" in captured.err


def test_ingest_cli_context_provider_none_stores_no_documents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "none.db"
    exit_code = main([*_base_args(database), "--context-provider", "none"])
    captured = capsys.readouterr()
    assert exit_code == 0
    aggregate = json.loads(captured.out)["aggregate"]
    assert len(aggregate["records"]) == 3
    assert aggregate["documents_stored"] == 0
    assert aggregate["chunks_indexed"] == 0


def _tavily_payload() -> dict[str, object]:
    return {
        "request_id": "req-e2e",
        "response_time": 0.2,
        "results": [
            {
                "url": "https://www.espn.com/mens-college-basketball/story/_/id/1",
                "title": "Pregame preview",
                "raw_content": "Both teams are healthy; the underdog is on short rest.",
                "score": 0.87,
                "published_date": "2024-12-01",
            }
        ],
    }


def _seed_games(database: Path) -> None:
    provider = FixtureHistoricalStatsProvider()
    repositories = bootstrap(database)
    try:
        for game_id in provider.list_game_ids("2024-2025"):
            try:
                game = provider.get_game(game_id, "2024-2025")
            except Exception:
                # One fixture intentionally has no final score; the pipeline
                # quarantines it as a historical-stats error.
                continue
            repositories.games.save_historical(game)
    finally:
        repositories.close()


@respx.mock
def test_ingest_cli_tavily_replays_from_sqlite_cache(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    database = tmp_path / "tavily.db"
    _seed_games(database)
    route = respx.post(_TAVILY_URL).mock(
        return_value=httpx.Response(200, json=_tavily_payload())
    )
    args = [*_base_args(database), "--context-provider", "tavily"]

    exit_code = main(args)
    captured = capsys.readouterr()
    assert exit_code == 0
    first = json.loads(captured.out)["aggregate"]
    assert len(first["records"]) == 3
    assert first["documents_stored"] == 3
    # Three searched games, not four: the fixture game with no final score cannot
    # be resolved, so it is quarantined without spending a Tavily credit.
    assert route.call_count == 3

    exit_code = main(args)
    captured = capsys.readouterr()
    assert exit_code == 0
    second = json.loads(captured.out)["aggregate"]
    assert len(second["records"]) == 3
    assert second["documents_stored"] == 3
    assert route.call_count == 3

    repositories = bootstrap(database)
    try:
        assert repositories.search_cache.count_queries() == 3
        assert repositories.search_cache.total_credits() == 3.0
        cached = repositories.search_cache.list_queries(game_id="401000001")
        assert len(cached) == 1
        assert cached[0].provider == "tavily"
        assert cached[0].expires_at is None
        assert cached[0].result_count == 1
    finally:
        repositories.close()


def test_ingest_cli_tavily_requires_an_api_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "missing-key.db"
    exit_code = main([*_base_args(database), "--context-provider", "tavily"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "TAVILY_API_KEY" in captured.err
