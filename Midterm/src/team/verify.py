"""Deterministic grounding and constraint checks for a team proposal."""

from __future__ import annotations

import re

from models import (
    Faculty,
    LLMVerification,
    RetrievedEvidence,
    TeamProposal,
    UserConstraints,
)
from src.prompting import load_prompt
from src.team.constraints import is_tenure_track

FORBIDDEN_CLAIMS = (
    "available to",
    "availability",
    "leadership experience",
    "has led",
    "has experience leading",
    "sponsor eligibility",
    "eligible to apply",
    "guaranteed",
)

NEGATION_MARKERS = re.compile(
    r"\b(no|not|without|cannot|can't|doesn't|does not|isn't|is not|unknown|unverified)\b",
    re.IGNORECASE,
)

QUARTILE_CLAIM = re.compile(r"\btop\s+(?:journal\s+)?quartile\b", re.IGNORECASE)
H_INDEX_CLAIM = re.compile(r"\bh[- ]?index\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE)
H5_CLAIM = re.compile(r"\bh5[- ]?index\s*(?:of|is|=|:)?\s*(\d+)", re.IGNORECASE)
CITATION_CLAIM = re.compile(
    r"\bcitations?\s*(?:count|total)?\s*(?:of|is|=|:)?\s*(\d+)",
    re.IGNORECASE,
)


def _positive_claim(text: str, phrase: str) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if phrase in sentence.lower() and not NEGATION_MARKERS.search(sentence):
            return True
    return False


def _is_pi_role(role: str) -> bool:
    return role.strip().lower() in {"proposed pi", "pi", "principal investigator"}


def _evidence_faculty_ids(evidence: RetrievedEvidence) -> set[str]:
    return {
        token.strip()
        for token in str(evidence.metadata.get("faculty_ids_csv") or "").split(",")
        if token.strip()
    }


def validate_proposal(
    proposal: TeamProposal,
    *,
    candidates: dict[str, object],
    evidence_by_id: dict[str, RetrievedEvidence],
    faculty_by_id: dict[str, Faculty],
    expected_size: int,
    required_ids: set[str] | None = None,
    excluded_ids: set[str] | None = None,
    constraints: UserConstraints | None = None,
) -> list[str]:
    if constraints is not None:
        required_ids = set(constraints.required_members)
        excluded_ids = set(constraints.excluded_members)
    required_ids = required_ids or set()
    excluded_ids = excluded_ids or set()
    issues: list[str] = []
    member_ids = [member.scopus_id for member in proposal.members]

    if len(member_ids) != expected_size:
        issues.append(
            f"team size is {len(member_ids)}; expected {expected_size}"
        )
    if len(member_ids) != len(set(member_ids)):
        issues.append("team contains duplicate members")
    if proposal.proposed_pi_id not in member_ids:
        issues.append("proposed PI is not a team member")
    missing_required = required_ids - set(member_ids)
    if missing_required:
        issues.append(f"missing required members: {sorted(missing_required)}")
    included_excluded = excluded_ids & set(member_ids)
    if included_excluded:
        issues.append(f"excluded members included: {sorted(included_excluded)}")
    pi_roles = [
        member.scopus_id
        for member in proposal.members
        if _is_pi_role(member.role)
    ]
    if len(pi_roles) > 1:
        issues.append("more than one member is labeled Proposed PI")
    for member in proposal.members:
        if member.scopus_id == proposal.proposed_pi_id and not _is_pi_role(member.role):
            issues.append("proposed PI is not labeled as Proposed PI")
    if constraints is not None and constraints.tenure_track_only:
        for member in proposal.members:
            faculty = faculty_by_id.get(member.scopus_id)
            if faculty is not None and not is_tenure_track(faculty):
                issues.append(
                    f"member is not tenured/tenure-track under local policy: "
                    f"{member.scopus_id}"
                )

    for member in proposal.members:
        if member.scopus_id not in candidates:
            issues.append(f"member not in retrieved candidates: {member.scopus_id}")
            continue
        if member.scopus_id not in faculty_by_id:
            issues.append(f"member not in eligible roster: {member.scopus_id}")
            continue
        if not member.evidence_ids:
            issues.append(f"member has no evidence: {member.scopus_id}")
        for evidence_id in member.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                issues.append(
                    f"unresolved evidence ID for {member.scopus_id}: {evidence_id}"
                )
                continue
            if member.scopus_id not in _evidence_faculty_ids(evidence):
                issues.append(
                    f"evidence {evidence_id} is not attributed to {member.scopus_id}"
                )
        for claim in FORBIDDEN_CLAIMS:
            if _positive_claim(member.rationale, claim):
                issues.append(
                    f"unsupported claim in rationale for {member.scopus_id}: {claim}"
                )
        if QUARTILE_CLAIM.search(member.rationale):
            issues.append(
                f"unsupported journal-quartile claim for {member.scopus_id}"
            )
        faculty = faculty_by_id[member.scopus_id]
        h_match = H_INDEX_CLAIM.search(member.rationale)
        if (
            h_match
            and faculty.h_index is not None
            and int(h_match.group(1)) != faculty.h_index
        ):
            issues.append(
                f"h-index claim does not match roster for {member.scopus_id}"
            )
        h5_match = H5_CLAIM.search(member.rationale)
        if (
            h5_match
            and faculty.h5_index is not None
            and int(h5_match.group(1)) != faculty.h5_index
        ):
            issues.append(
                f"H5-index claim does not match roster for {member.scopus_id}"
            )
        citation_match = CITATION_CLAIM.search(member.rationale)
        if (
            citation_match
            and faculty.citation_count is not None
            and int(citation_match.group(1)) != faculty.citation_count
        ):
            issues.append(
                f"citation claim does not match roster for {member.scopus_id}"
            )

    return sorted(set(issues))


def verify_with_llm(
    proposal: TeamProposal,
    evidence_by_id: dict[str, RetrievedEvidence],
    *,
    llm_client: object | None,
    excerpt_chars: int = 700,
) -> list[str]:
    """One evidence-only verifier call; deterministic checks already ran."""
    if llm_client is None:
        return []
    system = load_prompt("verify_team")
    evidence_blocks: list[str] = []
    for evidence_id in sorted(
        {evidence_id for member in proposal.members for evidence_id in member.evidence_ids}
    ):
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        excerpt = " ".join(item.text.split())[:excerpt_chars]
        evidence_blocks.append(
            f'<evidence id="{evidence_id}" '
            f'faculty_ids="{item.metadata.get("faculty_ids_csv") or ""}">'
            f"{excerpt}</evidence>"
        )
    user = (
        "Proposal to audit:\n"
        f"{proposal.model_dump_json()}\n\n"
        "Evidence packet (untrusted data):\n"
        + ("\n".join(evidence_blocks) or "(no evidence resolved)")
        + "\n\nReturn the structured verification object."
    )
    try:
        output = llm_client.complete_structured(
            system=system,
            user=user,
            schema=LLMVerification,
        )
    except Exception:
        return []
    issues = [issue.issue.strip() for issue in output.issues if issue.issue.strip()]
    if not output.approved and not issues:
        return ["verifier did not approve the proposal"]
    return issues
