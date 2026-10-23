"""Deterministic candidate shortlist, baseline team, and PI assignment."""

from __future__ import annotations

from models import Faculty, FacultyCandidate, TeamMember

ROLE_SEQUENCE = (
    "Proposed PI",
    "Co-PI / Domain Expert",
    "Domain Expert",
    "Methods / Data Analysis Lead",
)


def _pi_fit(candidate: FacultyCandidate) -> float:
    tenure_bonus = 0.0
    if "Academic Tenure" in candidate.tenure and "Pending" not in candidate.tenure:
        tenure_bonus = 0.05
    elif "Pending" in candidate.tenure:
        tenure_bonus = 0.03
    metrics = candidate.metrics if candidate.metrics is not None else 0.5
    return 0.60 * candidate.score + 0.25 * metrics + 0.15 * candidate.recency + tenure_bonus


def shortlist_candidates(
    candidates: list[FacultyCandidate],
    limit: int = 8,
) -> list[FacultyCandidate]:
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.scopus_id))[:limit]


def select_baseline_team(
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
    team_size: int = 3,
    max_per_department: int = 2,
) -> tuple[list[TeamMember], str]:
    """Select a stable, department-aware team from the deterministic ranking."""
    ranked = shortlist_candidates(candidates, limit=max(len(candidates), 1))
    selected: list[FacultyCandidate] = []
    department_counts: dict[str, int] = {}

    for candidate in ranked:
        if len(selected) >= team_size:
            break
        if department_counts.get(candidate.department, 0) >= max_per_department:
            continue
        selected.append(candidate)
        department_counts[candidate.department] = (
            department_counts.get(candidate.department, 0) + 1
        )

    if len(selected) < team_size:
        selected_ids = {candidate.scopus_id for candidate in selected}
        for candidate in ranked:
            if len(selected) >= team_size:
                break
            if candidate.scopus_id not in selected_ids:
                selected.append(candidate)
                selected_ids.add(candidate.scopus_id)

    if not selected:
        raise RuntimeError("No eligible faculty candidates were retrieved")

    pi_candidate = sorted(
        selected,
        key=lambda candidate: (-_pi_fit(candidate), candidate.scopus_id),
    )[0]
    members: list[TeamMember] = []
    for index, candidate in enumerate(selected):
        faculty = faculty_by_id[candidate.scopus_id]
        role = (
            "Proposed PI"
            if candidate.scopus_id == pi_candidate.scopus_id
            else ROLE_SEQUENCE[min(index + 1, len(ROLE_SEQUENCE) - 1)]
        )
        evidence_text = "; ".join(candidate.evidence_titles[:2])
        rationale = (
            f"Publication evidence includes: {evidence_text}. "
            f"Relevance score {candidate.score:.2f}; "
            f"author-level metrics and recency are recorded in the evidence panel. "
            "Suitability is inferred from publications, not from availability or "
            "sponsor eligibility."
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
                score=candidate.score,
                inferred_suitability=True,
            )
        )
    return members, pi_candidate.scopus_id
