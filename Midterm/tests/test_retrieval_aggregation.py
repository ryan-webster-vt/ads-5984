from __future__ import annotations

from models import Faculty, RetrievedEvidence
from src.retrieval.retrieve import aggregate_faculty_candidates


def _faculty(scopus_id, name, department, h_index=None, h5_index=None, citations=None):
    return Faculty(
        scopus_id=scopus_id,
        author_name=name,
        department=department,
        h_index=h_index,
        h5_index=h5_index,
        citation_count=citations,
        top_journal_quartile_pct=None,
    )


def _evidence(evidence_id, faculty_id, similarity, year=2024):
    return RetrievedEvidence(
        evidence_id=evidence_id,
        text="Publication evidence",
        metadata={
            "chunk_type": "publication",
            "faculty_ids_csv": faculty_id,
            "year": year,
            "title": f"Title {evidence_id}",
        },
        similarity=similarity,
    )


def test_aggregation_uses_exact_faculty_ids_and_stable_sort():
    faculty = {
        "111": _faculty("111", "Alpha, A", "Biological Sciences", h_index=20, citations=2000),
        "222": _faculty("222", "Beta, B", "Geosciences", h_index=10, citations=1000),
        "333": _faculty("333", "Gamma, C", "Statistics"),
    }
    evidence = [
        _evidence("e1", "111", 0.9, 2025),
        _evidence("e2", "111", 0.8, 2020),
        _evidence("e3", "222", 0.7, 2024),
        _evidence("e4", "222,333", 0.6, 2026),
        RetrievedEvidence(
            evidence_id="profile",
            text="profile",
            metadata={"chunk_type": "faculty_profile", "faculty_ids_csv": "333"},
            similarity=1.0,
        ),
    ]
    candidates = aggregate_faculty_candidates(evidence, faculty, current_year=2026)
    assert [candidate.scopus_id for candidate in candidates] == ["111", "333", "222"]
    assert candidates[0].score >= candidates[1].score >= candidates[2].score
    assert candidates[0].evidence_ids == ["e1", "e2"]
    gamma = next(candidate for candidate in candidates if candidate.scopus_id == "333")
    assert gamma.metrics is None
    assert "h_index" in gamma.metric_missing


def test_aggregation_ignores_profile_only_faculty():
    faculty = {"111": _faculty("111", "Alpha, A", "Biological Sciences", h_index=5)}
    evidence = [
        RetrievedEvidence(
            evidence_id="profile",
            text="profile",
            metadata={"chunk_type": "faculty_profile", "faculty_ids_csv": "111"},
            similarity=1.0,
        )
    ]
    assert aggregate_faculty_candidates(evidence, faculty, current_year=2026) == []
