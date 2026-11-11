"""Tests for run-context provenance helpers."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from upset_profiler.context import RunContext, utc_now


@pytest.mark.unit
def test_run_context_generates_unique_ids() -> None:
    first = RunContext.create(actor="cli")
    second = RunContext.create(actor="cli")
    assert first.run_id != second.run_id
    assert first.actor == "cli"


@pytest.mark.unit
def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is UTC


@pytest.mark.unit
def test_child_preserves_run_id_and_merges_metadata() -> None:
    parent = RunContext.create(actor="orchestrator", metadata={"season": "2024-2025"})
    child = parent.child(actor="monitor-worker", metadata={"game_id": "401234"})
    assert child.run_id == parent.run_id
    assert child.actor == "monitor-worker"
    assert child.metadata == {"season": "2024-2025", "game_id": "401234"}


@pytest.mark.unit
def test_run_context_is_frozen() -> None:
    context = RunContext.create()
    with pytest.raises(ValidationError):
        context.actor = "other"  # type: ignore[misc]
