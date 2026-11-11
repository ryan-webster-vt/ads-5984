"""Typed application settings for the Upset Condition Profiler.

Settings are grouped by concern and loaded from environment variables and an
optional ``.env`` file. Secret values use :class:`pydantic.SecretStr` so they
are never rendered accidentally in logs or diagnostic output.

This module performs no network access and holds no agent or pipeline logic.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

_SEASON_PATTERN = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})$")


class _EnvSettings(BaseSettings):
    """Base class for the environment-backed settings groups."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


class LLMSettings(_EnvSettings):
    """VT ARC / OpenAI-compatible chat model configuration."""

    open_ai_endpoint: str | None = None
    open_ai_api_key: SecretStr | None = None
    open_ai_model: str | None = None

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0.0)

    @property
    def base_url(self) -> str | None:
        """Override URL if provided, otherwise fall back to the VT ARC value."""
        return self.llm_base_url or self.open_ai_endpoint

    @property
    def api_key(self) -> SecretStr | None:
        """Override key if provided, otherwise fall back to the VT ARC value."""
        return self.llm_api_key or self.open_ai_api_key

    @property
    def model(self) -> str | None:
        """Override model if provided, otherwise fall back to the VT ARC value."""
        return self.llm_model or self.open_ai_model


class EmbeddingSettings(_EnvSettings):
    """Embedding endpoint configuration (default model: qwen3-embedding:8b)."""

    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "qwen3-embedding:8b"
    embedding_batch_size: int = Field(default=32, ge=1)


class DecisionModelSettings(_EnvSettings):
    """Decision-model configuration (default model: d3-mini)."""

    decision_model_base_url: str | None = None
    decision_model_api_key: SecretStr | None = None
    decision_model_name: str = "d3-mini"
    decision_model_timeout_seconds: float = Field(default=60.0, gt=0.0)


class ESPNSettings(_EnvSettings):
    """ESPN daily slate and matchup endpoints."""

    espn_base_url: str = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
    )
    espn_timeout_seconds: float = Field(default=30.0, gt=0.0)


class CollegeBasketballSettings(_EnvSettings):
    """Historical sports API provider configuration."""

    cbb_api_provider: str | None = None
    cbb_api_base_url: str | None = None
    cbb_api_key: SecretStr | None = None


class MCPSettings(_EnvSettings):
    """MCP server configuration for Brave Search, Fetch, and Deep Research."""

    mcp_brave_transport: Literal["stdio", "http", "sse"] = "stdio"
    mcp_brave_command: str | None = None
    mcp_brave_url: str | None = None
    brave_api_key: SecretStr | None = None
    mcp_fetch_command: str | None = None
    mcp_fetch_url: str | None = None
    mcp_deep_research_command: str | None = None
    mcp_deep_research_url: str | None = None
    mcp_timeout_seconds: float = Field(default=120.0, gt=0.0)


