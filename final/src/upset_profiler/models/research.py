"""Contracts for historical research batches, results, and persistence."""

from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import Field, HttpUrl, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import (
    AgentError,
    Provenance,
    SeasonStr,
    UtcDatetime,
    ValidationIssue,
    VersionedModel,
)
from upset_profiler.models.games import HistoricalGame


class ResearchBatch(VersionedModel):
    """A unit of work assigned by the Research-Orchestrator."""

    batch_id: str = Field(min_length=1)
    run_id: UUID
    game_ids: list[str] = Field(min_length=1)
    season: SeasonStr | None = None
    created_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _game_ids_are_unique(self) -> Self:
        if len(self.game_ids) != len(set(self.game_ids)):
            raise ValueError("research batch contains duplicate game_id values")
        return self


class HistoricalStatsResult(VersionedModel):
    """Structured historical statistics returned by the Historical-Stats worker."""

    run_id: UUID
    batch_id: str = Field(min_length=1)
    games: list[HistoricalGame] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _games_are_unique(self) -> Self:
        game_ids = [game.game_id for game in self.games]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("historical stats result contains duplicate game_id values")
        return self


class SourceDocument(VersionedModel):
    """A cited contextual document collected by the Context-Research worker."""

    document_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    url: HttpUrl
    title: str | None = None
    author: str | None = None
    source_domain: str = Field(min_length=1)
    published_at: UtcDatetime | None = None
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)
    content: str | None = None
    content_hash: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _document_has_content_or_hash(self) -> Self:
        if not self.content and not self.content_hash:
            raise ValueError("source document requires content or content_hash")
        return self


class ContextResearchResult(VersionedModel):
    """Cited documents and context returned by the Context-Research worker."""

    run_id: UUID
    batch_id: str = Field(min_length=1)
    documents: list[SourceDocument] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _document_ids_are_unique(self) -> Self:
        document_ids = [document.document_id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("context research result contains duplicate document_id values")
        return self


class ResearchRecord(VersionedModel):
    """A joined research record for one game after orchestrator aggregation."""

    game_id: str = Field(min_length=1)
    historical_game: HistoricalGame | None = None
    documents: list[SourceDocument] = Field(default_factory=list)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _nested_game_ids_match(self) -> Self:
        if self.historical_game is not None and self.historical_game.game_id != self.game_id:
            raise ValueError("historical_game.game_id must match record game_id")
        for document in self.documents:
            if document.game_id != self.game_id:
                raise ValueError("every source document must match record game_id")
        return self


class ResearchAggregate(VersionedModel):
    """Validated records and quarantined issues from a historical research run."""

    run_id: UUID
    records: list[ResearchRecord] = Field(default_factory=list)
    quarantined: list[ValidationIssue] = Field(default_factory=list)
    documents_stored: int = Field(default=0, ge=0)
    chunks_indexed: int = Field(default=0, ge=0)
    completed_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _records_are_unique(self) -> Self:
        game_ids = [record.game_id for record in self.records]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("research aggregate contains duplicate game_id values")
        return self


class IngestionResult(VersionedModel):
    """Summary of one historical ingestion run for the admin CLI and logs."""

    run_id: UUID
    season: SeasonStr
    aggregate: ResearchAggregate
    source: str = Field(min_length=1)
    started_at: UtcDatetime
    completed_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _run_ids_match(self) -> Self:
        if self.run_id != self.aggregate.run_id:
            raise ValueError("run_id must match aggregate.run_id")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self

    @property
    def record_count(self) -> int:
        """Return the number of validated research records."""
        return len(self.aggregate.records)

    @property
    def quarantine_count(self) -> int:
        """Return the number of quarantined issues."""
        return len(self.aggregate.quarantined)
