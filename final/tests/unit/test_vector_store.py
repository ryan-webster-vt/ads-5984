"""Tests for the deterministic in-memory vector store."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from upset_profiler.errors import ConfigurationError, StorageError
from upset_profiler.models.evidence import DocumentChunk
from upset_profiler.storage.chroma import ChromaDocumentStore, InMemoryVectorStore


def _chunk(chunk_id: str, game_id: str, published_at: datetime | None) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        game_id=game_id,
        season="2024-2025",
        chunk_index=0,
        text=f"text for {chunk_id}",
        token_count=3,
        start_char=0,
        end_char=15,
        source_url="https://www.espn.com/story",
        source_domain="espn.com",
        published_at=published_at,
        whitelisted=True,
        document_hash="hash",
    )


def test_upsert_and_similarity_query() -> None:
    store = InMemoryVectorStore()
    chunks = [
        _chunk("chunk-a", "game-1", datetime(2025, 1, 1, tzinfo=UTC)),
        _chunk("chunk-b", "game-2", datetime(2025, 1, 2, tzinfo=UTC)),
    ]
    assert store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0]]) == 2
    assert store.count() == 2

    evidence = store.query([1.0, 0.0], query_text="rest advantage", top_k=2)
    assert [item.chunk.chunk_id for item in evidence] == ["chunk-a", "chunk-b"]
    assert [item.rank for item in evidence] == [1, 2]
    assert evidence[0].query == "rest advantage"


def test_query_filters_by_game_season_and_publication_time() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        [
            _chunk("chunk-a", "game-1", datetime(2025, 1, 1, tzinfo=UTC)),
            _chunk("chunk-b", "game-2", datetime(2025, 1, 3, tzinfo=UTC)),
        ],
        [[1.0, 0.0], [1.0, 0.0]],
    )

    by_game = store.query([1.0, 0.0], query_text="q", top_k=5, game_id="game-2")
    assert [item.chunk.chunk_id for item in by_game] == ["chunk-b"]

    before = store.query(
        [1.0, 0.0],
        query_text="q",
        top_k=5,
        published_before=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert [item.chunk.chunk_id for item in before] == ["chunk-a"]

    by_season = store.query([1.0, 0.0], query_text="q", top_k=5, season="2023-2024")
    assert by_season == []


def test_upsert_replaces_existing_chunk_ids() -> None:
    store = InMemoryVectorStore()
    chunk = _chunk("chunk-a", "game-1", None)
    store.upsert([chunk], [[1.0, 0.0]])
    store.upsert([chunk], [[0.0, 1.0]])
    assert store.count() == 1


def test_delete_document_and_validation_errors() -> None:
    store = InMemoryVectorStore()
    chunk = _chunk("chunk-a", "game-1", None)
    store.upsert([chunk], [[1.0, 0.0]])
    assert store.delete_document("doc-chunk-a") == 1
    assert store.count() == 0

    with pytest.raises(StorageError, match="counts differ"):
        store.upsert([chunk], [])
    with pytest.raises(StorageError, match="dimensions"):
        store.upsert([chunk], [[1.0, 0.0]])
        store.query([1.0], query_text="q", top_k=1)


def test_chroma_store_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = importlib.import_module

    def fake_import(name: str) -> object:
        if name == "chromadb":
            raise ImportError("chromadb is not installed")
        return real_import(name)

    monkeypatch.setattr(
        "upset_profiler.storage.chroma.importlib.import_module",
        fake_import,
    )
    with pytest.raises(ConfigurationError, match="rag"):
        ChromaDocumentStore("unused")
