# Offline test module: Configurable weights, missing metrics, recency, and collaboration scoring.
from __future__ import annotations

from models import Faculty, ResearchQuery, RetrievedEvidence
from src.team.scoring import ScoringWeights, score_faculty_candidates


def _faculty(scopus_id, department, metrics=True):
    return Faculty(
        scopus_id=scopus_id,
        author_name=f"Person {scopus_id}",
        department=department,
        tenure="Academic Tenure",
        h_index=20 if metrics else None,
        h5_index=10 if metrics else None,
        citation_count=1000 if metrics else None,
        top_journal_quartile_pct=60.0 if metrics else None,
    )


def _evidence(evidence_id, faculty_id, score, year=2024, text="ecology"):
    return RetrievedEvidence(
        evidence_id=evidence_id,
        text=text,
        metadata={
            "chunk_type": "publication",
            "faculty_ids_csv": faculty_id,
            "eid": evidence_id,
            "year": year,
            "title": text,
            "final_score": score,
            "matched_concepts": ["ecology"],
        },
        similarity=score,
    )


# Verifies: missing metrics are not imputed and weight is redistributed.
def test_missing_metrics_are_not_imputed_and_weight_is_redistributed():
    faculty = {
        "1": _faculty("1", "Biological Sciences", metrics=True),
        "2": _faculty("2", "Chemistry", metrics=False),
    }
    evidence = [
        _evidence("e1", "1", 0.8),
        _evidence("e2", "2", 0.8),
    ]
    query = ResearchQuery(
        problem_statement="ecology",
        required_expertise=["ecology"],
        search_queries=["ecology"],
    )
    candidates = score_faculty_candidates(
        evidence,
        faculty,
        research_query=query,
        current_year=2026,
    )
    missing = next(candidate for candidate in candidates if candidate.scopus_id == "2")
    assert missing.metrics is None
    assert "h_index" in missing.metric_missing
    assert "metrics" not in missing.score_components
    assert missing.score > 0
    assert set(missing.score_components) <= {
        "relevance",
        "coverage",
        "recency",
        "collaboration",
        "diversity",
    }


# Verifies: weights are configurable and recency preference changes score.
def test_weights_are_configurable_and_recency_preference_changes_score():
    faculty = {
        "1": _faculty("1", "Biological Sciences", metrics=True),
        "2": _faculty("2", "Chemistry", metrics=True),
    }
    evidence = [
        _evidence("e1", "1", 0.9, year=2025),
        _evidence("e2", "2", 0.9, year=2010),
    ]
    query = ResearchQuery(problem_statement="ecology", search_queries=["ecology"])
    default = score_faculty_candidates(
        evidence, faculty, research_query=query, current_year=2026
    )
    recent = score_faculty_candidates(
        evidence,
        faculty,
        research_query=query,
        current_year=2026,
        recency_preference="recent",
    )
    assert default[0].scopus_id == "1"
    assert recent[0].scopus_id == "1"
    assert recent[0].recency >= default[0].recency
    weights = ScoringWeights.from_override({"relevance": 1.0, "metrics": 0.0})
    assert weights.relevance == 1.0
    assert weights.metrics == 0.0
