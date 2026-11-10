"""Embedding retrieval and deterministic publication-to-faculty aggregation."""

from __future__ import annotations

from bisect import bisect_left
from datetime import UTC, datetime

import chromadb

import config
from models import (
    Faculty,
    FacultyCandidate,
    Opportunity,
    ResearchQuery,
    RetrievedEvidence,
)
from src.retrieval.rerank import RerankOutcome, rerank_evidence


def build_query(opportunity: Opportunity, focus: str = "") -> str:
    # Opportunity text is untrusted input; it is delimited later by the optimizer,
    # not interpreted as instructions here.
    parts = [
        f"Grant opportunity: {opportunity.title}",
        f"Agency: {opportunity.agency}",
        opportunity.description[:4000],
    ]
    if focus.strip():
        parts.append(f"Additional team focus: {focus.strip()}")
    return "\n\n".join(part for part in parts if part.strip())


def retrieve_evidence(
    collection: chromadb.Collection,
    query: str,
    k_in: int = config.DEFAULT_K_IN,
) -> list[RetrievedEvidence]:
    # ``max(1, ...)`` keeps Chroma happy while allowing a caller to request zero
    # results only through the higher-level selection logic.
    result = collection.query(query_texts=[query], n_results=max(1, k_in))
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    evidence: list[RetrievedEvidence] = []
    for index, evidence_id in enumerate(ids):
        metadata = dict(metadatas[index] or {})
        distance = float(distances[index]) if index < len(distances) else None
        # Chroma cosine distance is in [0, 2]; clamp the display similarity to a
        # stable [0, 1] range.
        similarity = 0.0 if distance is None else max(0.0, min(1.0, 1.0 - distance))
        evidence.append(
            RetrievedEvidence(
                evidence_id=evidence_id,
                text=documents[index] or "",
                metadata=metadata,
                distance=distance,
                similarity=similarity,
            )
        )
    return evidence


def _percentile_map(values: dict[str, float | None]) -> dict[str, float | None]:
    # Percentiles are computed only across present roster values; missing metrics
    # stay null and are redistributed by the scorer.
    present = sorted(value for value in values.values() if value is not None)
    if not present:
        return {key: None for key in values}
    if len(present) == 1:
        return {
            key: (1.0 if value is not None else None)
            for key, value in values.items()
        }
    result: dict[str, float | None] = {}
    for key, value in values.items():
        if value is None:
            result[key] = None
        else:
            result[key] = bisect_left(present, value) / (len(present) - 1)
    return result


def _metric_percentiles(faculty: list[Faculty]) -> dict[str, dict[str, float | None]]:
    metrics = {
        "h_index": {member.scopus_id: member.h_index for member in faculty},
        "h5_index": {member.scopus_id: member.h5_index for member in faculty},
        "citation_count": {
            member.scopus_id: member.citation_count for member in faculty
        },
        "top_journal_quartile_pct": {
            member.scopus_id: member.top_journal_quartile_pct for member in faculty
        },
    }
    return {
        metric_name: _percentile_map(values)
        for metric_name, values in metrics.items()
    }


