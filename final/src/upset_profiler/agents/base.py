"""Shared agent protocol and progress-reporting helpers."""

from __future__ import annotations

from typing import Protocol

from upset_profiler.models.progress import ProgressEvent, ProgressStatus
from upset_profiler.orchestration.progress import ProgressReporter


class Agent(Protocol):
    """Structural interface implemented by every agent role."""

    name: str


class BaseAgent:
    """Base class that gives agents a consistent name and progress channel."""

    name: str = "agent"

    def __init__(self, *, progress: ProgressReporter | None = None) -> None:
        self._progress = progress

    @property
    def progress(self) -> ProgressReporter | None:
        """Return the shared progress reporter, if one was supplied."""
        return self._progress

    def _report(
        self,
        message: str,
        *,
        status: ProgressStatus = ProgressStatus.RUNNING,
        agent: str | None = None,
        game_id: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent | None:
        """Emit a progress event when a reporter is configured."""
        if self._progress is None:
            return None
        return self._progress.emit(
            message,
            status=status,
            agent=agent or self.name,
            game_id=game_id,
            completed=completed,
            total=total,
            metadata=metadata,
        )

    def _advance(
        self,
        message: str,
        *,
        agent: str | None = None,
        game_id: str | None = None,
        step: int = 1,
        total: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent | None:
        """Advance shared progress by ``step`` when a reporter is configured."""
        if self._progress is None:
            return None
        return self._progress.advance(
            message,
            step=step,
            agent=agent or self.name,
            game_id=game_id,
            total=total,
            metadata=metadata,
        )
