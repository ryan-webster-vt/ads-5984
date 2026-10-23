"""Application configuration and filesystem paths for the project."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# All paths are anchored to the project root so the app behaves the same no
# matter which directory the Streamlit process or CLI is launched from.
BASE_DIR = Path(__file__).resolve().parent
RAW_DATA_DIR = BASE_DIR / "raw_data"  # Read-only faculty/publication source CSVs.
DATA_DIR = BASE_DIR / "data"  # Generated stores and caches; safe to delete.
FIXTURES_DIR = BASE_DIR / "fixtures"
PROMPTS_DIR = BASE_DIR / "prompts"
CHROMA_DIR = DATA_DIR / "chroma"
INDEX_MANIFEST_PATH = DATA_DIR / "index_manifest.json"
OPPORTUNITIES_PATH = DATA_DIR / "opportunities.json"
OPPORTUNITIES_BACKUP_PATH = DATA_DIR / "opportunities.json.bak"
OPPORTUNITIES_LOCK_PATH = DATA_DIR / ".opportunities.lock"
DETAIL_CACHE_DIR = DATA_DIR / "grants_detail_cache"
RETRIEVAL_CACHE_DIR = DATA_DIR / "retrieval_cache"
ELSEVIER_CACHE_DIR = DATA_DIR / "elsevier_cache"
RUNS_DIR = DATA_DIR / "runs"

# Version identifiers are persisted in the manifest so a stale index is rejected
# instead of being silently mixed with a newer corpus or embedding pipeline.
APP_VERSION = "0.6.0"
INDEX_VERSION = "full-v1"
CHUNKER_VERSION = "1"
SCHEMA_VERSION = "2"
PROFILE_VERSION = "1"
COLLECTION_NAME = "research_full_v1"
DEFAULT_INDEX_SOURCE = "csv"  # CSV is the reproducible, fully verified path.
DEFAULT_INDEX_SCOPE = "full"
PUBLICATION_SOURCES = ("csv", "elsevier")

# Elsevier is optional and key-gated. The per-author cap defaults to unlimited
# but can be reduced on the CLI to conserve monthly quota during previews.
ELSEVIER_SEARCH_PAGE_SIZE = 25
ELSEVIER_MAX_RESULTS_PER_FACULTY: int | None = None
ELSEVIER_FETCH_ABSTRACTS = True
ELSEVIER_CACHE_TTL_HOURS = 168.0  # Abstracts rarely change; reuse for one week.
ELSEVIER_HTTP_TIMEOUT_SECONDS = 30
ELSEVIER_MAX_RETRIES = 3
ELSEVIER_RETRY_BASE_SECONDS = 1.0
ELSEVIER_RETRY_MAX_SECONDS = 30.0

# Grants.gov feed and detail-fetch limits. The feed count is authoritative for
# archiving; a page failure must never trigger archive logic.
OPPORTUNITY_SCHEMA_VERSION = "1"
OPPORTUNITY_OPEN_STATUSES = ("posted", "forecasted")
GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"
GRANTS_FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
GRANTS_SEARCH_ROWS = 1000  # Largest page size the public API accepts reliably.
GRANTS_DETAIL_CACHE_TTL_HOURS = 24
GRANTS_HTTP_TIMEOUT_SECONDS = 30
GRANTS_MAX_RETRIES = 2
GRANTS_MAX_WORKERS = 5  # Concurrent detail fetches; keeps the refresh polite.
GRANTS_MAX_PAGES = 50
OPPORTUNITY_PAGE_SIZE = 25
OPPORTUNITY_PAGE_SIZE_OPTIONS = (10, 25, 50, 100)

# Team-building defaults and hard limits. Keeping retrieval and generation caps
# in one place makes the per-run latency and cost budget auditable.
DEFAULT_TEAM_SIZE = 4
MIN_TEAM_SIZE = 3
MAX_TEAM_SIZE = 5
DEFAULT_K_IN = 30  # Candidates retrieved per optimized query.
DEFAULT_K_OUT = 12  # Evidence kept after reranking and diversity filtering.
RETRIEVAL_UNION_CAP = 60  # Upper bound on unique evidence considered per run.
RERANK_BATCH_SIZE = 8
MAX_RERANK_CALLS = 4
MAX_SHORTLIST = 8
MAX_LLM_MEMBERS = 4
MAX_CONVERSATION_TURNS = 8
RECENCY_HALF_LIFE_YEARS = 5.0  # Exponential decay for publication recency.

# Weights are normalized by the scorer. Missing author metrics drop their weight
# and redistribute it rather than being imputed with invented values.
SCORING_WEIGHTS = {
    "relevance": 0.45,
    "coverage": 0.20,
    "metrics": 0.15,
    "recency": 0.10,
    "collaboration": 0.05,
    "diversity": 0.05,
}

PROMPT_VERSION = "2"
PROMPT_NAMES = (
    "optimize_query",
    "rerank",
    "generate_team",
    "verify_team",
    "parse_followup",
)

# Chunking settings balance evidence granularity against duplicate recall.
CHUNK_MAX_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150
MAX_ABSTRACT_CHUNKS = 5

# Batches keep embedding and Chroma calls below provider request-size limits.
EMBED_BATCH_SIZE = 64
CHROMA_BATCH_SIZE = 64

# LLM reliability guardrails: retries handle transient provider failures while
# the per-run budget and circuit breaker stop a slow endpoint from hanging the UI.
# A follow-up prompt generated right after an initial team build can be slow on
# VT ARC. The limits are deliberately generous because raising only the request
# timeout is ineffective when the wall budget caps every call at the remaining
# time. Request timeout 600 s; per-run wall budget 1,800 s.
LLM_TIMEOUT_SECONDS = 600
EMBED_TIMEOUT_SECONDS = 90
LLM_MAX_RETRIES = 3
# Reasoning models (GLM-5.3, gpt-oss-120b) spend completion tokens on hidden
# thinking before emitting JSON; a live run exhausted 8,000 tokens on reasoning
# alone, so the per-request cap must leave room for thinking plus the payload.
LLM_MAX_TOKENS = 16_000
# The VT ARC proxy rejects non-streaming requests above this buffered-mode cap
# (HTTP 413), so every buffered request is clamped and longer generations use
# the streaming fallback in src/llm.py.
LLM_BUFFERED_MAX_TOKENS = 8_000
# Streaming requests may run longer; this is the hard ceiling for one call.
LLM_MAX_TOKENS_STREAMED = 32_000
LLM_RETRY_BASE_SECONDS = 1.0
LLM_RETRY_MAX_SECONDS = 20.0
LLM_RETRY_JITTER_SECONDS = 0.5
LLM_CIRCUIT_FAILURE_THRESHOLD = 3
LLM_CIRCUIT_COOLDOWN_SECONDS = 60.0
# A length-exhausted generate stage can legitimately spend several attempts
# (buffered + streamed + JSON-object fallback); the cap must leave room for
# optimize + rerank + generate + verify + one repair within one run.
LLM_MAX_CALLS_PER_RUN = 16
LLM_MAX_TOKENS_PER_RUN = 200_000
LLM_MAX_WALL_SECONDS = 1800.0

# The cache stores exact evidence lists, not generated prose, so a hit cannot
# bypass the deterministic scorer or validator.
RETRIEVAL_CACHE_ENABLED = True
RETRIEVAL_CACHE_TTL_HOURS = 24.0
RETRIEVAL_CACHE_VERSION = "1"

# Suggested operational targets from PLAN.md. Actual observations are recorded
# by scripts/eval_repeatability.py; these are reporting thresholds, not promises.
RUNTIME_BUDGETS = {
    "cached_refresh_seconds": 30.0,
    "cold_refresh_seconds": 600.0,
    "team_build_p95_ms": 90_000,
    "max_llm_calls": 8,
}

# Load keys from the project .env without overriding variables already exported
# by the shell. Values are never logged or included in generated artifacts.
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str:
    """Return a required, non-blank environment value or fail with a safe name."""
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def optional_env(name: str, default: str | None = None) -> str | None:
    """Return a configured value, treating blank strings as absent."""
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


# Per-stage generation models. Each pipeline stage can pin its own VT ARC model
# via OPEN_AI_<STAGE>_MODEL (for example OPEN_AI_RERANK_MODEL); stages without an
# override use OPEN_AI_MODEL so a single-model deployment is unchanged.
LLM_STAGES = ("optimize", "rerank", "generate", "verify", "followup")


def stage_model(stage: str) -> str | None:
    """Return the configured model for one LLM stage, or None for the default."""
    if stage not in LLM_STAGES:
        allowed = ", ".join(LLM_STAGES)
        raise ValueError(f"unknown LLM stage {stage!r}; expected one of {allowed}")
    return optional_env(f"OPEN_AI_{stage.upper()}_MODEL")


def stage_models(default_model: str | None = None) -> dict[str, str]:
    """Resolve every stage to its effective model for run metadata."""
    # Unset stages inherit the client default so metadata matches what ran.
    return {
        stage: stage_model(stage) or (default_model or "unavailable")
        for stage in LLM_STAGES
    }


def configured_publication_source() -> str:
    """Return the validated publication source (CSV stays the default)."""
    # Normalize before validation so "Elsevier" does not produce a confusing miss.
    source = (optional_env("PUBLICATION_SOURCE") or DEFAULT_INDEX_SOURCE).lower()
    if source not in PUBLICATION_SOURCES:
        allowed = ", ".join(PUBLICATION_SOURCES)
        raise RuntimeError(
            f"PUBLICATION_SOURCE must be one of {allowed}; got {source!r}"
        )
    return source


def ensure_directories() -> None:
    """Create generated-data directories before any writer needs them."""
    for path in (
        DATA_DIR,
        CHROMA_DIR,
        DETAIL_CACHE_DIR,
        RETRIEVAL_CACHE_DIR,
        ELSEVIER_CACHE_DIR,
        RUNS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
