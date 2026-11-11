"""Tests for the administrative CLI scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from upset_profiler.cli import main


@pytest.mark.unit
def test_validate_config_succeeds_with_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_env = tmp_path / "missing.env"
    exit_code = main(["--env-file", str(missing_env), "validate-config", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert "storage" in payload
    assert "pipeline" in payload
    assert payload["storage"]["sqlite_path"].endswith("upset_profiler.db")


@pytest.mark.unit
def test_strict_validation_reports_missing_core_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_env = tmp_path / "missing.env"
    exit_code = main(["--env-file", str(missing_env), "validate-config", "--strict"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "strict configuration validation failed" in captured.err


@pytest.mark.unit
def test_unimplemented_command_exits_with_message(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["monitor"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "not implemented" in captured.err
