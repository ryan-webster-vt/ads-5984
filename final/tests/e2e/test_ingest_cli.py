"""End-to-end CLI test for the Phase 4 ingestion command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from upset_profiler.cli import main
from upset_profiler.storage.repositories import bootstrap


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
