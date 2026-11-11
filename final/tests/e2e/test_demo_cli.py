"""End-to-end tests for the fixture-backed demo CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from upset_profiler.cli import main


def _stable(payload: dict[str, object]) -> list[tuple[object, ...]]:
    scores = payload["scores"]
    assert isinstance(scores, list)
    return [
        (
            score["game_id"],
            score["score"],
            score["rank"],
            score["rationale"],
            [citation["citation_id"] for citation in score["citations"]],
        )
        for score in scores
    ]


@pytest.mark.e2e
def test_demo_cli_emits_ranked_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo", "--date", "2025-01-15"])
    captured = capsys.readouterr()
    assert exit_code == 0

    payload = json.loads(captured.out)
    assert payload["model_name"] == "d3-mini"
    assert [score["game_id"] for score in payload["scores"]] == [
        "401000001",
        "401000003",
    ]
    assert [score["rank"] for score in payload["scores"]] == [1, 2]
    assert all(score["citations"] for score in payload["scores"])


@pytest.mark.e2e
def test_demo_cli_is_stable_across_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "--date", "2025-01-15", "--workers", "2"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["demo", "--date", "2025-01-15", "--workers", "1"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert _stable(first) == _stable(second)


@pytest.mark.e2e
def test_demo_cli_selected_non_matching_game(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo", "--date", "2025-01-15", "--game-id", "401000002"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["scores"] == []


@pytest.mark.e2e
def test_demo_cli_writes_output_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "ranking.json"
    exit_code = main(["demo", "--date", "2025-01-15", "--output", str(output)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(captured.out)


@pytest.mark.e2e
def test_demo_cli_progress_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo", "--date", "2025-01-15", "--progress"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["scores"]
    assert "monitor-worker" in captured.err


@pytest.mark.e2e
def test_demo_cli_rejects_unknown_game(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo", "--game-id", "does-not-exist"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not on the slate" in captured.err
