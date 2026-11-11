"""Contracts for chunking, retrieval, reranking, and evidence claims."""

from __future__ import annotations

from typing import Self

from pydantic import Field, HttpUrl, model_validator

from upset_profiler.models.common import (
    Citation,
    SeasonStr,
    StrictModel,
    UtcDatetime,
    VersionedModel,
)


class DocumentChunk(VersionedModel):
    """A deterministic chunk of a cited source document."""

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    season: SeasonStr | None = None
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    source_url: HttpUrl
    source_domain: str = Field(min_length=1)
    published_at: UtcDatetime | None = None
    whitelisted: bool = True
    document_hash: str = Field(min_length=1)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _offsets_are_consistent(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class RetrievedEvidence(VersionedModel):
    """A chunk returned by vector retrieval for a query."""

    query: str = Field(min_length=1)
    chunk: DocumentChunk
    score: float
    rank: int = Field(ge=1)


class RerankedEvidence(VersionedModel):
    """A retrieved chunk after decision-model reranking."""

    evidence: RetrievedEvidence
    rerank_score: float
    original_rank: int = Field(ge=1)
    final_rank: int = Field(ge=1)
    model_name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _original_rank_matches_evidence(self) -> Self:
        if self.original_rank != self.evidence.rank:
            raise ValueError("original_rank must match evidence.rank")
        return self


class Claim(StrictModel):
    """A natural-language claim with the citations intended to support it."""

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    supported: bool | None = None
    verification_issue: str | None = None
