"""Cited-document vector stores.

SQLite owns structured records; ChromaDB owns cited unstructured evidence. This
module keeps both the real Chroma adapter and a deterministic in-memory adapter
behind one interface so tests and offline demos never require the optional
``chromadb`` dependency.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from upset_profiler.config import Settings
from upset_profiler.errors import ConfigurationError, StorageError
from upset_profiler.models.common import UtcDatetime
from upset_profiler.models.evidence import DocumentChunk, RetrievedEvidence


class PolicyFilter(Protocol):
    """Subset of the source whitelist used by retrieval."""

    def is_allowed_url(self, url: str) -> bool:
        """Return whether a URL passes the source policy."""
        ...


class DocumentVectorStore(Protocol):
    """Structural interface for chunk upsert and filtered retrieval."""

    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        """Insert or update chunks and return the number written."""
        ...

    def query(
        self,
        embedding: Sequence[float],
        *,
        query_text: str,
        top_k: int,
        game_id: str | None = None,
        season: str | None = None,
        published_before: UtcDatetime | None = None,
    ) -> list[RetrievedEvidence]:
        """Return the nearest visible chunks ranked by similarity."""
        ...

    def count(self) -> int:
        """Return the number of stored chunks."""
        ...

    def delete_document(self, document_id: str) -> int:
        """Delete every chunk belonging to a document and return the count."""
        ...


def _validate_vectors(
    chunks: Sequence[DocumentChunk],
    embeddings: Sequence[Sequence[float]],
) -> list[tuple[float, ...]]:
    if len(chunks) != len(embeddings):
        raise StorageError(
            "chunk and embedding counts differ",
            context={"chunks": len(chunks), "embeddings": len(embeddings)},
        )
    vectors: list[tuple[float, ...]] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        if chunk.chunk_id == "":
            raise StorageError("chunk_id must be non-empty")
        vector = tuple(float(value) for value in embedding)
        if not vector:
            raise StorageError("embedding vectors must be non-empty")
        vectors.append(vector)
    return vectors


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise StorageError(
            "embedding dimensions differ",
            context={"left": len(left), "right": len(right)},
        )
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class InMemoryVectorStore:
    """Deterministic in-process store used by tests and offline ingestion."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[DocumentChunk, tuple[float, ...]]] = {}

    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        """Insert or replace chunks by deterministic chunk id."""
        vectors = _validate_vectors(chunks, embeddings)
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._items[chunk.chunk_id] = (chunk, vector)
        return len(chunks)

    def query(
        self,
        embedding: Sequence[float],
        *,
        query_text: str,
        top_k: int,
        game_id: str | None = None,
        season: str | None = None,
        published_before: UtcDatetime | None = None,
    ) -> list[RetrievedEvidence]:
        """Return nearest chunks after applying metadata filters."""
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not query_text.strip():
            raise ValueError("query_text must be non-empty")
        query_vector = tuple(float(value) for value in embedding)
        scored: list[tuple[float, DocumentChunk]] = []
        for chunk, vector in self._items.values():
            if game_id is not None and chunk.game_id != game_id:
                continue
            if season is not None and chunk.season != season:
                continue
            if (
                published_before is not None
                and chunk.published_at is not None
                and chunk.published_at >= published_before
            ):
                continue
            scored.append((_cosine(query_vector, vector), chunk))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            RetrievedEvidence(
                query=query_text,
                chunk=chunk,
                score=score,
                rank=index + 1,
            )
            for index, (score, chunk) in enumerate(scored[:top_k])
        ]

    def count(self) -> int:
        """Return the number of stored chunks."""
        return len(self._items)

    def delete_document(self, document_id: str) -> int:
        """Delete all chunks for a document."""
        chunk_ids = [
            chunk_id
            for chunk_id, (chunk, _) in self._items.items()
            if chunk.document_id == document_id
        ]
        for chunk_id in chunk_ids:
            del self._items[chunk_id]
        return len(chunk_ids)


