from __future__ import annotations

from src.research.identity import (
    attribute_faculty_ids,
    build_eligible_faculty_index,
    conflict_groups,
    parse_author_ids,
)
from src.research.loaders import load_faculty, load_publications


def test_real_roster_identity_counts():
    faculty = load_faculty()
    assert len(faculty) == 501
    assert len({member.scopus_id for member in faculty}) == 500
    conflicts = conflict_groups(faculty)
    assert set(conflicts) == {"56940337900"}
    names = {member.author_name for member in conflicts["56940337900"]}
    assert names == {"Iliescu, Traian", "Driscoll, Anne R."}
    assert len(build_eligible_faculty_index(faculty)) == 499


def test_real_publication_counts_and_conflict_exclusion():
    faculty = load_faculty()
    eligible = build_eligible_faculty_index(faculty)
    publications = load_publications(eligible)
    assert len(publications) == 21485
    assert len({publication.eid for publication in publications}) == 21485
    assert sum(1 for publication in publications if publication.faculty_ids) == 20042
    assert all(
        "56940337900" not in publication.faculty_ids
        for publication in publications
    )
    assert sum(
        1 for publication in publications if len(publication.faculty_ids) >= 2
    ) == 1602


def test_author_id_parsing_and_complete_id_matching():
    assert parse_author_ids("123; 456 ;789") == ["123", "456", "789"]
    faculty = load_faculty()
    eligible = build_eligible_faculty_index(faculty)
    assert attribute_faculty_ids(["56330169100"], eligible) == ["56330169100"]
    assert attribute_faculty_ids(["5633016910"], eligible) == []
    assert attribute_faculty_ids(["563301691000"], eligible) == []
