"""Deterministic user-constraint state, merges, and member resolution."""

from __future__ import annotations

import config
from models import (
    ConstraintPatch,
    Faculty,
    FacultyCandidate,
    FollowupResult,
    UserConstraints,
)
from src.research.identity import normalize_name

TITLES = {"dr", "prof", "professor"}


class ConstraintError(RuntimeError):
    """Raised when a hard constraint cannot be satisfied."""


class AmbiguousMemberError(ConstraintError):
    def __init__(self, reference: str, matches: list[str]) -> None:
        self.reference = reference
        self.matches = matches
        names = ", ".join(matches)
        super().__init__(f"'{reference}' is ambiguous; matches: {names}")


def initial_constraints(team_size: int = config.DEFAULT_TEAM_SIZE) -> UserConstraints:
    return UserConstraints(team_size=team_size)


def _name_tokens(value: str) -> list[str]:
    cleaned = normalize_name(value)
    return [
        token
        for token in cleaned.split()
        if token and token not in TITLES and token != "and"
    ]


def resolve_member_reference(reference: str, faculty_by_id: dict[str, Faculty]) -> str:
    """Resolve an explicit Scopus ID or a deterministic unique name match."""
    reference = " ".join(str(reference).split()).strip()
    if not reference:
        raise ConstraintError("Empty faculty reference")
    if reference in faculty_by_id:
        return reference

    exact: list[str] = []
    token_matches: list[str] = []
    reference_tokens = set(_name_tokens(reference))
    for faculty_id, member in faculty_by_id.items():
        member_tokens = set(_name_tokens(member.author_name))
        if normalize_name(reference) == normalize_name(member.author_name):
            exact.append(faculty_id)
        elif reference_tokens and reference_tokens.issubset(member_tokens):
            token_matches.append(faculty_id)

    matches = exact or token_matches
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousMemberError(
            reference,
            [f"{faculty_by_id[faculty_id].author_name} ({faculty_id})" for faculty_id in matches],
        )
    raise ConstraintError(f"Could not resolve faculty reference: {reference}")


def is_tenure_track(member: Faculty) -> bool:
    tenure = member.tenure.lower()
    appointment = member.appointment_type.lower()
    return (
        "academic tenure" in tenure
        or "tenure-track" in appointment
        or "tenured" in appointment
    )


def member_allowed_by_state(member: Faculty, state: UserConstraints) -> bool:
    if member.identity_conflict or not member.eligible_for_selection:
        return False
    if member.scopus_id in state.excluded_members:
        return False
    return not (state.tenure_track_only and not is_tenure_track(member))


def filter_candidates(
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
    state: UserConstraints,
) -> list[FacultyCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.scopus_id in faculty_by_id
        and member_allowed_by_state(faculty_by_id[candidate.scopus_id], state)
    ]


def _resolve_list(
    references: list[str],
    faculty_by_id: dict[str, Faculty],
    conflicts: list[str],
    *,
    label: str,
) -> list[str]:
    resolved: list[str] = []
    for reference in references:
        try:
            faculty_id = resolve_member_reference(reference, faculty_by_id)
        except ConstraintError as exc:
            conflicts.append(f"{label}: {exc}")
            continue
        if faculty_id not in resolved:
            resolved.append(faculty_id)
    return resolved


