"""Tests for deterministic recursive chunking."""

from __future__ import annotations

from datetime import UTC, datetime

from upset_profiler.models.research import SourceDocument
from upset_profiler.rag.chunking import ChunkingConfig, RecursiveChunker, count_tokens


def _document(content: str) -> SourceDocument:
    return SourceDocument(
        document_id="doc-1",
        game_id="game-1",
        url="https://www.espn.com/story",
        source_domain="espn.com",
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
        retrieved_at=datetime(2025, 1, 2, tzinfo=UTC),
        content=content,
        content_hash="hash-1",
    )


def test_chunking_is_deterministic_and_offset_consistent() -> None:
    content = (
        "First paragraph has five.\n\n"
        "Second paragraph has five.\n\n"
        "Third paragraph ends here."
    )
    chunker = RecursiveChunker(ChunkingConfig(chunk_size_tokens=5, overlap_tokens=1))
    document = _document(content)

    first = chunker.chunk_document(document, season="2024-2025")
    second = chunker.chunk_document(document, season="2024-2025")

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len(first) >= 3
    for chunk in first:
        assert content[chunk.start_char : chunk.end_char] == chunk.text
        assert chunk.document_id == "doc-1"
        assert chunk.game_id == "game-1"
        assert chunk.season == "2024-2025"
        assert chunk.document_hash == "hash-1"
        assert chunk.metadata["retrieved_at"] == "2025-01-02T00:00:00+00:00"
        assert chunk.token_count == count_tokens(chunk.text)


def test_oversized_sentences_overlap_on_word_windows() -> None:
    content = " ".join(f"word{index}" for index in range(20))
    chunker = RecursiveChunker(ChunkingConfig(chunk_size_tokens=8, overlap_tokens=3))
    chunks = chunker.chunk_document(_document(content))

    assert len(chunks) >= 3
    assert all(chunk.token_count <= 8 for chunk in chunks)
    first_words = set(chunks[0].text.split())
    second_words = set(chunks[1].text.split())
    assert first_words & second_words


def test_document_without_content_produces_no_chunks() -> None:
    document = SourceDocument(
        document_id="hash-only",
        game_id="game-1",
        url="https://www.espn.com/story",
        source_domain="espn.com",
        retrieved_at=datetime(2025, 1, 2, tzinfo=UTC),
        content_hash="hash-only",
    )
    assert RecursiveChunker().chunk_document(document) == []


def test_config_rejects_invalid_overlap() -> None:
    try:
        ChunkingConfig(chunk_size_tokens=10, overlap_tokens=10)
    except ValueError as exc:
        assert "overlap_tokens" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected invalid chunking configuration to fail")
