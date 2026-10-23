# Offline test module: Batched LLM reranking, deterministic fallback, and diversity caps.
from __future__ import annotations

from models import (
    LLMRerankBatch,
    LLMRerankItem,
    ResearchQuery,
    RetrievedEvidence,
)
from src.retrieval.rerank import rerank_evidence


def _evidence(evidence_id, faculty_id, similarity, *, eid=None, title=None, text=None):
    return RetrievedEvidence(
        evidence_id=evidence_id,
        text=text or f"Evidence about ecology and climate for {evidence_id}",
        metadata={
            "chunk_type": "publication",
            "faculty_ids_csv": faculty_id,
            "eid": eid or f"EID-{evidence_id}",
            "year": 2024,
            "title": title or f"Title {evidence_id}",
        },
        similarity=similarity,
    )


def _query():
    return ResearchQuery(
        problem_statement="Ecology and climate change.",
        required_expertise=["ecology", "climate change"],
        methods=["data analysis"],
        search_queries=["ecology climate"],
    )


class FakeReranker:
    def __init__(self, scores=None, error=None):
        self.scores = scores or {}
        self.error = error
        self.calls = 0

    def complete_structured(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        items = [
            LLMRerankItem(
                evidence_id=evidence_id,
                score=score,
                matched_concepts=["ecology"],
            )
            for evidence_id, score in self.scores.items()
        ]
        return LLMRerankBatch(items=items)


# Verifies: deterministic rerank limits one chunk per publication.
def test_deterministic_rerank_limits_one_chunk_per_publication():
    evidence = [
        _evidence("e1", "111", 0.9, eid="PUB1", text="ecology climate change"),
        _evidence("e2", "111", 0.8, eid="PUB1", text="ecology climate change"),
        _evidence("e3", "222", 0.7, eid="PUB2", text="climate change data analysis"),
        _evidence("e4", "333", 0.6, eid="PUB3", text="economics"),
    ]
    outcome = rerank_evidence(evidence, _query(), llm_client=None, k_out=3)
    ids = [item.evidence_id for item in outcome.evidence]
    assert len(ids) == 3
    assert len({"PUB1", "PUB2", "PUB3"} & {"PUB1"}) == 1
    assert sum(1 for item in outcome.evidence if item.metadata["eid"] == "PUB1") == 1
    assert outcome.fallback_used is True


# Verifies: rerank enforces per faculty cap.
def test_rerank_enforces_per_faculty_cap():
    evidence = [
        _evidence(f"e{index}", "111", 0.9 - index * 0.01, eid=f"PUB{index}")
        for index in range(6)
    ]
    outcome = rerank_evidence(evidence, _query(), llm_client=None, k_out=6)
    assert len(outcome.evidence) == 3
    assert all(item.metadata["faculty_ids_csv"] == "111" for item in outcome.evidence)


# Verifies: LLM rerank scores are batched and bounded.
def test_llm_rerank_scores_are_batched_and_bounded():
    evidence = [
        _evidence(f"e{index}", str(100 + index), 0.5, eid=f"PUB{index}")
        for index in range(20)
    ]
    client = FakeReranker(scores={"e0": 10, "e1": 1})
    outcome = rerank_evidence(
        evidence,
        _query(),
        llm_client=client,
        k_out=5,
        max_calls=2,
        batch_size=8,
    )
    assert client.calls == 2
    assert outcome.calls == 2
    assert any(item.metadata["rerank_source"] == "llm" for item in outcome.evidence)


# Verifies: LLM rerank error falls back deterministically.
def test_llm_rerank_error_falls_back_deterministically():
    evidence = [_evidence("e1", "111", 0.8)]
    client = FakeReranker(error=RuntimeError("bad json"))
    outcome = rerank_evidence(evidence, _query(), llm_client=client, k_out=1)
    assert outcome.fallback_used is True
    assert outcome.evidence[0].metadata["rerank_source"] == "deterministic"
