"""Build evidence packets and generate a validated, repaired team proposal."""

from __future__ import annotations

import json

import config
from models import (
    Faculty,
    FacultyCandidate,
    LLMTeamProposal,
    Opportunity,
    ResearchQuery,
    RetrievedEvidence,
    TeamMember,
    TeamProposal,
    UserConstraints,
)
from src.prompting import load_prompt
from src.retrieval.concepts import concept_list, matched_concepts
from src.team.constraints import ConstraintError, state_summary
from src.team.scoring import ScoringWeights
from src.team.select import select_baseline_team, shortlist_candidates
from src.team.verify import validate_proposal, verify_with_llm


# Required members are injected before ranking so the generator always sees them,
# even if another candidate would otherwise fill the shortlist.
def _packet_candidates(
    candidates: list[FacultyCandidate],
    *,
    required_ids: list[str],
    max_candidates: int,
) -> list[FacultyCandidate]:
    ranked = shortlist_candidates(candidates, limit=max(len(candidates), 1))
    # Preserve required members first, then fill remaining slots by rank.
    selected: list[FacultyCandidate] = []
    selected_ids: set[str] = set()
    for faculty_id in required_ids:
        for candidate in ranked:
            if candidate.scopus_id == faculty_id and faculty_id not in selected_ids:
                selected.append(candidate)
                selected_ids.add(faculty_id)
                break
    for candidate in ranked:
        if len(selected) >= max_candidates:
            break
        if candidate.scopus_id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.scopus_id)
    return selected[:max_candidates]


