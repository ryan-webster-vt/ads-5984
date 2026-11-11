"""Typed progress events emitted by agents and pipeline stages."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import UtcDatetime, VersionedModel


class ProgressStage(StrEnum):
    """Pipeline stages that can report progress."""

    INGEST = "ingest"
    EXTRACT = "extract"
    MONITOR = "monitor"
    SCORE = "score"
    BACKTEST = "backtest"
    VALIDATION = "validation"
    DEMO = "demo"
    CONFIG = "config"


class ProgressStatus(StrEnum):
    """Lifecycle state of a progress event."""

    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProgressEvent(VersionedModel):
    """A single ordered progress update for a run."""

    run_id: UUID
    event_id: UUID = Field(default_factory=uuid4)
    event_index: int = Field(ge=0)
    timestamp: UtcDatetime = Field(default_factory=utc_now)
    stage: ProgressStage
    status: ProgressStatus
    agent: str = Field(min_length=1)
    message: str = Field(min_length=1)
    game_id: str | None = None
    completed: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> Self:
        if self.total is not None and self.completed > self.total:
            raise ValueError("completed must not exceed total")
        return self

    @property
    def fraction(self) -> float | None:
        """Return completion fraction, or ``None`` when no total is known."""
        if self.total is None or self.total == 0:
            return None
        return self.completed / self.total