class ChromaDocumentStore:
    """ChromaDB-backed store for cited documents and chunks."""

    def __init__(
        self,
        path: str,
        *,
        collection_name: str = "research_documents",
    ) -> None:
        try:
            chromadb = importlib.import_module("chromadb")
        except ImportError as exc:
            raise ConfigurationError(
                "chromadb is not installed; install the 'rag' extra",
                context={"extra": "rag"},
            ) from exc
        try:
            self._client = chromadb.PersistentClient(path=path)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:  # pragma: no cover - depends on Chroma internals
            raise StorageError(
                "failed to open Chroma collection",
                context={"path": path, "collection": collection_name},
            ) from exc

    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        """Upsert chunks with deterministic ids and filterable metadata."""
        vectors = _validate_vectors(chunks, embeddings)
        if not chunks:
            return 0
        ids = [chunk.chunk_id for chunk in chunks]
        metadatas = [self._metadata(chunk) for chunk in chunks]
        try:
            self._collection.upsert(
                ids=ids,
                embeddings=[list(vector) for vector in vectors],
                documents=[chunk.text for chunk in chunks],
                metadatas=metadatas,
            )
        except Exception as exc:  # pragma: no cover - depends on Chroma internals
            raise StorageError("failed to upsert chunks into Chroma") from exc
        return len(chunks)

    def query(
        self,
        embedding: Sequence[float],
        *,
        query_text: str,
        top_k: int,
        game_id: str | None = None,
        season: str | None = None,
        published_before: UtcDatetime | None = None,
    ) -> list[RetrievedEvidence]:
        """Return nearest chunks using Chroma's filtered cosine search."""
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not query_text.strip():
            raise ValueError("query_text must be non-empty")
        if self.count() == 0:
            return []
        where = self._where(
            game_id=game_id,
            season=season,
            published_before=published_before,
        )
        try:
            result = self._collection.query(
                query_embeddings=[[float(value) for value in embedding]],
                n_results=top_k,
                where=where or None,
                include=["metadatas", "distances"],
            )
        except Exception as exc:  # pragma: no cover - depends on Chroma internals
            raise StorageError("failed to query Chroma") from exc

        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]
        evidence: list[RetrievedEvidence] = []
        for metadata, distance in zip(metadatas[0], distances[0], strict=False):
            if not isinstance(metadata, dict):
                continue
            payload = metadata.get("chunk_json")
            if not isinstance(payload, str):
                continue
            chunk = DocumentChunk.model_validate_json(payload)
            score = 1.0 - float(distance)
            evidence.append(
                RetrievedEvidence(
                    query=query_text,
                    chunk=chunk,
                    score=score,
                    rank=len(evidence) + 1,
                )
            )
        evidence.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return [
            item.model_copy(update={"rank": rank})
            for rank, item in enumerate(evidence, start=1)
        ]

    def count(self) -> int:
        """Return the number of chunks in the collection."""
        try:
            return int(self._collection.count())
        except Exception as exc:  # pragma: no cover - depends on Chroma internals
            raise StorageError("failed to count Chroma chunks") from exc

    def delete_document(self, document_id: str) -> int:
        """Delete all chunks for a document."""
        try:
            existing = self._collection.get(where={"document_id": document_id})
        except Exception as exc:  # pragma: no cover - depends on Chroma internals
            raise StorageError("failed to look up Chroma document") from exc
        ids = existing.get("ids") or []
        if ids:
            self._collection.delete(ids=list(ids))
        return len(ids)

    def _metadata(self, chunk: DocumentChunk) -> dict[str, str | int | float | bool]:
        retrieved_at = chunk.metadata.get("retrieved_at")
        metadata: dict[str, str | int | float | bool] = {
            "game_id": chunk.game_id,
            "document_id": chunk.document_id,
            "source_url": str(chunk.source_url),
            "source_domain": chunk.source_domain,
            "retrieved_at": retrieved_at if isinstance(retrieved_at, str) else "",
            "document_hash": chunk.document_hash,
            "chunk_index": chunk.chunk_index,
            "whitelisted": chunk.whitelisted,
            "chunk_json": chunk.model_dump_json(),
        }
        if chunk.season is not None:
            metadata["season"] = chunk.season
        if chunk.published_at is not None:
            metadata["published_at"] = int(chunk.published_at.timestamp())
        return metadata

    def _where(
        self,
        *,
        game_id: str | None,
        season: str | None,
        published_before: datetime | None,
    ) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = []
        if game_id is not None:
            clauses.append({"game_id": game_id})
        if season is not None:
            clauses.append({"season": season})
        if published_before is not None:
            clauses.append({"published_at": {"$lt": int(published_before.timestamp())}})
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses} if clauses else {}


def build_chroma_store(settings: Settings) -> ChromaDocumentStore:
    """Build the persistent Chroma store from typed settings."""
    return ChromaDocumentStore(
        str(settings.storage.chroma_path),
        collection_name=settings.storage.chroma_collection,
    )
