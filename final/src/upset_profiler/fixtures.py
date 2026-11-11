"""Deterministic fixture loading for offline tests and demos.

Fixtures live under ``tests/fixtures`` by default. Set
``UPSET_PROFILER_FIXTURE_ROOT`` to point the loader at an alternate directory.
All paths are resolved and confined to the fixture root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from upset_profiler.config import PROJECT_ROOT
from upset_profiler.errors import ConfigurationError

FIXTURE_ROOT_ENV = "UPSET_PROFILER_FIXTURE_ROOT"
DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"

ModelT = TypeVar("ModelT", bound=BaseModel)


def fixture_root() -> Path:
    """Return the resolved fixture root directory."""
    configured = os.environ.get(FIXTURE_ROOT_ENV)
    root = Path(configured).expanduser().resolve() if configured else DEFAULT_FIXTURE_ROOT.resolve()
    if not root.is_dir():
        raise ConfigurationError(
            "fixture root does not exist",
            context={"root": str(root), "env": FIXTURE_ROOT_ENV},
        )
    return root


def fixture_path(*parts: str) -> Path:
    """Resolve a fixture path and ensure it stays inside the fixture root."""
    if not parts:
        raise ConfigurationError("fixture path requires at least one path segment")
    root = fixture_root()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            "fixture path escapes the fixture root",
            context={"root": str(root), "path": str(candidate)},
        ) from exc
    if not candidate.is_file():
        raise ConfigurationError(
            "fixture file does not exist",
            context={"path": str(candidate)},
        )
    return candidate


def load_text(*parts: str) -> str:
    """Load a fixture as UTF-8 text."""
    path = fixture_path(*parts)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError("failed to read fixture", context={"path": str(path)}) from exc


def load_json(*parts: str) -> Any:
    """Load a fixture as decoded JSON."""
    path = fixture_path(*parts)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            "failed to load JSON fixture",
            context={"path": str(path)},
        ) from exc


def load_json_lines(*parts: str) -> list[Any]:
    """Load a newline-delimited JSON fixture, ignoring blank lines."""
    lines = [line for line in load_text(*parts).splitlines() if line.strip()]
    try:
        return [json.loads(line) for line in lines]
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "failed to load JSON-lines fixture",
            context={"path": str(fixture_path(*parts))},
        ) from exc


def load_model(model: type[ModelT], *parts: str) -> ModelT:
    """Load a JSON fixture and validate it as a Pydantic contract."""
    return model.model_validate(load_json(*parts))
