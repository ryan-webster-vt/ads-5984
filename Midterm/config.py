"""Application configuration and filesystem paths for the project."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
RAW_DATA_DIR = BASE_DIR / "raw_data"
DATA_DIR = BASE_DIR / "data"
FIXTURES_DIR = BASE_DIR / "fixtures"
PROMPTS_DIR = BASE_DIR / "prompts"
CHROMA_DIR = DATA_DIR / "chroma"
INDEX_MANIFEST_PATH = DATA_DIR / "index_manifest.json"
OPPORTUNITIES_PATH = DATA_DIR / "opportunities.json"
OPPORTUNITIES_BACKUP_PATH = DATA_DIR / "opportunities.json.bak"
OPPORTUNITIES_LOCK_PATH = DATA_DIR / ".opportunities.lock"
DETAIL_CACHE_DIR = DATA_DIR / "grants_detail_cache"
RUNS_DIR = DATA_DIR / "runs"

APP_VERSION = "0.3.0"
INDEX_VERSION = "full-v1"
CHUNKER_VERSION = "1"
SCHEMA_VERSION = "2"
PROFILE_VERSION = "1"
COLLECTION_NAME = "research_full_v1"
DEFAULT_INDEX_SOURCE = "csv"
DEFAULT_INDEX_SCOPE = "full"

OPPORTUNITY_SCHEMA_VERSION = "1"
OPPORTUNITY_OPEN_STATUSES = ("posted", "forecasted")
GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"
GRANTS_FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
GRANTS_SEARCH_ROWS = 1000
GRANTS_DETAIL_CACHE_TTL_HOURS = 24
GRANTS_HTTP_TIMEOUT_SECONDS = 30
GRANTS_MAX_RETRIES = 2
GRANTS_MAX_WORKERS = 5
GRANTS_MAX_PAGES = 50
OPPORTUNITY_PAGE_SIZE = 25
OPPORTUNITY_PAGE_SIZE_OPTIONS = (10, 25, 50, 100)

DEFAULT_TEAM_SIZE = 3
DEFAULT_K_IN = 30
DEFAULT_K_OUT = 12
MAX_LLM_MEMBERS = 4

CHUNK_MAX_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150
MAX_ABSTRACT_CHUNKS = 5

EMBED_BATCH_SIZE = 64
CHROMA_BATCH_SIZE = 64

LLM_TIMEOUT_SECONDS = 90
EMBED_TIMEOUT_SECONDS = 90
LLM_MAX_RETRIES = 3
LLM_MAX_TOKENS = 4096

load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def optional_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def ensure_directories() -> None:
    for path in (DATA_DIR, CHROMA_DIR, DETAIL_CACHE_DIR, RUNS_DIR):
        path.mkdir(parents=True, exist_ok=True)
