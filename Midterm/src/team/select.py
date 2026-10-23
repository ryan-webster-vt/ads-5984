"""Deterministic candidate shortlist, constrained baseline team, and PI assignment."""

from __future__ import annotations

import config
from models import Faculty, FacultyCandidate, TeamMember, UserConstraints
from src.team.constraints import ConstraintError, member_allowed_by_state

# Non-PI roles are assigned in this fixed order so repeated builds are stable.
ROLE_SEQUENCE = (
    "Co-PI / Domain Expert",
    "Domain Expert",
    "Methods / Data Analysis Lead",
    "Collaboration Lead",
)


def _pi_fit(candidate: FacultyCandidate, constraints: UserConstraints | None = None) -> float:
    # An explicit user preference overrides the local tenure/tenure-track prior.
    if constraints and constraints.pi_preference == candidate.scopus_id:
        return 2.0
    # The bonus is a local VT assumption about PI readiness, not sponsor policy.
    tenure_bonus = 0.0
    if "Academic Tenure" in candidate.tenure and "Pending" not in candidate.tenure:
        tenure_bonus = 0.05
    elif "Pending" in candidate.tenure:
        tenure_bonus = 0.03
    metrics = candidate.metrics if candidate.metrics is not None else 0.5
    return 0.60 * candidate.score + 0.25 * metrics + 0.15 * candidate.recency + tenure_bonus


# The shortlist bounds what the LLM may choose from; selection never invents IDs.
def shortlist_candidates(
    candidates: list[FacultyCandidate],
    limit: int = config.MAX_SHORTLIST,
) -> list[FacultyCandidate]:
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.scopus_id))[:limit]


def _preferred(candidate: FacultyCandidate, constraints: UserConstraints | None) -> bool:
    # Department preferences are soft: preferred candidates are tried first, but
    # they do not exclude other departments.
    if constraints is None or not constraints.department_preferences:
        return False
    preferred = {value.lower() for value in constraints.department_preferences}
    return candidate.department.lower() in preferred


def select_baseline_team(
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
    team_size: int = config.DEFAULT_TEAM_SIZE,
    max_per_department: int = 2,
    *,
    constraints: UserConstraints | None = None,
) -> tuple[list[TeamMember], str]:
    """Select a stable, department-aware team from the deterministic ranking."""
    # Explicit state controls the size; the argument is a fallback for callers
    # without constraints.
    if constraints is not None:
        team_size = constraints.team_size
    allowed: list[FacultyCandidate] = []
    for candidate in candidates:
        member = faculty_by_id.get(candidate.scopus_id)
        if member is None:
            continue
        if constraints is not None and not member_allowed_by_state(member, constraints):
            continue
        allowed.append(candidate)
    ranked = sorted(allowed, key=lambda candidate: (-candidate.score, candidate.scopus_id))

    # Required members must be present in the retrieved candidate set; the
    # baseline never fabricates evidence for a requested person.
    required_ids = list(constraints.required_members) if constraints else []
    if len(required_ids) > team_size:
        raise ConstraintError(
            f"{len(required_ids)} required members exceed the team size of {team_size}"
        )
    by_id = {candidate.scopus_id: candidate for candidate in ranked}
    missing_required = [
        faculty_id for faculty_id in required_ids if faculty_id not in by_id
    ]
    if missing_required:
        names = [
            faculty_by_id[faculty_id].author_name
            for faculty_id in missing_required
            if faculty_id in faculty_by_id
        ]
        raise ConstraintError(
            "No eligible retrieved publication evidence for required member(s): "
            + ", ".join(names or missing_required)
        )

    selected: list[FacultyCandidate] = [
        by_id[faculty_id] for faculty_id in required_ids if faculty_id in by_id
    ]
    selected_ids = {candidate.scopus_id for candidate in selected}
    department_counts: dict[str, int] = {}
    for candidate in selected:
        department_counts[candidate.department] = (
            department_counts.get(candidate.department, 0) + 1
        )

    def add_candidate(candidate: FacultyCandidate) -> None:
        selected.append(candidate)
        selected_ids.add(candidate.scopus_id)
        department_counts[candidate.department] = (
            department_counts.get(candidate.department, 0) + 1
        )

    # Two-pass greedy selection: preferred departments first, then the full
    # ranking. This is explainable and stable, not a global optimizer.
    passes = (
        [candidate for candidate in ranked if _preferred(candidate, constraints)],
        ranked,
    )
    for pool in passes:
        for candidate in pool:
            if len(selected) >= team_size:
                break
            if candidate.scopus_id in selected_ids:
                continue
            if department_counts.get(candidate.department, 0) >= max_per_department:
                continue
            add_candidate(candidate)
        if len(selected) >= team_size:
            break

    # Final pass relaxes the department cap only if the team is still short.
    if len(selected) < team_size:
        for candidate in ranked:
            if len(selected) >= team_size:
                break
            if candidate.scopus_id not in selected_ids:
                add_candidate(candidate)

    if not selected:
        raise ConstraintError("No eligible faculty candidates were retrieved")

    # PI choice is deterministic: explicit preference first, then _pi_fit, with a
    # stable Scopus-ID tie-break.
    pi_candidate = sorted(
        selected,
        key=lambda candidate: (-_pi_fit(candidate, constraints), candidate.scopus_id),
    )[0]
    members: list[TeamMember] = []
    non_pi_index = 0
    for candidate in selected:
        faculty = faculty_by_id[candidate.scopus_id]
        if candidate.scopus_id == pi_candidate.scopus_id:
            role = "Proposed PI"
        else:
            role = ROLE_SEQUENCE[min(non_pi_index, len(ROLE_SEQUENCE) - 1)]
            non_pi_index += 1
        # Baselines cite real titles and explain the deterministic score; they do
        # not claim availability or sponsor eligibility.
        evidence_text = "; ".join(candidate.evidence_titles[:2])
        rationale = (
            f"Publication evidence includes: {evidence_text}. "
            f"Deterministic score {candidate.score:.2f} "
            f"(relevance {candidate.relevance:.2f}; recency {candidate.recency:.2f}"
            + (
                f"; coverage {candidate.coverage:.2f}"
                if candidate.coverage is not None
                else "; coverage unavailable"
            )
            + "). Suitability is inferred from publications, not from availability "
            "or sponsor eligibility."
        )
        members.append(
            TeamMember(
                scopus_id=candidate.scopus_id,
                name=candidate.author_name,
                department=faculty.department,
                role=role,
                rationale=rationale,
                evidence_ids=candidate.evidence_ids,
                evidence_titles=candidate.evidence_titles,
                factual_claims=[
                    {"field": "department", "value": faculty.department, "source": "roster"},
                    {"field": "tenure", "value": faculty.tenure, "source": "roster"},
                ],
                score=candidate.score,
                score_components=candidate.score_components,
                metric_missing=candidate.metric_missing,
                inferred_suitability=True,
            )
        )
    return members, pi_candidate.scopus_id
