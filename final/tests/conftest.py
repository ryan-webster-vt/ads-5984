"""Shared pytest fixtures.

The default environment is scrubbed of application variables so tests never
depend on a developer's real ``.env`` file or shell. Tests that need specific
configuration set variables through ``monkeypatch`` or write a temporary env
file with the ``write_env`` fixture.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from upset_profiler.config import PROJECT_ROOT, Settings, reset_settings_cache

_ENV_KEYS = (
    "OPEN_AI_ENDPOINT",
    "OPEN_AI_API_KEY",
    "OPEN_AI_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT_SECONDS",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_BATCH_SIZE",
    "DECISION_MODEL_BASE_URL",
    "DECISION_MODEL_API_KEY",
    "DECISION_MODEL_NAME",
    "DECISION_MODEL_TIMEOUT_SECONDS",
    "ESPN_BASE_URL",
    "ESPN_TIMEOUT_SECONDS",
    "CBB_API_PROVIDER",
    "CBB_API_BASE_URL",
    "CBB_API_KEY",
    "MCP_BRAVE_TRANSPORT",
    "MCP_BRAVE_COMMAND",
    "MCP_BRAVE_URL",
    "BRAVE_API_KEY",
    "MCP_FETCH_COMMAND",
    "MCP_FETCH_URL",
    "MCP_DEEP_RESEARCH_COMMAND",
    "MCP_DEEP_RESEARCH_URL",
    "MCP_TIMEOUT_SECONDS",
    "TAVILY_API_KEY",
    "TAVILY_BASE_URL",
    "TAVILY_SEARCH_DEPTH",
    "TAVILY_TOPIC",
    "TAVILY_MAX_RESULTS",
    "TAVILY_INCLUDE_RAW_CONTENT",
    "TAVILY_KEYLESS",
    "TAVILY_TIMEOUT_SECONDS",
    "SQLITE_PATH",
    "CHROMA_PATH",
    "CHROMA_COLLECTION",
    "CACHE_TTL_SECONDS",
    "RESEARCH_BATCH_SIZE",
    "MONITOR_WORKER_COUNT",
    "MONITOR_MAX_CONCURRENCY",
    "RAG_TOP_K",
    "RERANK_TOP_N",
    "CHUNK_SIZE_TOKENS",
    "CHUNK_OVERLAP_TOKENS",
    "SOURCE_WHITELIST_PATH",
    "LEAKAGE_STRICT",
    "PRECONDITION_ALPHA",
    "FDR_ALPHA",
    "MIN_SAMPLE_SIZE",
    "TRAIN_SEASONS",
    "VALIDATION_SEASON",
    "TEST_SEASON",
    "APP_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "UPSET_PROFILER_FIXTURE_ROOT",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove application environment variables around every test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # The CLI's ``--env-file`` default is the project ``.env``. Point that default
    # at a path that cannot exist so a developer's real configuration (for
    # example a live ``TAVILY_API_KEY``) never changes a test outcome. Tests that
    # want a file pass ``--env-file`` or ``Settings.load(env_file=...)``.
    monkeypatch.setattr(
        "upset_profiler.cli.DEFAULT_ENV_FILE",
        Path("/nonexistent/upset-profiler-tests.env"),
    )
    reset_settings_cache()
    try:
        yield
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)
        reset_settings_cache()


@pytest.fixture
def project_root() -> Path:
    """Return the repository root."""
    return PROJECT_ROOT


@pytest.fixture
def settings() -> Settings:
    """Return settings with ``.env`` loading disabled."""
    return Settings.load(env_file=None)


@pytest.fixture
def write_env(tmp_path: Path) -> Callable[[dict[str, str]], Path]:
    """Return a helper that writes a temporary ``.env`` file."""

    def _write(values: dict[str, str]) -> Path:
        path = tmp_path / ".env"
        lines = [f"{key}={value}" for key, value in values.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return _write
