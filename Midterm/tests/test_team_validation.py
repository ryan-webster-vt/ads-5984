from __future__ import annotations

import pytest

from models import (
    Faculty,
    FacultyCandidate,
    Opportunity,
    RetrievedEvidence,
    TeamMember,
    TeamProposal,
)
from src.team.generate import generate_team_proposal
from src.team.select import select_baseline_team
from src.team.verify import validate_proposal


def _faculty(scopus_id="111", tenure="Academic Tenure", h_index=10):
    return Faculty(
        scopus_id=scopus_id,
        author_name="Alpha, Ada",
        department="Biological Sciences",
        tenure=tenure,
        h_index=h_index,
        h5_index=5,
        citation_count=100,
    )


def _evidence(evidence_id="e1", faculty_id="111"):
    return RetrievedEvidence(
        evidence_id=evidence_id,
        text="Evidence text",
        metadata={
            "chunk_type": "publication",
            "faculty_ids_csv": faculty_id,
            "title": "Evidence title",
        },
        similarity=0.9,
    )


def _proposal(rationale="Publication evidence supports this role.", evidence_ids=None):
    return TeamProposal(
        run_id="run",
        opportunity_id="opp",
        members=[
            TeamMember(
                scopus_id="111",
                name="Alpha, Ada",
                department="Biological Sciences",
                role="Proposed PI",
                rationale=rationale,
                evidence_ids=evidence_ids if evidence_ids is not None else ["e1"],
            )
        ],
        proposed_pi_id="111",
        baseline_team_ids=["111"],
        llm_team_ids=["111"],
        used_llm=True,
    )


def test_valid_proposal_passes():
    issues = validate_proposal(
        _proposal(),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty()},
        expected_size=1,
    )
    assert issues == []


def test_unresolved_evidence_is_rejected():
    issues = validate_proposal(
        _proposal(evidence_ids=["missing"]),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty()},
        expected_size=1,
    )
    assert any("unresolved evidence" in issue for issue in issues)


def test_forbidden_claims_and_quartile_claims_are_rejected():
    forbidden = validate_proposal(
        _proposal(rationale="This faculty member is available to lead the project."),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty()},
        expected_size=1,
    )
    assert any("unsupported claim" in issue for issue in forbidden)

    quartile = validate_proposal(
        _proposal(rationale="This publication is in a top quartile journal."),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty()},
        expected_size=1,
    )
    assert any("journal-quartile" in issue for issue in quartile)

    negated = validate_proposal(
        _proposal(
            rationale="The roster does not establish leadership experience or availability."
        ),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty()},
        expected_size=1,
    )
    assert negated == []


def test_wrong_metric_claim_is_rejected():
    issues = validate_proposal(
        _proposal(rationale="The roster h-index is 99 and the evidence is strong."),
        candidates={"111": object()},
        evidence_by_id={"e1": _evidence()},
        faculty_by_id={"111": _faculty(h_index=10)},
        expected_size=1,
    )
    assert any("h-index claim" in issue for issue in issues)


def test_baseline_selection_is_deterministic_and_tenure_preferred():
    faculty = {
        "111": _faculty("111", "Academic Tenure", 10),
        "222": _faculty("222", "Not Tenured Personnel", 30),
    }
    candidates = [
        FacultyCandidate(
            scopus_id="111",
            author_name="Alpha, Ada",
            department="Biological Sciences",
            score=0.8,
            relevance=0.8,
            metrics=0.2,
            recency=0.9,
            evidence_ids=["e1"],
            evidence_titles=["Title 1"],
            tenure="Academic Tenure",
        ),
        FacultyCandidate(
            scopus_id="222",
            author_name="Beta, Bob",
            department="Geosciences",
            score=0.8,
            relevance=0.8,
            metrics=0.2,
            recency=0.9,
            evidence_ids=["e2"],
            evidence_titles=["Title 2"],
            tenure="Not Tenured Personnel",
        ),
    ]
    first = select_baseline_team(candidates, faculty, team_size=2)
    second = select_baseline_team(candidates, faculty, team_size=2)
    assert [member.scopus_id for member in first[0]] == [
        member.scopus_id for member in second[0]
    ]
    assert first[1] == second[1] == "111"
    roles = [member.role for member in first[0]]
    assert roles.count("Proposed PI") == 1


def test_generation_requires_candidates():
    opportunity = Opportunity(opportunity_id="opp", title="Test opportunity")
    with pytest.raises(RuntimeError):
        generate_team_proposal(
            run_id="run",
            opportunity=opportunity,
            focus="",
            candidates=[],
            evidence=[],
            faculty_by_id={},
            team_size=3,
            llm_client=None,
        )
