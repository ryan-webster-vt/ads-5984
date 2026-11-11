"""Package smoke tests."""

from __future__ import annotations

import importlib

import pytest

import upset_profiler


@pytest.mark.unit
def test_version_is_declared() -> None:
    assert upset_profiler.__version__ == "0.1.0"


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "upset_profiler.config",
        "upset_profiler.context",
        "upset_profiler.errors",
        "upset_profiler.logging_config",
        "upset_profiler.cli",
        "upset_profiler.agents",
        "upset_profiler.orchestration",
        "upset_profiler.orchestration.pipelines",
        "upset_profiler.services",
        "upset_profiler.models",
        "upset_profiler.storage",
        "upset_profiler.integrations",
        "upset_profiler.rag",
        "upset_profiler.security",
        "upset_profiler.stats",
        "upset_profiler.ui",
        "upset_profiler.ui.components",
    ],
)
def test_modules_import(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