class TavilySettings(_EnvSettings):
    """Tavily Context-Research search backend configuration (ADR-006).

    Tavily is the default web-search backend: one ``/search`` call can return
    result text (``include_raw_content``) and a publication estimate
    (``include_published_date``), replacing a search-plus-fetch fanout. The
    keyless mode works without an account but cannot report credit usage.
    """

    tavily_api_key: SecretStr | None = None
    tavily_base_url: str = "https://api.tavily.com"
    tavily_search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    tavily_topic: Literal["general", "news"] = "general"
    tavily_max_results: int = Field(default=5, ge=1, le=20)
    tavily_include_raw_content: bool = True
    tavily_keyless: bool = False
    tavily_timeout_seconds: float = Field(default=30.0, gt=0.0)

    @field_validator("tavily_base_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class StorageSettings(_EnvSettings):
    """SQLite and ChromaDB storage locations."""

    sqlite_path: Path = Path("./data/db/upset_profiler.db")
    chroma_path: Path = Path("./data/chroma")
    chroma_collection: str = Field(default="research_documents", min_length=1)
    cache_ttl_seconds: int = Field(default=86_400, ge=0)

    @field_validator("sqlite_path", "chroma_path", mode="after")
    @classmethod
    def _resolve_against_project_root(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


class PipelineSettings(_EnvSettings):
    """Pipeline controls for batching, concurrency, retrieval, and chunking."""

    research_batch_size: int = Field(default=10, ge=1)
    monitor_worker_count: int = Field(default=2, ge=1)
    monitor_max_concurrency: int = Field(default=4, ge=1)
    rag_top_k: int = Field(default=20, ge=1)
    rerank_top_n: int = Field(default=8, ge=1)
    chunk_size_tokens: int = Field(default=800, ge=1)
    chunk_overlap_tokens: int = Field(default=120, ge=0)
    source_whitelist_path: Path = Path("./config/sources.yaml")
    leakage_strict: bool = True

    @field_validator("source_whitelist_path", mode="after")
    @classmethod
    def _resolve_whitelist_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("chunk_overlap_tokens", mode="after")
    @classmethod
    def _overlap_must_be_smaller_than_chunk(cls, value: int, info: Any) -> int:
        chunk_size = info.data.get("chunk_size_tokens")
        if chunk_size is not None and value >= chunk_size:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_SIZE_TOKENS")
        return value

    @field_validator("rerank_top_n", mode="after")
    @classmethod
    def _rerank_cannot_exceed_top_k(cls, value: int, info: Any) -> int:
        top_k = info.data.get("rag_top_k")
        if top_k is not None and value > top_k:
            raise ValueError("RERANK_TOP_N must not exceed RAG_TOP_K")
        return value


class StatsSettings(_EnvSettings):
    """Statistical validation thresholds and season splits."""

    precondition_alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    fdr_alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    min_sample_size: int = Field(default=30, ge=2)
    train_seasons: str = "2021-2022,2022-2023,2023-2024"
    validation_season: str = "2024-2025"
    test_season: str = "2025-2026"

    @property
    def train_season_list(self) -> tuple[str, ...]:
        """Return the comma-separated training seasons as a tuple."""
        return tuple(part.strip() for part in self.train_seasons.split(",") if part.strip())

    @field_validator("validation_season", "test_season")
    @classmethod
    def _validate_season(cls, value: str) -> str:
        return _validate_season_string(value)

    @field_validator("train_seasons", mode="after")
    @classmethod
    def _validate_training_seasons(cls, value: str) -> str:
        seasons = [part.strip() for part in value.split(",") if part.strip()]
        if not seasons:
            raise ValueError("TRAIN_SEASONS must contain at least one season")
        for season in seasons:
            _validate_season_string(season)
        return ",".join(seasons)


class LoggingSettings(_EnvSettings):
    """Application logging configuration."""

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"


def _validate_season_string(value: str) -> str:
    match = _SEASON_PATTERN.match(value.strip())
    if match is None:
        raise ValueError(f"invalid season {value!r}; expected a value like '2024-2025'")
    start, end = int(match.group("start")), int(match.group("end"))
    if end != start + 1:
        raise ValueError(f"invalid season {value!r}; end year must be start year + 1")
    return value.strip()


def _redact(value: Any) -> Any:
    """Recursively replace secret values with a placeholder."""
    if isinstance(value, SecretStr):
        return "**********"
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class Settings(BaseModel):
    """Aggregate, typed application settings."""

    llm: LLMSettings = Field(default_factory=LLMSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    decision_model: DecisionModelSettings = Field(default_factory=DecisionModelSettings)
    espn: ESPNSettings = Field(default_factory=ESPNSettings)
    college_basketball: CollegeBasketballSettings = Field(
        default_factory=CollegeBasketballSettings
    )
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    tavily: TavilySettings = Field(default_factory=TavilySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    stats: StatsSettings = Field(default_factory=StatsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def load(cls, env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
        """Build settings, optionally reading a specific ``.env`` file.

        Passing ``env_file=None`` disables ``.env`` loading, which keeps unit
        tests hermetic while still honoring real environment variables.
        """
        return cls(
            llm=LLMSettings(_env_file=env_file),
            embeddings=EmbeddingSettings(_env_file=env_file),
            decision_model=DecisionModelSettings(_env_file=env_file),
            espn=ESPNSettings(_env_file=env_file),
            college_basketball=CollegeBasketballSettings(_env_file=env_file),
            mcp=MCPSettings(_env_file=env_file),
            tavily=TavilySettings(_env_file=env_file),
            storage=StorageSettings(_env_file=env_file),
            pipeline=PipelineSettings(_env_file=env_file),
            stats=StatsSettings(_env_file=env_file),
            logging=LoggingSettings(_env_file=env_file),
        )

    def safe_summary(self) -> dict[str, Any]:
        """Return a serializable settings view with all secrets redacted."""
        dumped = self.model_dump(mode="python")
        redacted = _redact(dumped)
        assert isinstance(redacted, dict)
        return redacted


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings for the current process."""
    return Settings.load()


def reset_settings_cache() -> None:
    """Clear the cached settings, primarily for tests and config reloads."""
    get_settings.cache_clear()