def aggregate_faculty_candidates(
    evidence: list[RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    *,
    current_year: int | None = None,
) -> list[FacultyCandidate]:
    """Aggregate publication evidence into explainable faculty candidates."""
    current_year = current_year or datetime.now(UTC).year
    percentiles = _metric_percentiles(list(faculty_by_id.values()))
    by_faculty: dict[str, list[RetrievedEvidence]] = {}

    # Only publication chunks attribute faculty; profile chunks are for recall and
    # cannot serve as factual team evidence.

    for item in evidence:
        if item.metadata.get("chunk_type") != "publication":
            continue
        faculty_ids = [
            token.strip()
            for token in str(item.metadata.get("faculty_ids_csv") or "").split(",")
            if token.strip()
        ]
        for faculty_id in faculty_ids:
            if faculty_id in faculty_by_id:
                by_faculty.setdefault(faculty_id, []).append(item)

    candidates: list[FacultyCandidate] = []
    for faculty_id, items in by_faculty.items():
        member = faculty_by_id[faculty_id]
        ranked = sorted(
            items,
            key=lambda item: (-item.similarity, item.evidence_id),
        )
        # Best evidence drives relevance; the top three items are retained for
        # explainability and later shortlist scoring.
        relevance = ranked[0].similarity
        recency_years = [
            int(item.metadata.get("year") or 0) for item in ranked
        ]
        recent_year = max(recency_years) if recency_years else 0
        # Five-year half-life matches the documented recency policy.
        recency = (
            0.5 ** (max(0, current_year - recent_year) / 5.0)
            if recent_year > 0
            else 0.0
        )
        # Average only metrics that exist for this faculty member.
        metric_values = [
            percentiles[name].get(faculty_id)
            for name in percentiles
            if percentiles[name].get(faculty_id) is not None
        ]
        metric_score = (
            sum(metric_values) / len(metric_values) if metric_values else None
        )
        components: list[tuple[str, float, float]] = [
            ("relevance", relevance, 0.60),
            ("recency", recency, 0.20),
        ]
        if metric_score is not None:
            components.append(("metrics", metric_score, 0.20))
        weight_total = sum(weight for _, _, weight in components)
        score = sum(value * weight for _, value, weight in components) / weight_total

        # Surface missing metrics to the UI instead of hiding imputation.
        metric_missing = [
            name
            for name in percentiles
            if percentiles[name].get(faculty_id) is None
        ]
        evidence_ids = [item.evidence_id for item in ranked[:3]]
        evidence_titles = [
            str(item.metadata.get("title") or item.evidence_id)
            for item in ranked[:3]
        ]
        candidates.append(
            FacultyCandidate(
                scopus_id=faculty_id,
                author_name=member.author_name,
                department=member.department,
                score=score,
                relevance=relevance,
                metrics=metric_score,
                recency=recency,
                evidence_ids=evidence_ids,
                evidence_titles=evidence_titles,
                metric_missing=metric_missing,
                tenure=member.tenure,
                rank=member.rank,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.scopus_id))


def retrieve_evidence_multi(
    collection: chromadb.Collection,
    queries: list[str],
    *,
    k_in: int = config.DEFAULT_K_IN,
    union_cap: int = config.RETRIEVAL_UNION_CAP,
) -> list[RetrievedEvidence]:
    """Query each optimized search string and merge by exact evidence ID."""
    merged: dict[str, RetrievedEvidence] = {}
    # Each optimized query contributes candidates; keep the strongest occurrence of
    # the exact same evidence chunk.
    for query in queries:
        if not query.strip():
            continue
        for item in retrieve_evidence(collection, query, k_in=k_in):
            existing = merged.get(item.evidence_id)
            if existing is None or item.similarity > existing.similarity:
                merged[item.evidence_id] = item
    # Stable ordering plus an explicit union cap bounds reranker cost.
    ordered = sorted(
        merged.values(),
        key=lambda item: (-item.similarity, item.evidence_id),
    )
    return ordered[: max(1, union_cap)]


def retrieve_for_research_query(
    collection: chromadb.Collection,
    research_query: ResearchQuery,
    *,
    llm_client: object | None = None,
    k_in: int = config.DEFAULT_K_IN,
    k_out: int = config.DEFAULT_K_OUT,
    union_cap: int = config.RETRIEVAL_UNION_CAP,
) -> tuple[list[RetrievedEvidence], RerankOutcome]:
    """Retrieve a union across optimized queries, then rerank and diversify."""
    union = retrieve_evidence_multi(
        collection,
        research_query.search_queries,
        k_in=k_in,
        union_cap=union_cap,
    )
    outcome = rerank_evidence(
        union,
        research_query,
        llm_client=llm_client,
        k_out=k_out,
    )
    return outcome.evidence, outcome
