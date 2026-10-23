"""Configurable, explainable candidate scoring with missing-metric handling."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime

import config
from models import Faculty, FacultyCandidate, ResearchQuery, RetrievedEvidence
from src.retrieval.concepts import concept_list, matched_concepts

COMPONENT_NAMES = (
    "relevance",
    "coverage",
    "metrics",
    "recency",
    "collaboration",
    "diversity",
)


@dataclass(frozen=True)
class ScoringWeights:
    relevance: float = 0.45
    coverage: float = 0.20
    metrics: float = 0.15
    recency: float = 0.10
    collaboration: float = 0.05
    diversity: float = 0.05

    @classmethod
    def from_config(cls) -> ScoringWeights:
        return cls(**config.SCORING_WEIGHTS)

    @classmethod
    def from_override(cls, override: dict[str, float] | None) -> ScoringWeights:
        if not override:
            return cls.from_config()
        base = cls.from_config().as_dict()
        for key, value in override.items():
            if key in base and value is not None:
                base[key] = max(0.0, float(value))
        return cls(**base)

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in COMPONENT_NAMES}


def _percentile_map(values: dict[str, float | None]) -> dict[str, float | None]:
    present = sorted(value for value in values.values() if value is not None)
    if not present:
        return {key: None for key in values}
    if len(present) == 1:
        return {key: (1.0 if value is not None else None) for key, value in values.items()}
    result: dict[str, float | None] = {}
    for key, value in values.items():
        result[key] = (
            None if value is None else bisect_left(present, value) / (len(present) - 1)
        )
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


def _faculty_ids(item: RetrievedEvidence) -> list[str]:
    return [
        token.strip()
        for token in str(item.metadata.get("faculty_ids_csv") or "").split(",")
        if token.strip()
    ]


def _evidence_concepts(item: RetrievedEvidence, concepts: list[str]) -> list[str]:
    recorded = [str(value) for value in item.metadata.get("matched_concepts") or []]
    text = " ".join(
        [
            str(item.metadata.get("title") or ""),
            str(item.metadata.get("source_title") or ""),
            item.text,
        ]
    )
    combined = recorded + matched_concepts(text, concepts)
    result: list[str] = []
    seen: set[str] = set()
    for value in combined:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _recency_decay(year: int, current_year: int, preference: str) -> float:
    if year <= 0:
        return 0.0
    age = max(0, current_year - year)
    if preference == "any":
        return 0.8
    decay = 0.5 ** (age / config.RECENCY_HALF_LIFE_YEARS)
    if preference == "recent":
        return min(1.0, decay * 1.15)
    return decay


def _observed_collaboration(
    by_faculty: dict[str, list[RetrievedEvidence]],
    candidate_ids: list[str],
) -> dict[str, float]:
    shared_counts: dict[str, int] = {candidate_id: 0 for candidate_id in candidate_ids}
    seen_publications: set[str] = set()
    for items in by_faculty.values():
        for item in items:
            key = str(
                item.metadata.get("eid")
                or item.metadata.get("parent_id")
                or item.evidence_id
            )
            if key in seen_publications:
                continue
            seen_publications.add(key)
            publication_faculty = [
                faculty_id
                for faculty_id in _faculty_ids(item)
                if faculty_id in shared_counts
            ]
            if len(publication_faculty) < 2:
                continue
            for faculty_id in publication_faculty:
                shared_counts[faculty_id] += len(publication_faculty) - 1
    maximum = max(shared_counts.values(), default=0)
    if maximum <= 0:
        return {candidate_id: 0.0 for candidate_id in candidate_ids}
    return {
        candidate_id: count / maximum
        for candidate_id, count in shared_counts.items()
    }


def score_faculty_candidates(
    evidence: list[RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    *,
    research_query: ResearchQuery | None = None,
    weights: ScoringWeights | None = None,
    current_year: int | None = None,
    recency_preference: str = "balanced",
) -> list[FacultyCandidate]:
    """Aggregate publication evidence into deterministic ranked candidates.

    Missing metrics drop their weight and the remaining weights are renormalized;
    no missing metric is silently imputed. `Top Journal Quartile %` is treated as
    an author-level prior only.
    """
    weights = weights or ScoringWeights.from_config()
    current_year = current_year or datetime.now(UTC).year
    concepts = concept_list(research_query)
    percentiles = _metric_percentiles(list(faculty_by_id.values()))

    by_faculty: dict[str, list[RetrievedEvidence]] = {}
    for item in evidence:
        if item.metadata.get("chunk_type") != "publication":
            continue
        for faculty_id in _faculty_ids(item):
            if faculty_id in faculty_by_id:
                by_faculty.setdefault(faculty_id, []).append(item)

    candidate_ids = sorted(by_faculty)
    collaboration = _observed_collaboration(by_faculty, candidate_ids)
    department_counts: dict[str, int] = {}
    for faculty_id in candidate_ids:
        department = faculty_by_id[faculty_id].department
        department_counts[department] = department_counts.get(department, 0) + 1
    candidate_total = max(1, len(candidate_ids))

    candidates: list[FacultyCandidate] = []
    for faculty_id in candidate_ids:
        member = faculty_by_id[faculty_id]
        ranked = sorted(
            by_faculty[faculty_id],
            key=lambda item: (
                -float(item.metadata.get("final_score") or item.similarity),
                item.evidence_id,
            ),
        )
        top = ranked[:3]
        relevance = float(
            top[0].metadata.get("final_score") or top[0].similarity
        )
        evidence_concepts = {
            concept
            for item in ranked
            for concept in _evidence_concepts(item, concepts)
        }
        coverage = (
            len(evidence_concepts & set(concepts)) / len(concepts)
            if concepts
            else None
        )
        metric_values = [
            value
            for name in percentiles
            if (value := percentiles[name].get(faculty_id)) is not None
        ]
        metrics = sum(metric_values) / len(metric_values) if metric_values else None
        metric_missing = [
            name for name in percentiles if percentiles[name].get(faculty_id) is None
        ]
        recency = max(
            _recency_decay(int(item.metadata.get("year") or 0), current_year, recency_preference)
            for item in top
        )
        diversity = 1.0 - (
            (department_counts.get(member.department, 1) - 1) / max(1, candidate_total - 1)
        )

        components: dict[str, float | None] = {
            "relevance": relevance,
            "coverage": coverage,
            "metrics": metrics,
            "recency": recency,
            "collaboration": collaboration.get(faculty_id, 0.0),
            "diversity": diversity,
        }
        available = {
            name: value for name, value in components.items() if value is not None
        }
        weight_total = sum(getattr(weights, name) for name in available)
        score = (
            sum(value * getattr(weights, name) for name, value in available.items())
            / weight_total
            if weight_total > 0
            else 0.0
        )
        matched = sorted(
            evidence_concepts,
            key=lambda concept: concepts.index(concept) if concept in concepts else 999,
        )
        candidates.append(
            FacultyCandidate(
                scopus_id=faculty_id,
                author_name=member.author_name,
                department=member.department,
                score=score,
                relevance=relevance,
                metrics=metrics,
                recency=recency,
                coverage=coverage,
                collaboration=collaboration.get(faculty_id, 0.0),
                diversity=diversity,
                evidence_ids=[item.evidence_id for item in top],
                evidence_titles=[
                    str(item.metadata.get("title") or item.evidence_id) for item in top
                ],
                matched_concepts=matched,
                metric_missing=metric_missing,
                score_components={
                    name: float(value) for name, value in available.items()
                },
                tenure=member.tenure,
                rank=member.rank,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.scopus_id))