def _evidence_packet(
    candidates: list[FacultyCandidate],
    evidence: list[RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    research_query: ResearchQuery | None = None,
    constraints: UserConstraints | None = None,
    *,
    max_candidates: int = 6,
    max_evidence_per_candidate: int = 2,
    excerpt_chars: int = 500,
) -> tuple[str, dict[str, RetrievedEvidence]]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    # The packet is the only evidence the generator may cite.
    selected = _packet_candidates(
        candidates,
        required_ids=constraints.required_members if constraints else [],
        max_candidates=max_candidates,
    )
    concepts = concept_list(research_query)
    lines: list[str] = []
    # Research needs and constraints provide context but never override the
    # deterministic candidate/evidence boundary.
    if research_query is not None:
        lines.append(
            "Research needs: "
            + json.dumps(
                {
                    "problem_statement": research_query.problem_statement,
                    "required_expertise": research_query.required_expertise,
                    "methods": research_query.methods,
                    "disciplines": research_query.disciplines,
                },
                ensure_ascii=True,
            )
        )
    if constraints is not None:
        lines.append(f"Explicit constraints: {state_summary(constraints)}")
    lines.append("Candidate faculty (deterministic ranking):")
    used_ids: set[str] = set()
    for candidate in selected:
        member = faculty_by_id[candidate.scopus_id]
        lines.append(
            "\n"
            f"- scopus_id={candidate.scopus_id}; name={candidate.author_name}; "
            f"department={candidate.department}; rank={candidate.rank or 'unknown'}; "
            f"tenure={candidate.tenure or 'unknown'}; "
            f"h_index={member.h_index if member.h_index is not None else 'unknown'}; "
            f"h5_index={member.h5_index if member.h5_index is not None else 'unknown'}; "
            f"citation_count="
            f"{member.citation_count if member.citation_count is not None else 'unknown'}; "
            f"top_journal_quartile_pct="
            f"{member.top_journal_quartile_pct if member.top_journal_quartile_pct is not None else 'unknown'} "
            "(author-level only); "
            f"relevance={candidate.relevance:.3f}; recency={candidate.recency:.3f}; "
            f"coverage={candidate.coverage if candidate.coverage is not None else 'unknown'}; "
            f"metrics={candidate.metrics if candidate.metrics is not None else 'unknown'}; "
            f"matched_concepts={candidate.matched_concepts}; "
            f"evidence_ids={candidate.evidence_ids}"
        )
        # Include only a bounded number of excerpts per candidate to keep prompts
        # small and avoid sending the whole corpus to the LLM.
        for evidence_id in candidate.evidence_ids[:max_evidence_per_candidate]:
            item = evidence_by_id.get(evidence_id)
            if item is None or evidence_id in used_ids:
                continue
            used_ids.add(evidence_id)
            excerpt = " ".join(item.text.split())[:excerpt_chars]
            hits = matched_concepts(
                f"{item.metadata.get('title') or ''} {item.text}", concepts
            )
            lines.append(
                f'  <evidence id="{evidence_id}" source="publication" '
                f'faculty_ids="{item.metadata.get("faculty_ids_csv") or ""}" '
                f'matched_concepts="{"; ".join(hits)}">'
                f"{excerpt}</evidence>"
            )
    return "\n".join(lines), {key: evidence_by_id[key] for key in used_ids}


# Coverage is measured from cited evidence only, not from model prose.
def _compute_coverage(
    members: list[TeamMember],
    evidence_by_id: dict[str, RetrievedEvidence],
    research_query: ResearchQuery | None,
) -> dict[str, list[str]]:
    concepts = concept_list(research_query)
    coverage: dict[str, list[str]] = {concept: [] for concept in concepts}
    for member in members:
        member_text = " ".join(
            [
                evidence_by_id[evidence_id].text
                for evidence_id in member.evidence_ids
                if evidence_id in evidence_by_id
            ]
        )
        for concept in matched_concepts(member_text, concepts):
            coverage[concept].append(member.scopus_id)
    return coverage


def _llm_members_to_team(
    output: LLMTeamProposal,
    candidates_by_id: dict[str, FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
) -> list[TeamMember]:
    members: list[TeamMember] = []
    # Unknown IDs remain in the proposal so deterministic validation can reject
    # them with a clear issue instead of silently dropping a requested member.
    for item in output.members:
        candidate = candidates_by_id.get(item.scopus_id)
        faculty = faculty_by_id.get(item.scopus_id)
        evidence_titles: list[str] = []
        if candidate:
            evidence_by_id = dict(
                zip(candidate.evidence_ids, candidate.evidence_titles, strict=False)
            )
            evidence_titles = [
                evidence_by_id.get(evidence_id, evidence_id)
                for evidence_id in item.evidence_ids
            ]
        members.append(
            TeamMember(
                scopus_id=item.scopus_id,
                name=faculty.author_name if faculty else "Unknown",
                department=faculty.department if faculty else "Unknown",
                role=item.role,
                rationale=item.rationale,
                evidence_ids=item.evidence_ids,
                evidence_titles=evidence_titles,
                factual_claims=[],
                score=candidate.score if candidate else 0.0,
                score_components=candidate.score_components if candidate else {},
                metric_missing=candidate.metric_missing if candidate else [],
                inferred_suitability=True,
            )
        )
    return members


def _baseline_proposal(
    *,
    run_id: str,
    opportunity: Opportunity,
    candidates: list[FacultyCandidate],
    evidence: list[RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    team_size: int,
    constraints: UserConstraints | None,
    research_query: ResearchQuery | None,
    status: str,
    issues: list[str] | None = None,
    extra_limitations: list[str] | None = None,
) -> TeamProposal:
    # The baseline is always computed first so it is available on any LLM failure.
    members, pi_id = select_baseline_team(
        candidates,
        faculty_by_id,
        team_size=team_size,
        constraints=constraints,
    )
    evidence_by_id = {item.evidence_id: item for item in evidence}
    # These limitations are always shown; they are not model-generated claims.
    limitations = [
        "Suitability is inferred from publications and author-level metrics.",
        "Availability, leadership experience, and sponsor eligibility are not "
        "present in the source data.",
        "Top Journal Quartile % is an author-level metric, not a per-publication "
        "journal quartile.",
    ]
    limitations.extend(extra_limitations or [])
    return TeamProposal(
        run_id=run_id,
        opportunity_id=opportunity.opportunity_id,
        members=members,
        proposed_pi_id=pi_id,
        team_rationale=(
            "Deterministic baseline selected from the highest-ranked retrieved "
            "publication evidence, with explicit constraints, department diversity, "
            "and a tenure-aware PI preference."
        ),
        limitations=limitations,
        coverage=_compute_coverage(members, evidence_by_id, research_query),
        constraints_satisfied=not issues,
        validator_status=status,
        validation_issues=issues or [],
        baseline_team_ids=[member.scopus_id for member in members],
        llm_team_ids=[],
        used_llm=False,
    )


# One narrow wrapper makes it easy to count calls and mock generation in tests.
def _llm_call(
    *,
    llm_client: object,
    system: str,
    user: str,
) -> LLMTeamProposal:
    return llm_client.complete_structured(
        system=system,
        user=user,
        schema=LLMTeamProposal,
        model=config.stage_model("generate"),
    )


def generate_team_proposal(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    candidates: list[FacultyCandidate],
    evidence: list[RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    team_size: int,
    llm_client: object | None = None,
    research_query: ResearchQuery | None = None,
    constraints: UserConstraints | None = None,
    weights: ScoringWeights | None = None,
) -> TeamProposal:
    # Never ask for more members than the candidate pool can support.
    target_size = constraints.team_size if constraints else team_size
    expected_size = min(target_size, len(candidates))
    if expected_size == 0:
        raise RuntimeError("No eligible faculty candidates were retrieved")
    if constraints and len(constraints.required_members) > expected_size:
        raise ConstraintError(
            "Required members exceed the number of eligible retrieved candidates"
        )

    baseline = _baseline_proposal(
        run_id=run_id,
        opportunity=opportunity,
        candidates=candidates,
        evidence=evidence,
        faculty_by_id=faculty_by_id,
        team_size=expected_size,
        constraints=constraints,
        research_query=research_query,
        status="baseline",
    )
    if llm_client is None:
        return baseline

    # Build the bounded evidence packet only after the baseline exists, so a
    # packet-building failure can still return a usable team.
    packet, evidence_by_id = _evidence_packet(
        candidates,
        evidence,
        faculty_by_id,
        research_query,
        constraints,
    )
    system = load_prompt("generate_team")
    # Opportunity/focus text is untrusted and delimited; the prompt forbids using
    # it to change permissions or the candidate list.
    user_prompt = (
        "<opportunity>\n"
        f"Title: {opportunity.title}\n"
        f"Agency: {opportunity.agency}\n"
        f"Description:\n{(opportunity.description or '')[:1500]}\n"
        "</opportunity>\n\n"
        f"<user_focus>{focus or 'None'}</user_focus>\n\n"
        f"Requested team size: {expected_size}\n\n"
        f"{packet}\n\n"
        "Return the structured team proposal. Every member must come from the "
        "candidate shortlist and cite supplied evidence IDs."
    )
    candidates_by_id = {candidate.scopus_id: candidate for candidate in candidates}

    try:
        output = _llm_call(llm_client=llm_client, system=system, user=user_prompt)
    except Exception as exc:
        # Any generation failure falls back to the deterministic baseline.
        baseline.limitations.append(
            f"LLM generation unavailable ({type(exc).__name__}); deterministic "
            "baseline used."
        )
        baseline.validator_status = "fallback_after_llm_error"
        return baseline

    def proposal_from_output(
        repaired: LLMTeamProposal, *, call_count: int, repair_attempts: int
    ) -> TeamProposal:
        # One builder for the initial and repaired proposals so both stay
        # structurally identical.
        return TeamProposal(
            run_id=run_id,
            opportunity_id=opportunity.opportunity_id,
            members=_llm_members_to_team(repaired, candidates_by_id, faculty_by_id),
            proposed_pi_id=repaired.proposed_pi_id,
            team_rationale=repaired.team_rationale,
            limitations=repaired.limitations
            + [
                "Suitability is inferred from publication evidence and author-level "
                "metrics, not from availability or sponsor eligibility."
            ],
            coverage=_compute_coverage(
                _llm_members_to_team(repaired, candidates_by_id, faculty_by_id),
                evidence_by_id,
                research_query,
            ),
            baseline_team_ids=[member.scopus_id for member in baseline.members],
            llm_team_ids=[
                member.scopus_id
                for member in _llm_members_to_team(repaired, candidates_by_id, faculty_by_id)
            ],
            used_llm=True,
            llm_call_count=call_count,
            repair_attempts=repair_attempts,
        )

    def deterministic_issues(proposal: TeamProposal) -> list[str]:
        return validate_proposal(
            proposal,
            candidates=candidates_by_id,
            evidence_by_id=evidence_by_id,
            faculty_by_id=faculty_by_id,
            expected_size=expected_size,
            constraints=constraints,
        )

    def repair(proposal: TeamProposal, issues: list[str]) -> tuple[TeamProposal, list[str]]:
        """One repair attempt for any issue kind; returns the proposal + issues."""
        repair_user = (
            f"{user_prompt}\n\nPrevious proposal:\n{proposal.model_dump_json()}\n\n"
            "Validation issues to fix (a strict evidence verifier produced them; "
            "keep every rationale claim inside the cited evidence text):\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\n\nReturn a corrected structured team proposal."
        )
        repaired = _llm_call(
            llm_client=llm_client,
            system=system,
            user=repair_user,
        )
        next_call_count = proposal.llm_call_count + 1
        repaired_proposal = proposal_from_output(
            repaired,
            call_count=next_call_count,
            repair_attempts=proposal.repair_attempts + 1,
        )
        # Verifier attempts made so far still count on the repaired proposal.
        repaired_proposal.verifier_calls = proposal.verifier_calls
        return repaired_proposal, deterministic_issues(repaired_proposal)

    proposal = proposal_from_output(output, call_count=1, repair_attempts=0)
    # Deterministic validation runs before the optional LLM verifier.
    issues = deterministic_issues(proposal)

    # Allow exactly one repair attempt for deterministic issues.
    if issues:
        try:
            proposal, issues = repair(proposal, issues)
        except Exception:
            issues = issues + ["repair attempt failed"]

    # Only a deterministically valid proposal is worth an evidence-only verifier
    # call; this keeps the LLM budget focused.
    if not issues:
        verifier_issues = verify_with_llm(
            proposal,
            evidence_by_id,
            llm_client=llm_client,
        )
        proposal.verifier_calls = 1
        proposal.llm_call_count += 1
        if verifier_issues:
            # Grounding issues from the verifier get the same single repair
            # chance deterministic issues get, then one re-verification.
            try:
                proposal, issues = repair(proposal, verifier_issues)
                if not issues:
                    recheck = verify_with_llm(
                        proposal,
                        evidence_by_id,
                        llm_client=llm_client,
                    )
                    proposal.verifier_calls += 1
                    proposal.llm_call_count += 1
                    if recheck:
                        issues = recheck
            except Exception:
                issues = verifier_issues + ["repair attempt failed"]

    # Any remaining issue means the model cannot be trusted for this run; show the
    # baseline and report why.
    if issues:
        fallback = _baseline_proposal(
            run_id=run_id,
            opportunity=opportunity,
            candidates=candidates,
            evidence=evidence,
            faculty_by_id=faculty_by_id,
            team_size=expected_size,
            constraints=constraints,
            research_query=research_query,
            status=(
                "fallback_after_verifier_error"
                if proposal.verifier_calls
                else "fallback_after_validation_error"
            ),
            issues=issues,
            extra_limitations=[
                "The LLM proposal failed grounding/constraint validation; the "
                "deterministic baseline is shown instead."
            ],
        )
        # Carry the attempt counters onto the fallback so run metadata still
        # reports how many generator/verifier calls the rejected attempt used,
        # and which team the LLM proposed before validation rejected it.
        fallback.llm_call_count = proposal.llm_call_count
        fallback.verifier_calls = proposal.verifier_calls
        fallback.repair_attempts = proposal.repair_attempts
        fallback.llm_team_ids = proposal.llm_team_ids
        return fallback

    proposal.validator_status = "passed"
    proposal.constraints_satisfied = True
    return proposal
