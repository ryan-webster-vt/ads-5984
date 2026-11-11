"""Integration test for the optional ChromaDB vector store.

The test is skipped when the ``rag`` extra is not installed so the default
offline suite remains dependency-light.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from upset_profiler.models.evidence import DocumentChunk
from upset_profiler.storage.chroma import ChromaDocumentStore

pytest.importorskip("chromadb")


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        chunk_id="doc-1:0:abc",
        document_id="doc-1",
        game_id="game-1",
        season="2024-2025",
        chunk_index=0,
        text="Clemson enters Durham on short rest.",
        token_count=6,
        start_char=0,
        end_char=36,
        source_url="https://www.espn.com/story",
        source_domain="espn.com",
        published_at=datetime(2024, 12, 9, tzinfo=UTC),
        whitelisted=True,
        document_hash="hash-1",
    )


def test_chroma_persists_and_retrieves_citations(tmp_path: Path) -> None:
    store = ChromaDocumentStore(str(tmp_path / "chroma"), collection_name="test")
    chunk = _chunk()
    assert store.upsert([chunk], [[1.0, 0.0, 0.0]]) == 1
    assert store.count() == 1

    evidence = store.query(
        [1.0, 0.0, 0.0],
        query_text="rest advantage",
        top_k=3,
        game_id="game-1",
    )
    assert len(evidence) == 1
    assert evidence[0].chunk.source_url == chunk.source_url
    assert evidence[0].chunk.published_at == chunk.published_at
    assert store.delete_document("doc-1") == 1
    assert store.count() == 0
