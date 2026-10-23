# Offline test module: Retrieval-cache keys, TTL, invalidation, and no-cache behavior.
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from models import ResearchQuery, RetrievedEvidence
from src.retrieval import cache as cache_module
from src.retrieval.cache import (
    RetrievalCache,
    cache_context_from_manifest,
    cached_retrieve,
    retrieval_cache_key,
)
from src.retrieval.rerank import RerankOutcome

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
CONTEXT = {
    "index_version": "full-v1",
    "collection_name": "research_full_v1",
    "embedding_model": "qwen3-embedding:8b",
    "corpus_hash": "abc123",
    "chunker_version": "1",
    "schema_version": "2",
}


def _query(problem: str = "Ecology and climate.") -> ResearchQuery:
    return ResearchQuery(
        problem_statement=problem,
        required_expertise=["ecology"],
        search_queries=["ecology climate"],
        prompt_version="optimize_query-v1",
    )


def _evidence() -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            evidence_id="e1",
            text="Ecology climate evidence.",
            metadata={"chunk_type": "publication", "faculty_ids_csv": "1"},
            similarity=0.9,
        )
    ]


def _outcome(*, fallback: bool = False) -> RerankOutcome:
    return RerankOutcome(
        evidence=_evidence(),
        calls=2,
        fallback_used=fallback,
        model_scored=1,
        concepts=["ecology", "climate"],
    )


# Verifies: cache key invalidates on model index query and k.
def test_cache_key_invalidates_on_model_index_query_and_k():
    base = retrieval_cache_key(_query(), cache_context=CONTEXT, k_in=30, k_out=12)
    assert base == retrieval_cache_key(
        _query(), cache_context=CONTEXT, k_in=30, k_out=12
    )
    assert base != retrieval_cache_key(
        _query("Different problem."), cache_context=CONTEXT, k_in=30, k_out=12
    )
    assert base != retrieval_cache_key(
        _query(), cache_context={**CONTEXT, "embedding_model": "other"}, k_in=30, k_out=12
    )
    assert base != retrieval_cache_key(
        _query(), cache_context={**CONTEXT, "index_version": "full-v2"}, k_in=30, k_out=12
    )
    assert base != retrieval_cache_key(
        _query(), cache_context=CONTEXT, k_in=20, k_out=12
    )


# Verifies: cache roundtrip and ttl.
def test_cache_roundtrip_and_ttl(tmp_path):
    cache = RetrievalCache(tmp_path, ttl_hours=24)
    key = retrieval_cache_key(_query(), cache_context=CONTEXT, k_in=30, k_out=12)
    cache.put(key, _evidence(), _outcome(), cache_context=CONTEXT, now=NOW)

    fresh = cache.get(key, now=NOW + timedelta(hours=23))
    assert fresh is not None
    assert fresh["evidence"][0]["evidence_id"] == "e1"
    assert cache.get(key, now=NOW + timedelta(hours=25)) is None


# Verifies: cached retrieve hit skips retrieval.
def test_cached_retrieve_hit_skips_retrieval(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake_retrieve(collection, research_query, **kwargs):
        calls.append(1)
        return _evidence(), _outcome()

    monkeypatch.setattr(cache_module, "retrieve_for_research_query", fake_retrieve)
    cache = RetrievalCache(tmp_path, ttl_hours=24)
    first, first_outcome = cached_retrieve(
        object(),
        _query(),
        llm_client=None,
        cache=cache,
        cache_context=CONTEXT,
    )
    second, second_outcome = cached_retrieve(
        object(),
        _query(),
        llm_client=None,
        cache=cache,
        cache_context=CONTEXT,
    )
    assert len(calls) == 1
    assert first_outcome.cache_hit is False
    assert second_outcome.cache_hit is True
    assert second_outcome.calls == 0
    assert [item.evidence_id for item in first] == [item.evidence_id for item in second]


# Verifies: no cache bypasses persistent cache.
def test_no_cache_bypasses_persistent_cache(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake_retrieve(collection, research_query, **kwargs):
        calls.append(1)
        return _evidence(), _outcome()

    monkeypatch.setattr(cache_module, "retrieve_for_research_query", fake_retrieve)
    cache = RetrievalCache(tmp_path, ttl_hours=24)
    for _ in range(2):
        _, outcome = cached_retrieve(
            object(),
            _query(),
            llm_client=None,
            cache=cache,
            cache_context=CONTEXT,
            use_cache=False,
        )
        assert outcome.cache_hit is False
    assert len(calls) == 2
    assert list(tmp_path.glob("*.json")) == []


# Verifies: transient reranker fallback is not cached.
def test_transient_reranker_fallback_is_not_cached(tmp_path, monkeypatch):
    def fake_retrieve(collection, research_query, **kwargs):
        return _evidence(), _outcome(fallback=True)

    monkeypatch.setattr(cache_module, "retrieve_for_research_query", fake_retrieve)
    cache = RetrievalCache(tmp_path, ttl_hours=24)
    cached_retrieve(
        object(),
        _query(),
        llm_client=object(),
        cache=cache,
        cache_context=CONTEXT,
    )
    assert list(tmp_path.glob("*.json")) == []


# Verifies: cache context from manifest uses index metadata.
def test_cache_context_from_manifest_uses_index_metadata():
    class Manifest:
        index_version = "full-v1"
        collection_name = "research_full_v1"
        embedding_model = "qwen3-embedding:8b"
        corpus_sha256 = "abc123"
        chunker_version = "1"
        schema_version = "2"

    assert cache_context_from_manifest(Manifest()) == CONTEXT
