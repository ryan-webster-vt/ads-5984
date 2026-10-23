from __future__ import annotations

from models import Faculty, Opportunity, ResearchQuery, RetrievedEvidence, UserConstraints
from src.team.generate import generate_team_proposal
from src.team.scoring import score_faculty_candidates
from src.team.verify import validate_proposal


def _faculty():
    names = [
        ("1", "Carey, Cayelan C.", "Biological Sciences", "Academic Tenure"),
        ("2", "Smith, John", "Chemistry", "Academic Tenure"),
        ("3", "Jones, Amy", "Physics", "Not Tenured Personnel"),
        ("4", "Barrett, John E.", "Biological Sciences", "Academic Tenure Pending"),
        ("5", "Monarrez, Pedro M.", "Geosciences", "Academic Tenure"),
    ]
    return {
        scopus_id: Faculty(
            scopus_id=scopus_id,
            author_name=name,
            department=department,
            tenure=tenure,
            h_index=10 + int(scopus_id),
            h5_index=5 + int(scopus_id),
            citation_count=100 * int(scopus_id),
            top_journal_quartile_pct=50.0,
        )
        for scopus_id, name, department, tenure in names
    }


def _evidence():
    faculty = _faculty()
    evidence = []
    for index, (faculty_id, member) in enumerate(faculty.items()):
        evidence.append(
            RetrievedEvidence(
                evidence_id=f"e{index + 1}",
                text=f"Ecology climate data analysis evidence for {member.author_name}",
                metadata={
                    "chunk_type": "publication",
                    "faculty_ids_csv": faculty_id,
                    "eid": f"E{index + 1}",
                    "year": 2024,
                    "title": f"Ecology study {index + 1}",
                    "final_score": 0.9 - index * 0.05,
                },
                similarity=0.9 - index * 0.05,
            )
        )
    return evidence


def test_five_deterministic_runs_are_stable_and_validate():
    faculty = _faculty()
    evidence = _evidence()
    query = ResearchQuery(
        problem_statement="Ecology and climate.",
        required_expertise=["ecology", "climate"],
        methods=["data analysis"],
        search_queries=["ecology climate"],
    )
    candidates = score_faculty_candidates(
        evidence,
        faculty,
        research_query=query,
        current_year=2026,
    )
    constraints = UserConstraints(team_size=3)
    member_sets: list[set[str]] = []
    pi_ids: list[str] = []
    for run_index in range(5):
        proposal = generate_team_proposal(
            run_id=f"run-{run_index}",
            opportunity=Opportunity(opportunity_id="opp", title="Ecology grant"),
            focus="",
            candidates=candidates,
            evidence=evidence,
            faculty_by_id=faculty,
            team_size=3,
            llm_client=None,
            research_query=query,
            constraints=constraints,
        )
        issues = validate_proposal(
            proposal,
            candidates={candidate.scopus_id: candidate for candidate in candidates},
            evidence_by_id={item.evidence_id: item for item in evidence},
            faculty_by_id=faculty,
            expected_size=3,
            constraints=constraints,
        )
        assert issues == []
        member_sets.append({member.scopus_id for member in proposal.members})
        pi_ids.append(proposal.proposed_pi_id)

    assert all(member_set == member_sets[0] for member_set in member_sets)
    assert len(set(pi_ids)) == 1


def test_baseline_satisfies_required_excluded_pi_and_evidence_properties():
    faculty = _faculty()
    evidence = _evidence()
    query = ResearchQuery(
        problem_statement="Ecology and climate.",
        required_expertise=["ecology"],
        search_queries=["ecology"],
    )
    candidates = score_faculty_candidates(
        evidence,
        faculty,
        research_query=query,
        current_year=2026,
    )
    constraints = UserConstraints(
        team_size=3,
        required_members=["2"],
        excluded_members=["5"],
        tenure_track_only=True,
    )
    proposal = generate_team_proposal(
        run_id="properties",
        opportunity=Opportunity(opportunity_id="opp", title="Ecology grant"),
        focus="",
        candidates=candidates,
        evidence=evidence,
        faculty_by_id=faculty,
        team_size=3,
        llm_client=None,
        research_query=query,
        constraints=constraints,
    )
    member_ids = [member.scopus_id for member in proposal.members]
    assert len(member_ids) == 3
    assert len(set(member_ids)) == 3
    assert "2" in member_ids
    assert "5" not in member_ids
    assert "3" not in member_ids
    assert proposal.proposed_pi_id in member_ids
    assert [member.role for member in proposal.members].count("Proposed PI") == 1
    assert all(member.evidence_ids for member in proposal.members)
