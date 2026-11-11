"""Unit tests for the fixture loaders."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from upset_profiler.errors import ConfigurationError
from upset_profiler.fixtures import (
    FIXTURE_ROOT_ENV,
    fixture_path,
    fixture_root,
    load_json,
    load_json_lines,
    load_model,
    load_text,
)
from upset_profiler.models import TeamRef


@pytest.fixture
def fixture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "fixtures"
    root.mkdir()
    monkeypatch.setenv(FIXTURE_ROOT_ENV, str(root))
    yield root


@pytest.mark.unit
def test_load_json_and_model(fixture_dir: Path) -> None:
    payload = {"team_id": "1", "name": "Duke", "conference": "ACC"}
    (fixture_dir / "team.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_json("team.json") == payload
    team = load_model(TeamRef, "team.json")
    assert team == TeamRef.model_validate(payload)


@pytest.mark.unit
def test_load_text_and_json_lines(fixture_dir: Path) -> None:
    (fixture_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (fixture_dir / "rows.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")

    assert load_text("notes.txt") == "hello"
    assert load_json_lines("rows.jsonl") == [{"a": 1}, {"a": 2}]


@pytest.mark.unit
def test_fixture_paths_cannot_escape_root(fixture_dir: Path) -> None:
    outside = fixture_dir.parent / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="escapes"):
        fixture_path("..", "outside.json")


@pytest.mark.unit
def test_missing_fixture_raises(fixture_dir: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_json("missing.json")


@pytest.mark.unit
def test_missing_fixture_root_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FIXTURE_ROOT_ENV, str(tmp_path / "missing"))
    with pytest.raises(ConfigurationError, match="fixture root"):
        fixture_root()


@pytest.mark.unit
def test_invalid_json_raises(fixture_dir: Path) -> None:
    (fixture_dir / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="JSON fixture"):
        load_json("bad.json")
