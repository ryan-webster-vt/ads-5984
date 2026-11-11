"""Parallel task execution with per-task isolation.

Phase 2 uses this runtime to run independent Monitor workers concurrently while
keeping one worker's failure from cancelling the rest of the slate. Later phases
reuse the same helper for research batches and extraction candidates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Generic, TypeVar

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class TaskOutcome(Generic[ResultT]):
    """The value or exception produced by one isolated parallel task."""

    key: str
    value: ResultT | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """Return whether the task completed without raising."""
        return self.error is None


def run_parallel(
    tasks: Mapping[str, Callable[[], ResultT]],
    *,
    max_workers: int,
) -> list[TaskOutcome[ResultT]]:
    """Run tasks in a thread pool and return outcomes in input order.

    Every task is isolated: an exception is captured in that task's outcome and
    never prevents other tasks from running.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if not tasks:
        return []

    outcomes: dict[str, TaskOutcome[ResultT]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {executor.submit(task): key for key, task in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                outcomes[key] = TaskOutcome(key=key, value=future.result())
            except Exception as exc:
                outcomes[key] = TaskOutcome(key=key, error=exc)
    return [outcomes[key] for key in tasks]
