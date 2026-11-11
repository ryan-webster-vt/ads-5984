"""Embedding adapters for the ``qwen3-embedding:8b`` model.

The live adapter targets any OpenAI-compatible ``/embeddings`` endpoint through
the shared HTTP client. The deterministic fixture adapter keeps tests and the
offline demo free of network access while preserving the same interface.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol

from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.integrations.base import BaseHttpClient


class EmbeddingProvider(Protocol):
    """Structural interface for document and query embeddings."""

    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of documents."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed one retrieval query."""
        ...


class DeterministicEmbeddingProvider:
    """Hashing-based embeddings for offline tests and demos."""

    model_name = "fixture-embedding"

    def __init__(self, *, dimension: int = 64) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self._dimension = dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one deterministic unit vector per input text."""
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Return the deterministic vector for a query."""
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingProvider:
    """Batch embeddings against an OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        client: BaseHttpClient,
        *,
        model_name: str = "qwen3-embedding:8b",
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ConfigurationError("embedding batch size must be at least 1")
        self._client = client
        self.model_name = model_name
        self._batch_size = batch_size

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed documents in deterministic batches."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        vectors = self._embed_batch([text])
        return vectors[0]

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = self._client.post_json(
            "embeddings",
            json={"model": self.model_name, "input": texts},
        )
        return _parse_embeddings(payload, expected=len(texts))


def _parse_embeddings(payload: Any, *, expected: int) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise IntegrationError("embedding response must be an object", retryable=False)
    data = payload.get("data")
    if not isinstance(data, list):
        raise IntegrationError("embedding response data must be a list", retryable=False)
    rows: list[tuple[int, list[float]]] = []
    for fallback_index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        embedding = item.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            continue
        try:
            vector = [float(value) for value in embedding]
        except (TypeError, ValueError):
            continue
        index = item.get("index")
        rows.append((index if isinstance(index, int) else fallback_index, vector))
    rows.sort(key=lambda row: row[0])
    vectors = [vector for _, vector in rows]
    if len(vectors) != expected:
        raise IntegrationError(
            "embedding response count did not match request",
            context={"expected": expected, "received": len(vectors)},
            retryable=False,
        )
    return vectors
