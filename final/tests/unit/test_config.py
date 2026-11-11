"""Tests for typed settings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from upset_profiler.config import PROJECT_ROOT, Settings


@pytest.mark.unit
def test_defaults_are_typed_and_resolved(settings: Settings) -> None:
    assert settings.pipeline.monitor_worker_count == 2
    assert settings.embeddings.embedding_model == "qwen3-embedding:8b"
    assert settings.decision_model.decision_model_name == "d3-mini"
    assert settings.storage.sqlite_path.is_absolute()
    assert settings.storage.sqlite_path == (PROJECT_ROOT / "data/db/upset_profiler.db").resolve()
    assert settings.pipeline.source_whitelist_path.is_absolute()


@pytest.mark.unit
def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_AI_MODEL", "vt-arc-llm")
    monkeypatch.setenv("MONITOR_WORKER_COUNT", "4")
    monkeypatch.setenv("LEAKAGE_STRICT", "false")
    settings = Settings.load(env_file=None)
    assert settings.llm.model == "vt-arc-llm"
    assert settings.pipeline.monitor_worker_count == 4
    assert settings.pipeline.leakage_strict is False


@pytest.mark.unit
def test_llm_override_falls_back_to_vt_arc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_AI_ENDPOINT", "https://arc.example/v1")
    monkeypatch.setenv("OPEN_AI_MODEL", "vt-arc-llm")
    monkeypatch.setenv("LLM_MODEL", "override-model")
    settings = Settings.load(env_file=None)
    assert settings.llm.base_url == "https://arc.example/v1"
    assert settings.llm.model == "override-model"


@pytest.mark.unit
def test_safe_summary_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_AI_API_KEY", "super-secret-value")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret-value")
    settings = Settings.load(env_file=None)
    summary = settings.safe_summary()
    assert summary["llm"]["open_ai_api_key"] == "**********"
    assert summary["mcp"]["brave_api_key"] == "**********"
    assert "super-secret-value" not in str(summary)
    assert "brave-secret-value" not in str(summary)


@pytest.mark.unit
def test_env_file_is_loaded(write_env: Callable[[dict[str, str]], Path]) -> None:
    env_file = write_env(
        {
            "OPEN_AI_MODEL": "from-env-file",
            "RAG_TOP_K": "12",
            "RERANK_TOP_N": "5",
        }
    )
    settings = Settings.load(env_file=env_file)
    assert settings.llm.model == "from-env-file"
    assert settings.pipeline.rag_top_k == 12
    assert settings.pipeline.rerank_top_n == 5


@pytest.mark.unit
def test_chunk_overlap_must_be_smaller_than_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "100")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "100")
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
        Settings.load(env_file=None)


@pytest.mark.unit
def test_rerank_top_n_cannot_exceed_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TOP_K", "5")
    monkeypatch.setenv("RERANK_TOP_N", "10")
    with pytest.raises(ValidationError, match="RERANK_TOP_N"):
        Settings.load(env_file=None)


@pytest.mark.unit
@pytest.mark.parametrize("bad_season", ["2024-26", "2024/2025", "2024", "abcd-efgh"])
def test_invalid_season_is_rejected(monkeypatch: pytest.MonkeyPatch, bad_season: str) -> None:
    monkeypatch.setenv("VALIDATION_SEASON", bad_season)
    with pytest.raises(ValidationError):
        Settings.load(env_file=None)


@pytest.mark.unit
def test_training_seasons_are_parsed(settings: Settings) -> None:
    assert settings.stats.train_season_list == ("2021-2022", "2022-2023", "2023-2024")


@pytest.mark.unit
def test_tavily_defaults_and_overrides(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = settings.tavily
    assert defaults.tavily_base_url == "https://api.tavily.com"
    assert defaults.tavily_search_depth == "basic"
    assert defaults.tavily_topic == "general"
    assert defaults.tavily_max_results == 5
    assert defaults.tavily_include_raw_content is True
    assert defaults.tavily_keyless is False
    assert defaults.tavily_api_key is None

    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("TAVILY_BASE_URL", "https://tavily.example/v1/")
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "advanced")
    monkeypatch.setenv("TAVILY_TOPIC", "news")
    monkeypatch.setenv("TAVILY_MAX_RESULTS", "8")
    monkeypatch.setenv("TAVILY_INCLUDE_RAW_CONTENT", "false")
    monkeypatch.setenv("TAVILY_KEYLESS", "true")
    overridden = Settings.load(env_file=None).tavily
    assert overridden.tavily_base_url == "https://tavily.example/v1"
    assert overridden.tavily_search_depth == "advanced"
    assert overridden.tavily_topic == "news"
    assert overridden.tavily_max_results == 8
    assert overridden.tavily_include_raw_content is False
    assert overridden.tavily_keyless is True
    assert overridden.tavily_api_key is not None


@pytest.mark.unit
def test_tavily_limits_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_MAX_RESULTS", "21")
    with pytest.raises(ValidationError, match="tavily_max_results"):
        Settings.load(env_file=None)
    monkeypatch.setenv("TAVILY_MAX_RESULTS", "5")
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "thorough")
    with pytest.raises(ValidationError, match="tavily_search_depth"):
        Settings.load(env_file=None)


@pytest.mark.unit
def test_tavily_secret_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret-value")
    settings = Settings.load(env_file=None)
    summary = settings.safe_summary()
    assert summary["tavily"]["tavily_api_key"] == "**********"
    assert "tavily-secret-value" not in str(summary)
