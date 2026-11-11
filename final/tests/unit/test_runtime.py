"""Tests for the isolated parallel runtime."""

from __future__ import annotations

import pytest

from upset_profiler.orchestration.runtime import run_parallel


@pytest.mark.unit
def test_run_parallel_preserves_input_order() -> None:
    outcomes = run_parallel(
        {
            "third": lambda: 3,
            "first": lambda: 1,
            "second": lambda: 2,
        },
        max_workers=3,
    )
    assert [outcome.key for outcome in outcomes] == ["third", "first", "second"]
    assert [outcome.value for outcome in outcomes] == [3, 1, 2]


@pytest.mark.unit
def test_run_parallel_isolates_failures() -> None:
    def boom() -> int:
        raise RuntimeError("worker failed")

    outcomes = run_parallel({"ok": lambda: 1, "bad": boom}, max_workers=2)
    assert outcomes[0].ok is True
    assert outcomes[1].ok is False
    assert isinstance(outcomes[1].error, RuntimeError)


@pytest.mark.unit
def test_run_parallel_handles_empty_and_invalid_workers() -> None:
    assert run_parallel({}, max_workers=2) == []
    with pytest.raises(ValueError, match="max_workers"):
        run_parallel({}, max_workers=0)
