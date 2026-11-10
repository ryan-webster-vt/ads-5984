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

# Honorifics are ignored during name resolution but kept in the displayed roster
# name. Matching is still deterministic and requires a unique candidate.
TITLES = {"dr", "prof", "professor"}


class ConstraintError(RuntimeError):
    """Raised when a hard constraint cannot be satisfied."""


class AmbiguousMemberError(ConstraintError):
    # Ambiguity is surfaced to the user as a clarification request, not guessed.
    def __init__(self, reference: str, matches: list[str]) -> None:
        self.reference = reference
        self.matches = matches
        names = ", ".join(matches)
        super().__init__(f"'{reference}' is ambiguous; matches: {names}")


def initial_constraints(team_size: int = config.DEFAULT_TEAM_SIZE) -> UserConstraints:
    # The initial state is explicit and versioned; later turns patch a copy.
    return UserConstraints(team_size=team_size)


def coerce_constraints(value: object) -> UserConstraints:
    """Normalize state at an API boundary, including Streamlit hot-reload objects.

    Streamlit can keep session-state instances alive while re-importing modules
    during a code edit. Those old instances are duck-typed but are no longer an
    instance of the currently imported ``UserConstraints`` class, so Pydantic
    rejects them. Converting via ``model_dump`` gives the current class a plain
    mapping to validate.
    """
    if isinstance(value, UserConstraints):
        return value
    if isinstance(value, dict):
        return UserConstraints.model_validate(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return UserConstraints.model_validate(dump())
    raise ConstraintError(
        f"Unsupported constraint state type: {type(value).__name__}"
    )


def _name_tokens(value: str) -> list[str]:
    # Normalize punctuation/case only; never fuzzy-match against publication names.
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

    # Exact full-name matches win over token-subset matches so "Smith" can never
    # silently select a different Smith.
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
    # Only a single unambiguous resolution may be applied automatically.
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousMemberError(
            reference,
            [f"{faculty_by_id[faculty_id].author_name} ({faculty_id})" for faculty_id in matches],
        )
    raise ConstraintError(f"Could not resolve faculty reference: {reference}")


def is_tenure_track(member: Faculty) -> bool:
    # This is a local VT policy approximation, not sponsor eligibility.
    tenure = member.tenure.lower()
    appointment = member.appointment_type.lower()
    return (
        "academic tenure" in tenure
        or "tenure-track" in appointment
        or "tenured" in appointment
    )


def member_allowed_by_state(member: Faculty, state: UserConstraints) -> bool:
    # Hard exclusions are checked in code before any LLM sees the shortlist.
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
        # A failed resolution becomes a conflict, never a fabricated ID.
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
    state = coerce_constraints(state)
    conflicts: list[str] = []
    # Merge into a copy so the caller's previous state remains available in the
    # conversation history.
    updated = state.model_copy(deep=True)

    # Validate team size against global policy before accepting the patch.
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

    # Include/exclude of the same person is a hard conflict requiring clarification.
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

    # Expertise additions/removals are applied case-insensitively but displayed in
    # the user's original wording.
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

    # PI preference is resolved to an exact Scopus ID before it enters state.
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
    # Incrementing the version lets the UI and run metadata distinguish revisions.
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
    # Notes are bounded so a long conversation cannot grow state without limit.
    note = " ".join(note.split())
    if not note:
        return existing
    if not existing:
        return note
    return f"{existing} {note}"[:1200]


def patch_requests_new_expertise(patch: ConstraintPatch) -> bool:
    # Only these changes warrant a new retrieval pass; size/notes changes can reuse
    # the existing evidence packet.
    return bool(
        patch.add_expertise
        or patch.department_preferences
        or patch.add_required_members
    )


def state_summary(state: UserConstraints) -> str:
    # Human-readable state is shown in chat and included in run metadata.
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
