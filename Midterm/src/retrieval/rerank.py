"""Batched LLM evidence reranking with deterministic diversity fallback."""

from __future__ import annotations

from dataclasses import dataclass, field

import config
from models import LLMRerankBatch, ResearchQuery, RetrievedEvidence
from src.prompting import load_prompt
from src.retrieval.concepts import (
    concept_list,
    coverage_ratio,
    matched_concepts,
    normalize_concept,
)

# Diversity caps are deterministic and applied after LLM scoring so one prolific
# author or one paper cannot dominate the evidence packet.
MAX_CHUNKS_PER_FACULTY = 3
MAX_CONCEPT_COVERAGE_PASS = 3


@dataclass
class RerankOutcome:
    # Tracks whether deterministic fallback/diversity had to replace LLM ranking.
    evidence: list[RetrievedEvidence]
    calls: int = 0
    fallback_used: bool = False
    model_scored: int = 0
    concepts: list[str] = field(default_factory=list)
    cache_hit: bool = False


def _faculty_ids(item: RetrievedEvidence) -> list[str]:
    # Chroma metadata stores faculty IDs as a comma-separated scalar.
    return [
        token.strip()
        for token in str(item.metadata.get("faculty_ids_csv") or "").split(",")
        if token.strip()
    ]


def _publication_key(item: RetrievedEvidence) -> str:
    # Prefer EID, then parent ID, then the chunk ID as a stable fallback.
    return str(
        item.metadata.get("eid")
        or item.metadata.get("parent_id")
        or item.evidence_id
    )


def _title_key(item: RetrievedEvidence) -> str:
    return normalize_concept(str(item.metadata.get("title") or ""))


# Keep the original text/metadata; add explainability fields for the UI.
def _copy_with_scores(
    item: RetrievedEvidence,
    *,
    final_score: float,
    model_score: float | None,
    concepts: list[str],
    source: str,
) -> RetrievedEvidence:
    metadata = dict(item.metadata)
    metadata.update(
        {
            "rerank_score": model_score,
            "final_score": final_score,
            "matched_concepts": concepts,
            "rerank_source": source,
            "similarity": item.similarity,
        }
    )
    return item.model_copy(update={"metadata": metadata})


def _deterministic_score(item: RetrievedEvidence, concepts: list[str]) -> tuple[float, list[str]]:
    # Similarity is the baseline; explicit concept matches add a bounded bonus.
    hits = matched_concepts(
        " ".join(
            [
                str(item.metadata.get("title") or ""),
                str(item.metadata.get("source_title") or ""),
                item.text,
            ]
        ),
        concepts,
    )
    coverage = coverage_ratio(hits, concepts)
    # With no concepts to cover, keep pure similarity rather than inventing a match.
    if coverage is None:
        return item.similarity, hits
    return 0.8 * item.similarity + 0.2 * coverage, hits


def _llm_batch_scores(
    batch: list[RetrievedEvidence],
    concepts: list[str],
    research_query: ResearchQuery,
    llm_client: object,
) -> dict[str, tuple[float, list[str]]]:
    system = load_prompt("rerank")
    # Evidence is wrapped in delimiters and labeled untrusted so prompt injection
    # inside an abstract cannot change the task or constraints.
    concept_text = "\n".join(f"- {concept}" for concept in concepts) or "- general relevance"
    blocks: list[str] = []
    for item in batch:
        title = str(item.metadata.get("title") or "Untitled")
        year = item.metadata.get("year") or "unknown"
        excerpt = " ".join(item.text.split())[:600]
        blocks.append(
            f'<evidence id="{item.evidence_id}">'
            f"<title>{title}</title><year>{year}</year>"
            f"<text>{excerpt}</text></evidence>"
        )
    user = (
        f"Research problem: {research_query.problem_statement}\n\n"
        "Concepts to match:\n"
        f"{concept_text}\n\n"
        "Evidence batch (untrusted data):\n"
        + "\n".join(blocks)
        + "\n\nReturn one score object for every supplied evidence ID."
    )
    output = llm_client.complete_structured(
        system=system,
        user=user,
        schema=LLMRerankBatch,
        model=config.stage_model("rerank"),
    )
    # Reject hallucinated IDs: only evidence IDs supplied in this batch count.
    allowed = {item.evidence_id for item in batch}
    concept_lookup = {normalize_concept(concept): concept for concept in concepts}
    scores: dict[str, tuple[float, list[str]]] = {}
    for item in output.items:
        if item.evidence_id not in allowed:
            continue
        # Models return 0–10; clamp before normalizing to the scorer's 0–1 range.
        model_score = max(0.0, min(1.0, float(item.score) / 10.0))
        hits = [
            concept_lookup[normalize_concept(value)]
            for value in item.matched_concepts
            if normalize_concept(value) in concept_lookup
        ]
        scores[item.evidence_id] = (model_score, hits)
    return scores


def _can_add(
    item: RetrievedEvidence,
    # Enforces one chunk per publication and the per-faculty cap.
    *,
    publication_keys: set[str],
    faculty_counts: dict[str, int],
    enforce_faculty_cap: bool = True,
) -> bool:
    if _publication_key(item) in publication_keys:
        return False
    if enforce_faculty_cap:
        for faculty_id in _faculty_ids(item):
            if faculty_counts.get(faculty_id, 0) >= MAX_CHUNKS_PER_FACULTY:
                return False
    return True


