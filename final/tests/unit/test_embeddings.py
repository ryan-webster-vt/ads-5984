"""Tests for deterministic and HTTP embedding adapters."""

from __future__ import annotations

import httpx
import pytest

from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.integrations.base import HttpClientConfig, create_http_client
from upset_profiler.integrations.embeddings import (
    DeterministicEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)


def test_deterministic_embeddings_are_stable_and_normalized() -> None:
    provider = DeterministicEmbeddingProvider(dimension=8)
    first = provider.embed_documents(["duke rest advantage", "tcu road trip"])
    second = provider.embed_query("duke rest advantage")

    assert first[0] == second
    assert first[0] != first[1]
    assert len(first[0]) == 8
    norm = sum(value * value for value in first[0]) ** 0.5
    assert norm == pytest.approx(1.0)


def test_http_embedding_provider_batches_and_orders_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        assert '"model":"qwen3-embedding:8b"' in payload
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    client = create_http_client(
        HttpClientConfig(base_url="https://embeddings.test/v1"),
        transport=httpx.MockTransport(handler),
    )
    provider = OpenAICompatibleEmbeddingProvider(client, batch_size=2)

    assert provider.embed_documents(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert provider.embed_documents([]) == []


def test_http_embedding_provider_rejects_count_mismatch() -> None:
    client = create_http_client(
        HttpClientConfig(base_url="https://embeddings.test/v1"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        ),
    )
    provider = OpenAICompatibleEmbeddingProvider(client)
    with pytest.raises(IntegrationError, match="count"):
        provider.embed_query("query")


def test_embedding_provider_validates_batch_size() -> None:
    client = create_http_client(HttpClientConfig())
    with pytest.raises(ConfigurationError):
        OpenAICompatibleEmbeddingProvider(client, batch_size=0)
