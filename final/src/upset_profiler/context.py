"""Run context and provenance helpers.

A :class:`RunContext` identifies a single pipeline execution and carries the
metadata needed for auditability: a unique run id, creation time, the actor that
started the run, and an extensible metadata mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=UTC)


class RunContext(BaseModel):
    """Provenance metadata for a single run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utc_now)
    actor: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> RunContext:
        """Create a new run context with generated id and timestamp."""
        return cls(actor=actor, metadata=dict(metadata or {}))

    def child(self, *, actor: str, metadata: dict[str, Any] | None = None) -> RunContext:
        """Derive a context for a nested stage, preserving the original run id."""
        merged = {**self.metadata, **(metadata or {})}
        return RunContext(
            run_id=self.run_id,
            created_at=self.created_at,
            actor=actor,
            metadata=merged,
        )