def apply_patch(
    state: UserConstraints,
    patch: ConstraintPatch,
    faculty_by_id: dict[str, Faculty],
) -> FollowupResult:
    """Merge a parsed patch into explicit state, preserving hard conflicts."""
    conflicts: list[str] = []
    updated = state.model_copy(deep=True)

    if patch.team_size is not None:
        if patch.team_size < config.MIN_TEAM_SIZE or patch.team_size > config.MAX_TEAM_SIZE:
            conflicts.append(
                f"team size must be between {config.MIN_TEAM_SIZE} and "
                f"{config.MAX_TEAM_SIZE}"
            )
        else:
            updated.team_size = patch.team_size

    added_required = _resolve_list(
        patch.add_required_members,
        faculty_by_id,
        conflicts,
        label="required member",
    )
    added_excluded = _resolve_list(
        patch.add_excluded_members,
        faculty_by_id,
        conflicts,
        label="excluded member",
    )
    removed_required = _resolve_list(
        patch.remove_required_members,
        faculty_by_id,
        conflicts,
        label="remove required member",
    )
    removed_excluded = _resolve_list(
        patch.remove_excluded_members,
        faculty_by_id,
        conflicts,
        label="remove excluded member",
    )

    required = [value for value in updated.required_members if value not in removed_required]
    excluded = [value for value in updated.excluded_members if value not in removed_excluded]
    for faculty_id in added_required:
        if faculty_id not in required:
            required.append(faculty_id)
    for faculty_id in added_excluded:
        if faculty_id not in excluded:
            excluded.append(faculty_id)

    both = set(required) & set(excluded)
    if both:
        names = [
            f"{faculty_by_id[faculty_id].author_name} ({faculty_id})"
            for faculty_id in sorted(both)
            if faculty_id in faculty_by_id
        ]
        conflicts.append(
            "A person cannot be both required and excluded: " + ", ".join(names)
        )
        required = [faculty_id for faculty_id in required if faculty_id not in both]
        excluded = [faculty_id for faculty_id in excluded if faculty_id not in both]

    if len(required) > updated.team_size:
        conflicts.append(
            f"{len(required)} required members exceed the requested team size "
            f"of {updated.team_size}"
        )

    updated.required_members = required
    updated.excluded_members = excluded

    removed_expertise = {value.lower() for value in patch.remove_expertise}
    expertise = [
        value
        for value in updated.add_expertise
        if value.lower() not in removed_expertise
    ]
    for value in patch.add_expertise:
        if value.lower() not in {item.lower() for item in expertise}:
            expertise.append(value)
    updated.add_expertise = expertise
    for value in patch.remove_expertise:
        updated.notes = _append_note(updated.notes, f"Removed expertise request: {value}")

    for value in patch.department_preferences:
        if value.lower() not in {item.lower() for item in updated.department_preferences}:
            updated.department_preferences.append(value)

    if patch.pi_preference:
        try:
            updated.pi_preference = resolve_member_reference(
                patch.pi_preference,
                faculty_by_id,
            )
        except ConstraintError as exc:
            conflicts.append(f"PI preference: {exc}")

    if patch.tenure_track_only is not None:
        updated.tenure_track_only = patch.tenure_track_only
    if patch.recency_preference:
        if patch.recency_preference not in {"recent", "balanced", "any"}:
            conflicts.append(f"unknown recency preference: {patch.recency_preference}")
        else:
            updated.recency_preference = patch.recency_preference
    if patch.notes:
        updated.notes = _append_note(updated.notes, patch.notes)

    updated.conflicts = conflicts
    updated.version = state.version + 1
    clarification = patch.clarification_needed
    if conflicts and not clarification:
        clarification = "Please clarify the conflicting constraints."

    return FollowupResult(
        patch=patch,
        state=updated,
        conflicts=conflicts,
        clarification_needed=clarification,
        applied=not conflicts,
        redo_retrieval=patch_requests_new_expertise(patch),
        assistant_message=state_summary(updated),
    )


def _append_note(existing: str, note: str) -> str:
    note = " ".join(note.split())
    if not note:
        return existing
    if not existing:
        return note
    return f"{existing} {note}"[:1200]


def patch_requests_new_expertise(patch: ConstraintPatch) -> bool:
    return bool(
        patch.add_expertise
        or patch.department_preferences
        or patch.add_required_members
    )


def state_summary(state: UserConstraints) -> str:
    parts = [f"Team size: {state.team_size}."]
    if state.required_members:
        parts.append(f"Required IDs: {', '.join(state.required_members)}.")
    if state.excluded_members:
        parts.append(f"Excluded IDs: {', '.join(state.excluded_members)}.")
    if state.add_expertise:
        parts.append(f"Added expertise: {', '.join(state.add_expertise)}.")
    if state.department_preferences:
        parts.append(
            f"Preferred departments: {', '.join(state.department_preferences)}."
        )
    if state.pi_preference:
        parts.append(f"PI preference: {state.pi_preference}.")
    if state.tenure_track_only:
        parts.append("Restricted to tenured/tenure-track faculty (local policy).")
    if state.notes:
        parts.append(f"Notes: {state.notes}")
    return " ".join(parts)
