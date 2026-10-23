from __future__ import annotations

from models import Faculty, Publication
from src.research.chunking import (
    chunk_faculty_profile,
    chunk_publication,
    split_text_sentence_aware,
)


def _faculty():
    return Faculty(
        scopus_id="12345678901",
        author_name="Example, Ada",
        department="Biological Sciences",
        h_index=12,
        h5_index=5,
        scholarly_output=40,
        citation_count=1000,
        top_journal_quartile_pct=50.0,
    )


def _publication(abstract: str | None):
    return Publication(
        eid="2-s2.0-123",
        doi="10.1000/example",
        title="Long-term ecological synthesis",
        year=2024,
        source_title="Journal of Examples",
        document_type="Article",
        abstract=abstract,
        faculty_ids=["12345678901"],
        is_attributed=True,
        provenance_url="https://example.org/record",
    )


def test_sentence_aware_split_is_bounded_and_stable():
    text = " ".join(f"Sentence number {index} has useful evidence." for index in range(200))
    chunks = split_text_sentence_aware(text, max_chars=300, overlap_chars=40, max_chunks=5)
    assert 1 < len(chunks) <= 5
    assert all(len(chunk) <= 360 for chunk in chunks)
    assert chunks == split_text_sentence_aware(
        text, max_chars=300, overlap_chars=40, max_chunks=5
    )


def test_publication_chunks_have_stable_ids_and_no_cross_document_text():
    publication = _publication("A" * 3000)
    chunks = chunk_publication(publication, {"12345678901": "Example, Ada"})
    assert len(chunks) > 1
    assert [chunk.evidence_id for chunk in chunks] == [
        f"pub::2-s2.0-123::c{index:02d}" for index in range(len(chunks))
    ]
    assert all("Long-term ecological synthesis" in chunk.text for chunk in chunks)
    assert all(chunk.faculty_ids == ["12345678901"] for chunk in chunks)


def test_missing_abstract_still_creates_metadata_chunk():
    chunks = chunk_publication(_publication(None), {"12345678901": "Example, Ada"})
    assert len(chunks) == 1
    assert chunks[0].has_abstract is False
    assert "No abstract" in chunks[0].text


def test_faculty_profile_contains_roster_facts_not_invented_bio():
    chunk = chunk_faculty_profile(_faculty())
    assert chunk.chunk_type == "faculty_profile"
    assert "H-index: 12" in chunk.text
    assert "availability" in chunk.text.lower()
    assert "Scopus ID: 12345678901" in chunk.text
