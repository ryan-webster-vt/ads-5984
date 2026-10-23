from __future__ import annotations

import config
from models import Faculty, Opportunity, ResearchQuery, RetrievedEvidence, UserConstraints
from src.evaluation import RunObservation, evaluate_observations
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


def test_evaluation_metrics_meet_targets_for_stable_runs():
    observations = [
        RunObservation(
            run_id=f"run-{index}",
            member_ids=["1", "2", "4"],
            pi_id="1",
            evidence_ids=["e1", "e2", "e4"],
            validator_status="baseline",
            latency_ms=100,
            llm_calls=0,
            total_tokens=0,
        )
        for index in range(5)
    ]
    summary = evaluate_observations(observations)
    assert summary["member_jaccard_mean"] == 1.0
    assert summary["pi_agreement"] == 1.0
    assert summary["validator_pass_rate"] == 1.0
    assert summary["targets"]["targets_met"] is True
    assert summary["runtime"]["within_budget"] is True


def test_evaluation_metrics_detect_member_pi_and_validator_instability():
    observations = [
        RunObservation(
            run_id="a",
            member_ids=["1", "2", "4"],
            pi_id="1",
            evidence_ids=["e1"],
            validator_status="baseline",
        ),
        RunObservation(
            run_id="b",
            member_ids=["1", "3", "5"],
            pi_id="2",
            evidence_ids=["e2"],
            validator_status="fallback",
            validation_issues=["team size is 2; expected 3"],
        ),
    ]
    summary = evaluate_observations(observations)
    assert summary["member_jaccard_mean"] < 0.8
    assert summary["pi_agreement"] == 0.5
    assert summary["validator_pass_rate"] == 0.5
    assert summary["targets"]["targets_met"] is False


def test_evaluation_runtime_budget_flags_excess_latency_and_calls():
    observations = [
        RunObservation(
            run_id=str(index),
            member_ids=["1"],
            pi_id="1",
            evidence_ids=["e1"],
            validator_status="baseline",
            latency_ms=config.RUNTIME_BUDGETS["team_build_p95_ms"] + 1,
            llm_calls=config.RUNTIME_BUDGETS["max_llm_calls"] + 1,
        )
        for index in range(3)
    ]
    summary = evaluate_observations(observations)
    assert summary["runtime"]["within_budget"] is False


def test_five_offline_runs_record_evaluation_targets():
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
    observations: list[RunObservation] = []
    for run_index in range(5):
        proposal = generate_team_proposal(
            run_id=f"eval-{run_index}",
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
        observations.append(
            RunObservation(
                run_id=f"eval-{run_index}",
                member_ids=[member.scopus_id for member in proposal.members],
                pi_id=proposal.proposed_pi_id,
                evidence_ids=[
                    evidence_id
                    for member in proposal.members
                    for evidence_id in member.evidence_ids
                ],
                validator_status=proposal.validator_status,
                validation_issues=issues,
            )
        )
    summary = evaluate_observations(observations)
    assert summary["targets"]["targets_met"] is True
    assert summary["validator_pass_rate"] == 1.0
