"""Shared team-build pipeline: retrieve, score, generate, and persist metadata."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import config
from models import (
    Faculty,
    FacultyCandidate,
    IndexManifest,
    Opportunity,
    ResearchQuery,
    RetrievedEvidence,
    RunMetadata,
    TeamProposal,
    UserConstraints,
)
from src.io_utils import write_json_atomic
from src.llm import BudgetedLLMClient, ensure_budgeted
from src.prompting import prompt_versions
from src.redaction import redact_secrets
from src.retrieval.cache import (
    RetrievalCache,
    cache_context_from_manifest,
    cached_retrieve,
)
from src.team.constraints import filter_candidates
from src.team.generate import generate_team_proposal
from src.team.scoring import ScoringWeights, score_faculty_candidates


@dataclass
class BuildResult:
    # ``payload`` is the exact JSON persisted under data/runs; the other fields
    # let callers render without re-parsing it.
    payload: dict
    evidence: list[RetrievedEvidence]
    candidates: list[FacultyCandidate]
    research_query: ResearchQuery
    proposal: TeamProposal
    metadata: RunMetadata


def new_run_id() -> str:
    # Short IDs are convenient in filenames and logs while remaining unique enough
    # for a single app deployment.
    return uuid.uuid4().hex[:12]


def _budget_snapshot(llm_client: object | None) -> dict:
    # Only the per-run wrapper exposes a complete budget; raw clients return none.
    if isinstance(llm_client, BudgetedLLMClient):
        return llm_client.budget_snapshot
    return {}


def retrieve_and_score(
    *,
    collection: object,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    llm_client: object | None,
    k_in: int = config.DEFAULT_K_IN,
    k_out: int = config.DEFAULT_K_OUT,
    use_cache: bool = True,
    cache_context: dict[str, str] | None = None,
) -> tuple[list[RetrievedEvidence], list[FacultyCandidate], int, bool, bool]:
    # Cache exact evidence lists only; generation and validation always rerun.
    cache = None
    if use_cache and config.RETRIEVAL_CACHE_ENABLED:
        cache = RetrievalCache(config.RETRIEVAL_CACHE_DIR)
    evidence, outcome = cached_retrieve(
        collection,
        research_query,
        llm_client=llm_client,
        k_in=k_in,
        k_out=k_out,
        cache=cache,
        cache_context=cache_context,
        use_cache=use_cache,
    )
    # Explicit constraint overrides are applied before deterministic filtering.
    weights = ScoringWeights.from_override(constraints.weights_override)
    candidates = score_faculty_candidates(
        evidence,
        faculty_by_id,
        research_query=research_query,
        weights=weights,
        recency_preference=constraints.recency_preference,
    )
    # Hard constraints are applied after scoring so excluded members never reach
    # the shortlist, even if the LLM would otherwise choose them.
    candidates = filter_candidates(candidates, faculty_by_id, constraints)
    return (
        evidence,
        candidates,
        outcome.calls,
        outcome.fallback_used,
        outcome.cache_hit,
    )


def _candidate_payloads(
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
) -> list[dict]:
    payloads: list[dict] = []
    # Attach roster facts separately from inferred candidate scores so the UI can
    # label each value by source.
    for candidate in candidates:
        item = candidate.model_dump(mode="json")
        member = faculty_by_id.get(candidate.scopus_id)
        if member is not None:
            item["roster"] = {
                "department": member.department,
                "rank": member.rank,
                "tenure": member.tenure,
                "appointment_type": member.appointment_type,
                "h_index": member.h_index,
                "h5_index": member.h5_index,
                "citation_count": member.citation_count,
                "top_journal_quartile_pct": member.top_journal_quartile_pct,
            }
        payloads.append(item)
    return payloads


def build_run_payload(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    manifest: IndexManifest,
    proposal: TeamProposal,
    evidence: list[RetrievedEvidence],
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
    elapsed_ms: int,
    llm_client: object | None,
    rerank_calls: int,
    rerank_fallback: bool,
    rerank_cache_hit: bool,
    use_cache: bool,
    turns: list[dict] | None = None,
    conversation: list[dict] | None = None,
    errors: list[str] | None = None,
) -> tuple[dict, RunMetadata]:
    weights = ScoringWeights.from_override(constraints.weights_override)
    # Metadata is intentionally exhaustive: a run should be explainable without
    # re-running the pipeline.
    metadata = RunMetadata(
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        opportunity_id=opportunity.opportunity_id,
        index_version=manifest.index_version,
        corpus_hash=manifest.corpus_sha256,
        embedding_model=manifest.embedding_model,
        chat_model=getattr(llm_client, "model", "unavailable"),
        stage_models=(
            config.stage_models(getattr(llm_client, "model", "unavailable"))
            if llm_client is not None
            else {}
        ),
        query=research_query.problem_statement,
        search_queries=research_query.search_queries,
        research_query=research_query.model_dump(mode="json"),
        k_in=config.DEFAULT_K_IN,
        k_out=config.DEFAULT_K_OUT,
        team_size=constraints.team_size,
        prompt_versions=prompt_versions(*config.PROMPT_NAMES),
        cache_hits=1 if rerank_cache_hit else 0,
        cache_enabled=use_cache,
        llm_calls=getattr(llm_client, "calls", 0),
        rerank_calls=rerank_calls,
        # Counts attempts, including a proposal the verifier later rejected;
        # `fallback_used` already says whether the final team is the baseline.
        generator_calls=1 if proposal.used_llm or proposal.llm_call_count else 0,
        verifier_calls=proposal.verifier_calls,
        repair_attempts=proposal.repair_attempts,
        token_usage=getattr(llm_client, "token_usage", {}) or {},
        llm_budget=_budget_snapshot(llm_client),
        latency_ms=elapsed_ms,
        runtime_budget_ms=int(config.RUNTIME_BUDGETS["team_build_p95_ms"]),
        # Errors are already redacted by the caller.
        errors=list(errors or []),
        validation_issues=proposal.validation_issues,
        fallback_used=not proposal.used_llm,
        weights=weights.as_dict(),
        constraints=constraints.model_dump(mode="json"),
        ranked_evidence_ids=[item.evidence_id for item in evidence],
        baseline_team=proposal.baseline_team_ids,
        final_team=[member.scopus_id for member in proposal.members],
        coverage=proposal.coverage,
    )
    # Persist inputs, scores, evidence, and outputs together for auditability.
    payload = {
        "metadata": metadata.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "candidates": _candidate_payloads(candidates, faculty_by_id),
        "research_query": research_query.model_dump(mode="json"),
        "constraints": constraints.model_dump(mode="json"),
        "focus": focus,
        "rerank": {
            "calls": rerank_calls,
            "fallback_used": rerank_fallback,
            "cache_hit": rerank_cache_hit,
        },
        "cache": {"enabled": use_cache, "hit": rerank_cache_hit},
        "turns": turns or [],
        "conversation": conversation or [],
    }
    return payload, metadata


def build_team_run(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    collection: object,
    manifest: IndexManifest,
    llm_client: object | None,
    evidence: list[RetrievedEvidence] | None = None,
    candidates: list[FacultyCandidate] | None = None,
    rerank_calls: int = 0,
    rerank_fallback: bool = False,
    rerank_cache_hit: bool = False,
    use_cache: bool = True,
    cache_context: dict[str, str] | None = None,
    turns: list[dict] | None = None,
    conversation: list[dict] | None = None,
    persist: bool = True,
    progress: Callable[[str, float], None] | None = None,
) -> BuildResult:
    started = time.perf_counter()

    def report(label: str, fraction: float) -> None:
        # The UI callback only records stage labels/fractions; rendering stays in
        # the Streamlit main thread.
        if progress is not None:
            progress(label, fraction)

    # Wrap a raw client once so optimizer/reranker/generator share one budget.
    llm_client = ensure_budgeted(llm_client)
    # The chat model affects reranking, so it is part of the cache identity.
    cache_context = dict(cache_context or cache_context_from_manifest(manifest))
    cache_context["chat_model"] = str(getattr(llm_client, "model", "none") or "none")
    # The effective rerank model (stage override or client default) changes which
    # evidence a cache entry would contain, so it keys the cache separately.
    cache_context["rerank_model"] = (
        config.stage_model("rerank") or cache_context["chat_model"]
    )
    cache_context["chat_enabled"] = "yes" if llm_client is not None else "no"
    # Follow-ups can pass previously retrieved evidence to avoid a second pass.
    if evidence is None or candidates is None:
        report("Retrieving and reranking evidence", 0.25)
        (
            evidence,
            candidates,
            rerank_calls,
            rerank_fallback,
            rerank_cache_hit,
        ) = retrieve_and_score(
            collection=collection,
            research_query=research_query,
            constraints=constraints,
            faculty_by_id=faculty_by_id,
            llm_client=llm_client,
            use_cache=use_cache,
            cache_context=cache_context,
        )
    else:
        # Even reused evidence is filtered again because constraints may have
        # changed since the previous turn.
        candidates = filter_candidates(candidates, faculty_by_id, constraints)
    if not candidates:
        raise RuntimeError(
            "No faculty candidates matched the retrieval and explicit constraints."
        )
    report("Generating the team proposal", 0.6)
    proposal = generate_team_proposal(
        run_id=run_id,
        opportunity=opportunity,
        focus=focus,
        candidates=candidates,
        evidence=evidence,
        faculty_by_id=faculty_by_id,
        team_size=constraints.team_size,
        llm_client=llm_client,
        research_query=research_query,
        constraints=constraints,
    )
    report("Validating evidence and constraints", 0.9)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    errors: list[str] = []
    # Surface validation failures in run metadata and the UI rather than hiding
    # them behind the fallback team.
    if proposal.validation_issues:
        errors.append(
            "proposal validation issues: "
            + "; ".join(proposal.validation_issues[:5])
        )
    payload, metadata = build_run_payload(
        run_id=run_id,
        opportunity=opportunity,
        focus=focus,
        research_query=research_query,
        constraints=constraints,
        manifest=manifest,
        proposal=proposal,
        evidence=evidence,
        candidates=candidates,
        faculty_by_id=faculty_by_id,
        elapsed_ms=elapsed_ms,
        llm_client=llm_client,
        rerank_calls=rerank_calls,
        rerank_fallback=rerank_fallback,
        rerank_cache_hit=rerank_cache_hit,
        use_cache=use_cache,
        turns=turns,
        conversation=conversation,
        errors=[redact_secrets(item)[:300] for item in errors],
    )
    if persist:
        # Atomic write prevents a partially rendered run file after a crash.
        report("Saving run metadata", 0.98)
        config.ensure_directories()
        write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
    return BuildResult(
        payload=payload,
        evidence=evidence,
        candidates=candidates,
        research_query=research_query,
        proposal=proposal,
        metadata=metadata,
    )
