# Offline test module: EID/DOI/title deduplication and deterministic canonical selection.
from __future__ import annotations

from models import Publication
from src.research.dedupe import dedupe_publications


def _publication(**overrides):
    data = {
        "eid": "2-s2.0-1",
        "doi": "10.1000/example",
        "title": "A study of things",
        "year": 2020,
        "cited_by": 1,
        "faculty_ids": ["111"],
        "is_attributed": True,
    }
    data.update(overrides)
    return Publication(**data)


# Verifies: canonical selection prefers citations then EID.
def test_canonical_selection_prefers_citations_then_eid():
    records = [
        _publication(eid="2-s2.0-2", cited_by=5),
        _publication(eid="2-s2.0-1", cited_by=5),
        _publication(eid="2-s2.0-3", cited_by=1),
    ]
    canonical = dedupe_publications(records)
    assert len(canonical) == 1
    assert canonical[0].eid == "2-s2.0-1"
    assert canonical[0].alias_eids == ["2-s2.0-1", "2-s2.0-2", "2-s2.0-3"]


# Verifies: same DOI different title is kept and flagged.
def test_same_doi_different_title_is_kept_and_flagged():
    records = [
        _publication(eid="2-s2.0-1", title="First title"),
        _publication(eid="2-s2.0-2", title="Different title"),
    ]
    canonical = dedupe_publications(records)
    assert len(canonical) == 2
    assert all("doi_conflict" in item.data_quality_flags for item in canonical)


# Verifies: no DOI uses title and year.
def test_no_doi_uses_title_and_year():
    records = [
        _publication(eid="2-s2.0-1", doi=None, title="Same", year=2020),
        _publication(eid="2-s2.0-2", doi=None, title="Same", year=2020),
        _publication(eid="2-s2.0-3", doi=None, title="Same", year=2021),
    ]
    canonical = dedupe_publications(records)
    assert len(canonical) == 2
