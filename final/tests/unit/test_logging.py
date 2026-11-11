"""Tests for structured logging and redaction."""

from __future__ import annotations

import io
import json
import re

import pytest

from upset_profiler.config import Settings
from upset_profiler.logging_config import get_logger, setup_logging


@pytest.mark.unit
def test_json_logging_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_AI_API_KEY", "super-secret-value")
    monkeypatch.setenv("LOG_FORMAT", "json")
    settings = Settings.load(env_file=None)
    stream = io.StringIO()

    setup_logging(settings, stream=stream, force=True)
    get_logger("upset_profiler.tests").info(
        "key is super-secret-value, api_key=abc123, token=xyz, Bearer deadbeef"
    )

    output = stream.getvalue().strip()
    assert output, "expected at least one log line"
    assert "super-secret-value" not in output
    assert "abc123" not in output
    assert "deadbeef" not in output

    payload = json.loads(output.splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "upset_profiler.tests"
    assert payload["message"].count("**********") >= 2


@pytest.mark.unit
def test_console_logging_uses_expected_format(settings: Settings) -> None:
    stream = io.StringIO()
    setup_logging(settings, stream=stream, force=True)
    get_logger("upset_profiler.tests").warning("console message")
    output = stream.getvalue()
    assert re.search(r"WARNING\s+upset_profiler\.tests: console message", output)
