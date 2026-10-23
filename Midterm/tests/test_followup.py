from __future__ import annotations

from models import Faculty, ResearchQuery
from src.team.constraints import initial_constraints
from src.team.followup import (
    apply_followup,
    augment_research_query,
    deterministic_parse_patch,
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


def test_replace_member_with_a_chemist_excludes_and_prefers_chemistry():
    result = apply_followup(
        "Replace Dr. Carey with a chemist.",
        initial_constraints(4),
        _faculty(),
    )
    assert result.applied
    assert result.state.excluded_members == ["1"]
    assert "Chemistry" in result.state.department_preferences
    assert result.redo_retrieval is True


def test_add_expertise_and_keep_team_size():
    result = apply_followup(
        "Add climate-change expertise and keep the team at four.",
        initial_constraints(3),
        _faculty(),
    )
    assert result.applied
    assert result.state.team_size == 4
    assert "climate-change" in result.state.add_expertise


def test_shrink_to_three():
    patch = deterministic_parse_patch("Shrink to three.")
    assert patch.team_size == 3
    result = apply_followup("Shrink to three.", initial_constraints(4), _faculty())
    assert result.state.team_size == 3


def test_include_and_exclude_same_member_requires_clarification():
    result = apply_followup(
        "Include Dr. Smith and exclude Dr. Smith.",
        initial_constraints(4),
        _faculty(),
    )
    assert not result.applied
    assert result.conflicts
    assert result.clarification_needed


def test_only_tenure_track_faculty_sets_local_policy():
    result = apply_followup(
        "Only tenure-track faculty.",
        initial_constraints(4),
        _faculty(),
    )
    assert result.applied
    assert result.state.tenure_track_only is True


def test_required_member_by_name_is_resolved_to_scopus_id():
    result = apply_followup(
        "Include Dr. Barrett.",
        initial_constraints(4),
        _faculty(),
    )
    assert result.applied
    assert result.state.required_members == ["4"]
    assert result.redo_retrieval is True


def test_unmapped_message_asks_for_clarification():
    result = apply_followup("Make it better somehow.", initial_constraints(4), _faculty())
    assert not result.applied
    assert result.clarification_needed


def test_augment_research_query_adds_bounded_query():
    query = ResearchQuery(
        problem_statement="Ecology",
        required_expertise=["ecology"],
        search_queries=["ecology", "climate"],
    )
    state = initial_constraints(4)
    state.add_expertise = ["climate change", "stable isotopes"]
    augmented = augment_research_query(query, state)
    assert "climate change" in augmented.required_expertise
    assert len(augmented.search_queries) == 3
    assert any("stable isotopes" in value for value in augmented.search_queries)
