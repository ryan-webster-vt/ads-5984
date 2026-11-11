"""Shared Pydantic contracts and strict-model helpers.

Every agent handoff and stored record inherits from :class:`StrictModel`, which
forbids unknown fields. Aggregate and versioned artifacts inherit from
:class:`VersionedModel` so schema changes are explicit. Timestamps are validated
as timezone-aware and normalized to UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl

from upset_profiler.context import RunContext, utc_now

SCHEMA_VERSION = 1

_SEASON_PATTERN = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})$")


def _to_utc(value: datetime) -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _validate_season(value: str) -> str:
    match = _SEASON_PATTERN.match(value.strip())
    if match is None:
        raise ValueError(f"invalid season {value!r}; expected a value like '2024-2025'")
    start, end = int(match.group("start")), int(match.group("end"))
    if end != start + 1:
        raise ValueError(f"invalid season {value!r}; end year must be start year + 1")
    return value.strip()


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
SeasonStr = Annotated[str, AfterValidator(_validate_season)]
ThresholdValue = bool | int | float | str


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields and trims string whitespace."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class VersionedModel(StrictModel):
    """Contract artifact that participates in explicit schema versioning."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)


class TeamRef(StrictModel):
    """Reference to a team participating in a game."""

    team_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    abbreviation: str | None = None
    conference: str | None = None
    division: str = "D1"


class Citation(StrictModel):
    """A citable source attached to a claim, flag, or validated precondition."""

    citation_id: str | None = None
    title: str | None = None
    url: HttpUrl
    source_domain: str = Field(min_length=1)
    published_at: UtcDatetime | None = None
    retrieved_at: UtcDatetime
    quote: str | None = None


class Provenance(StrictModel):
    """Audit metadata describing where an artifact came from."""

    run_id: UUID
    actor: str = "system"
    source: str = "unknown"
    model_name: str | None = None
    prompt_version: str | None = None
    created_at: UtcDatetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_run_context(
        cls,
        context: RunContext,
        *,
        source: str,
        actor: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Provenance:
        """Build provenance from an existing :class:`RunContext`."""
        merged = {**context.metadata, **(metadata or {})}
        return cls(
            run_id=context.run_id,
            actor=actor or context.actor,
            source=source,
            model_name=model_name,
            prompt_version=prompt_version,
            metadata=merged,
        )


class ValidationIssue(StrictModel):
    """A single validation problem attached to a record or handoff."""

    location: str = Field(min_length=1)
    message: str = Field(min_length=1)
    code: str = "validation_error"
    severity: Literal["warning", "error"] = "error"
    context: dict[str, Any] = Field(default_factory=dict)


class AgentError(StrictModel):
    """Serializable error record carried in agent results.

    This contract is distinct from the ``AgentError`` exception in
    :mod:`upset_profiler.errors`; it is the on-the-wire representation.
    """

    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    agent: str | None = None
    stage: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        agent: str | None = None,
        stage: str | None = None,
        retryable: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentError:
        """Convert an exception into a serializable error record."""
        inferred_retryable = getattr(exc, "retryable", False)
        merged = {**getattr(exc, "context", {}), **(context or {})}
        return cls(
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            retryable=inferred_retryable if retryable is None else retryable,
            agent=agent,
            stage=stage,
            context=merged,
        )
