# Offline test module: Constraint merging, member resolution, ambiguity, and explicit state.
from __future__ import annotations

import pytest

from models import ConstraintPatch, Faculty, FacultyCandidate
from src.team.constraints import (
    AmbiguousMemberError,
    ConstraintError,
    apply_patch,
    filter_candidates,
    initial_constraints,
    is_tenure_track,
    resolve_member_reference,
)


def _faculty():
    return {
        "1": Faculty(
            scopus_id="1",
            author_name="Carey, Cayelan C.",
            department="Biological Sciences",
            tenure="Academic Tenure",
        ),
        "2": Faculty(
            scopus_id="2",
            author_name="Smith, John",
            department="Chemistry",
            tenure="Academic Tenure",
        ),
        "3": Faculty(
            scopus_id="3",
            author_name="Jones, Amy",
            department="Physics",
            tenure="Not Tenured Personnel",
        ),
        "4": Faculty(
            scopus_id="4",
            author_name="Barrett, John E.",
            department="Biological Sciences",
            tenure="Academic Tenure Pending",
        ),
    }


def _candidate(scopus_id, department):
    return FacultyCandidate(
        scopus_id=scopus_id,
        author_name=scopus_id,
        department=department,
        score=0.5,
        relevance=0.5,
    )


# Verifies: member resolution is exact or unambiguous.
def test_member_resolution_is_exact_or_unambiguous():
    faculty = _faculty()
    assert resolve_member_reference("2", faculty) == "2"
    assert resolve_member_reference("Carey, Cayelan C.", faculty) == "1"
    assert resolve_member_reference("Dr. Cayelan Carey", faculty) == "1"
    with pytest.raises(ConstraintError):
        resolve_member_reference("Nobody, Nemo", faculty)


# Verifies: ambiguous member resolution requires clarification.
def test_ambiguous_member_resolution_requires_clarification():
    faculty = _faculty()
    faculty["5"] = Faculty(
        scopus_id="5",
        author_name="Smith, Jane",
        department="Chemistry",
        tenure="Academic Tenure",
    )
    with pytest.raises(AmbiguousMemberError):
        resolve_member_reference("Smith", faculty)


# Verifies: size tenure and department constraints apply.
def test_size_tenure_and_department_constraints_apply():
    faculty = _faculty()
    state = initial_constraints(4)
    result = apply_patch(
        state,
        ConstraintPatch(
            team_size=3,
            tenure_track_only=True,
            department_preferences=["Chemistry"],
        ),
        faculty,
    )
    assert result.applied
    assert result.state.team_size == 3
    assert result.state.tenure_track_only is True
    assert result.state.department_preferences == ["Chemistry"]
    filtered = filter_candidates(
        [_candidate("1", "Biological Sciences"), _candidate("3", "Physics")],
        faculty,
        result.state,
    )
    assert [candidate.scopus_id for candidate in filtered] == ["1"]


# Verifies: include and exclude same person is a hard conflict.
def test_include_and_exclude_same_person_is_a_hard_conflict():
    faculty = _faculty()
    result = apply_patch(
        initial_constraints(4),
        ConstraintPatch(
            add_required_members=["Smith, John"],
            add_excluded_members=["2"],
        ),
        faculty,
    )
    assert not result.applied
    assert result.conflicts
    assert result.clarification_needed
    assert "2" not in result.state.required_members
    assert "2" not in result.state.excluded_members


# Verifies: unknown member and invalid size are conflicts.
def test_unknown_member_and_invalid_size_are_conflicts():
    result = apply_patch(
        initial_constraints(4),
        ConstraintPatch(team_size=9, add_required_members=["Nobody, Nemo"]),
        _faculty(),
    )
    assert not result.applied
    assert len(result.conflicts) >= 2


# Verifies: tenure track local policy.
def test_tenure_track_local_policy():
    faculty = _faculty()
    assert is_tenure_track(faculty["1"])
    assert is_tenure_track(faculty["4"])
    assert not is_tenure_track(faculty["3"])
