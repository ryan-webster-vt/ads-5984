from __future__ import annotations

import pytest

import config
from src.research.chunking import chunk_faculty_profile, chunk_publication
from src.research.dedupe import dedupe_publications
from src.research.identity import build_eligible_faculty_index
from src.research.index import select_index_publications
from src.research.loaders import load_faculty, load_publications


@pytest.fixture(scope="module")
def corpus():
    faculty = load_faculty()
    eligible = build_eligible_faculty_index(faculty)
    raw = load_publications(eligible)
    canonical = dedupe_publications(raw)
    attributed = [publication for publication in canonical if publication.faculty_ids]
    return faculty, eligible, raw, canonical, attributed


def test_full_corpus_counts(corpus):
    faculty, eligible, raw, canonical, attributed = corpus
    assert len(faculty) == 501
    assert len({member.scopus_id for member in faculty}) == 500
    assert len(eligible) == 499
    assert len(raw) == 21485
    assert len(canonical) == 21428
    assert len(attributed) == 19986
    assert sum(1 for publication in raw if not publication.faculty_ids) == 1443
    assert len(canonical) - len(attributed) == 1442
    assert sum(1 for publication in attributed if len(publication.faculty_ids) >= 2) == 1602
    assert all(
        "56940337900" not in publication.faculty_ids for publication in attributed
    )


def test_full_index_selection_covers_every_attributed_work(corpus):
    _, _, _, _, attributed = corpus
    selected = select_index_publications(attributed)
    assert len(selected) == len(attributed)
    assert len({publication.eid for publication in selected}) == len(selected)


def test_every_selected_publication_chunks_with_stable_ids(corpus):
    _, eligible, _, _, attributed = corpus
    faculty_names = {
        scopus_id: member.author_name for scopus_id, member in eligible.items()
    }
    seen_ids: set[str] = set()
    total_chunks = 0
    for publication in select_index_publications(attributed):
        chunks = chunk_publication(publication, faculty_names)
        assert 1 <= len(chunks) <= config.MAX_ABSTRACT_CHUNKS
        assert chunks[0].evidence_id.startswith(f"pub::{publication.eid}::c")
        assert all(chunk.parent_id == publication.eid for chunk in chunks)
        assert all(publication.title in chunk.text for chunk in chunks)
        for chunk in chunks:
            assert chunk.evidence_id not in seen_ids
            seen_ids.add(chunk.evidence_id)
        total_chunks += len(chunks)
    assert total_chunks == 32393


def test_long_abstract_is_split_without_crossing_documents(corpus):
    _, eligible, _, _, attributed = corpus
    faculty_names = {
        scopus_id: member.author_name for scopus_id, member in eligible.items()
    }
    longest = max(
        attributed,
        key=lambda publication: len(publication.abstract or ""),
    )
    assert len(longest.abstract or "") > 4000
    chunks = chunk_publication(longest, faculty_names)
    assert 1 < len(chunks) <= config.MAX_ABSTRACT_CHUNKS
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.chunk_count == len(chunks) for chunk in chunks)
    assert all(chunk.eid == longest.eid for chunk in chunks)
    assert len({chunk.content_hash for chunk in chunks}) == len(chunks)


def test_profile_chunks_cover_every_eligible_faculty(corpus):
    _, eligible, _, _, _ = corpus
    chunks = [chunk_faculty_profile(member) for member in eligible.values()]
    assert len(chunks) == 499
    assert len({chunk.evidence_id for chunk in chunks}) == 499
    assert all(chunk.chunk_type == "faculty_profile" for chunk in chunks)
    assert all("availability" in chunk.text.lower() for chunk in chunks)