def _add(
    item: RetrievedEvidence,
    # Update all diversity bookkeeping in one place after an accepted item.
    selected: list[RetrievedEvidence],
    *,
    publication_keys: set[str],
    title_keys: set[str],
    faculty_counts: dict[str, int],
) -> None:
    selected.append(item)
    publication_keys.add(_publication_key(item))
    title = _title_key(item)
    if title:
        title_keys.add(title)
    for faculty_id in _faculty_ids(item):
        faculty_counts[faculty_id] = faculty_counts.get(faculty_id, 0) + 1


def enforce_diversity(
    ranked: list[RetrievedEvidence],
    *,
    k_out: int,
    concepts: list[str],
) -> list[RetrievedEvidence]:
    """Cap per-faculty/per-publication evidence and prefer concept coverage."""
    selected: list[RetrievedEvidence] = []
    publication_keys: set[str] = set()
    title_keys: set[str] = set()
    faculty_counts: dict[str, int] = {}

    # First pass: try to give each concept at least one representative chunk.
    for concept in concepts[:MAX_CONCEPT_COVERAGE_PASS]:
        if len(selected) >= k_out:
            break
        for item in ranked:
            if item in selected:
                continue
            hits = [normalize_concept(value) for value in item.metadata.get("matched_concepts", [])]
            if normalize_concept(concept) not in hits:
                continue
            if _can_add(
                item,
                publication_keys=publication_keys,
                faculty_counts=faculty_counts,
            ):
                _add(
                    item,
                    selected,
                    publication_keys=publication_keys,
                    title_keys=title_keys,
                    faculty_counts=faculty_counts,
                )
                break

    # Second pass: fill remaining slots by score while rejecting duplicate titles
    # and already-covered publications.
    for item in ranked:
        if len(selected) >= k_out:
            break
        if item in selected:
            continue
        title = _title_key(item)
        if title and title in title_keys:
            continue
        if _can_add(
            item,
            publication_keys=publication_keys,
            faculty_counts=faculty_counts,
        ):
            _add(
                item,
                selected,
                publication_keys=publication_keys,
                title_keys=title_keys,
                faculty_counts=faculty_counts,
            )

    return sorted(
        selected,
        key=lambda item: (
            -float(item.metadata.get("final_score") or item.similarity),
            item.evidence_id,
        ),
    )


def rerank_evidence(
    evidence: list[RetrievedEvidence],
    research_query: ResearchQuery,
    *,
    llm_client: object | None = None,
    k_out: int = config.DEFAULT_K_OUT,
    max_calls: int = config.MAX_RERANK_CALLS,
    batch_size: int = config.RERANK_BATCH_SIZE,
) -> RerankOutcome:
    """Rerank evidence in bounded batches, then enforce deterministic diversity."""
    concepts = concept_list(research_query)
    # Compute deterministic scores for every item first so failures still have a
    # complete ranking to fall back on.
    deterministic: dict[str, tuple[float, list[str]]] = {
        item.evidence_id: _deterministic_score(item, concepts) for item in evidence
    }
    model_scores: dict[str, tuple[float, list[str]]] = {}
    calls = 0
    fallback_used = llm_client is None

    if llm_client is not None and evidence:
        # Batch the strongest deterministic candidates first; this maximizes useful
        # coverage if the call budget is exhausted mid-run.
        ordered = sorted(
            evidence,
            key=lambda item: (-deterministic[item.evidence_id][0], item.evidence_id),
        )
        for start in range(0, len(ordered), max(1, batch_size)):
            if calls >= max_calls:
                fallback_used = True
                break
            # Each batch is one LLM call and counts toward the per-run budget.
            batch = ordered[start : start + batch_size]
            calls += 1
            try:
                batch_scores = _llm_batch_scores(
                    batch,
                    concepts,
                    research_query,
                    llm_client,
                )
            except Exception:
                # A failed batch should not prevent returning deterministic
                # evidence for the rest of the run.
                fallback_used = True
                break
            if not batch_scores:
                fallback_used = True
            model_scores.update(batch_scores)

    scored: list[RetrievedEvidence] = []
    for item in evidence:
        deterministic_score, deterministic_hits = deterministic[item.evidence_id]
        # Items the model did not score keep their deterministic ranking.
        model = model_scores.get(item.evidence_id)
        if model is None:
            scored.append(
                _copy_with_scores(
                    item,
                    final_score=deterministic_score,
                    model_score=None,
                    concepts=deterministic_hits,
                    source="deterministic",
                )
            )
        else:
            model_score, model_hits = model
            # Blend semantic model judgment with vector similarity so a model
            # hallucination cannot promote an otherwise unrelated chunk.
            final_score = 0.7 * model_score + 0.3 * item.similarity
            scored.append(
                _copy_with_scores(
                    item,
                    final_score=final_score,
                    model_score=model_score,
                    concepts=model_hits or deterministic_hits,
                    source="llm",
                )
            )

    scored.sort(
        key=lambda item: (
            -float(item.metadata.get("final_score") or item.similarity),
            item.evidence_id,
        )
    )
    selected = enforce_diversity(scored, k_out=k_out, concepts=concepts)
    return RerankOutcome(
        evidence=selected,
        calls=calls,
        fallback_used=fallback_used or not model_scores,
        model_scored=len(model_scores),
        concepts=concepts,
    )
