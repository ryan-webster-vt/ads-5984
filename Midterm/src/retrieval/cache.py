"""Version-keyed retrieval cache with explicit invalidation and no-cache mode."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import config
from models import IndexManifest, ResearchQuery, RetrievedEvidence
from src.io_utils import read_json, write_json_atomic
from src.prompting import prompt_version
from src.retrieval.rerank import RerankOutcome
from src.retrieval.retrieve import retrieve_for_research_query


def cache_context_from_manifest(manifest: IndexManifest) -> dict[str, str]:
    """Return the index/config fields that must invalidate cached evidence."""
    return {
        "index_version": manifest.index_version,
        "collection_name": manifest.collection_name,
        "embedding_model": manifest.embedding_model,
        "corpus_hash": manifest.corpus_sha256,
        "chunker_version": manifest.chunker_version,
        "schema_version": manifest.schema_version,
    }


def retrieval_cache_key(
    research_query: ResearchQuery,
    *,
    cache_context: dict[str, str],
    k_in: int,
    k_out: int,
    union_cap: int = config.RETRIEVAL_UNION_CAP,
) -> str:
    """Hash every input that changes retrieval or reranking semantics."""
    payload = {
        "cache_version": config.RETRIEVAL_CACHE_VERSION,
        "context": cache_context,
        "query": {
            "problem_statement": research_query.problem_statement,
            "required_expertise": research_query.required_expertise,
            "methods": research_query.methods,
            "disciplines": research_query.disciplines,
            "search_queries": research_query.search_queries,
            "exclusions": research_query.exclusions,
            "prompt_version": research_query.prompt_version,
        },
        "k_in": k_in,
        "k_out": k_out,
        "union_cap": union_cap,
        "rerank_prompt_version": prompt_version("rerank"),
        "rerank_batch_size": config.RERANK_BATCH_SIZE,
        "max_rerank_calls": config.MAX_RERANK_CALLS,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class RetrievalCache:
    """Small on-disk cache for exact evidence packets, never raw source data."""

    directory: Path = config.RETRIEVAL_CACHE_DIR
    ttl_hours: float = config.RETRIEVAL_CACHE_TTL_HOURS

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = read_json(path)
        except (OSError, ValueError):
            return None
        if payload.get("cache_version") != config.RETRIEVAL_CACHE_VERSION:
            return None
        if payload.get("key") != key:
            return None
        if self.ttl_hours > 0:
            created_at = payload.get("created_at")
            if not created_at:
                return None
            try:
                created = datetime.fromisoformat(created_at)
            except (TypeError, ValueError):
                return None
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            now = now or datetime.now(UTC)
            if now - created > timedelta(hours=self.ttl_hours):
                return None
        return payload

    def put(
        self,
        key: str,
        evidence: list[RetrievedEvidence],
        outcome: RerankOutcome,
        *,
        cache_context: dict[str, str],
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        write_json_atomic(
            self._path(key),
            {
                "cache_version": config.RETRIEVAL_CACHE_VERSION,
                "key": key,
                "created_at": now.isoformat(),
                "context": cache_context,
                "outcome": {
                    "calls": outcome.calls,
                    "fallback_used": outcome.fallback_used,
                    "model_scored": outcome.model_scored,
                    "concepts": outcome.concepts,
                },
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
        )

    def clear(self) -> int:
        if not self.directory.exists():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed


def cached_retrieve(
    collection: object,
    research_query: ResearchQuery,
    *,
    llm_client: object | None,
    k_in: int = config.DEFAULT_K_IN,
    k_out: int = config.DEFAULT_K_OUT,
    union_cap: int = config.RETRIEVAL_UNION_CAP,
    cache: RetrievalCache | None = None,
    cache_context: dict[str, str] | None = None,
    use_cache: bool = True,
) -> tuple[list[RetrievedEvidence], RerankOutcome]:
    """Return retrieval evidence, reusing a compatible cache when allowed."""
    if not use_cache or cache is None or not cache_context:
        return retrieve_for_research_query(
            collection,
            research_query,
            llm_client=llm_client,
            k_in=k_in,
            k_out=k_out,
            union_cap=union_cap,
        )

    key = retrieval_cache_key(
        research_query,
        cache_context=cache_context,
        k_in=k_in,
        k_out=k_out,
        union_cap=union_cap,
    )
    payload = cache.get(key)
    if payload is not None:
        try:
            evidence = [
                RetrievedEvidence.model_validate(item)
                for item in payload.get("evidence", [])
            ]
        except ValueError:
            evidence = []
        if evidence:
            outcome_payload = payload.get("outcome") or {}
            return evidence, RerankOutcome(
                evidence=evidence,
                calls=0,
                fallback_used=bool(outcome_payload.get("fallback_used")),
                model_scored=int(outcome_payload.get("model_scored") or 0),
                concepts=list(outcome_payload.get("concepts") or []),
                cache_hit=True,
            )

    evidence, outcome = retrieve_for_research_query(
        collection,
        research_query,
        llm_client=llm_client,
        k_in=k_in,
        k_out=k_out,
        union_cap=union_cap,
    )
    # Do not cache a transient reranker failure; a later run should retry the LLM.
    if llm_client is None or not outcome.fallback_used:
        cache.put(key, evidence, outcome, cache_context=cache_context)
    return evidence, outcome
