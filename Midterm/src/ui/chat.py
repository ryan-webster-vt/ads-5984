"""Streamlit team-builder, evidence inspector, and conversational refinement."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import streamlit as st

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
from src.prompting import prompt_versions
from src.retrieval.optimize import optimize_query
from src.retrieval.retrieve import retrieve_for_research_query
from src.team.constraints import filter_candidates, state_summary
from src.team.followup import apply_followup, augment_research_query
from src.team.generate import generate_team_proposal
from src.team.scoring import ScoringWeights, score_faculty_candidates


def _candidate_by_id(payload: dict) -> dict[str, dict]:
    return {
        candidate["scopus_id"]: candidate for candidate in payload.get("candidates", [])
    }


def _run_payload(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    query: str,
    team_size: int,
    k_in: int,
    k_out: int,
    manifest: IndexManifest,
    proposal: TeamProposal,
    evidence: list[RetrievedEvidence],
    candidates: list[FacultyCandidate],
    faculty_by_id: dict[str, Faculty],
    elapsed_ms: int,
    llm_client,
    rerank_calls: int,
    rerank_fallback: bool,
    turns: list[dict],
    conversation: list[dict],
) -> dict:
    weights = ScoringWeights.from_override(constraints.weights_override)
    metadata = RunMetadata(
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        opportunity_id=opportunity.opportunity_id,
        index_version=manifest.index_version,
        corpus_hash=manifest.corpus_sha256,
        embedding_model=manifest.embedding_model,
        chat_model=getattr(llm_client, "model", "unavailable"),
        query=query,
        search_queries=research_query.search_queries,
        research_query=research_query.model_dump(mode="json"),
        k_in=k_in,
        k_out=k_out,
        team_size=team_size,
        prompt_versions=prompt_versions(*config.PROMPT_NAMES),
        llm_calls=getattr(llm_client, "calls", 0),
        rerank_calls=rerank_calls,
        generator_calls=1 if proposal.used_llm else 0,
        verifier_calls=proposal.verifier_calls,
        repair_attempts=proposal.repair_attempts,
        latency_ms=elapsed_ms,
        validation_issues=proposal.validation_issues,
        fallback_used=not proposal.used_llm,
        weights=weights.as_dict(),
        constraints=constraints.model_dump(mode="json"),
        ranked_evidence_ids=[item.evidence_id for item in evidence],
        baseline_team=proposal.baseline_team_ids,
        final_team=[member.scopus_id for member in proposal.members],
        coverage=proposal.coverage,
    )
    candidate_payloads: list[dict] = []
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
        candidate_payloads.append(item)
    return {
        "metadata": metadata.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "candidates": candidate_payloads,
        "research_query": research_query.model_dump(mode="json"),
        "constraints": constraints.model_dump(mode="json"),
        "focus": focus,
        "rerank": {"calls": rerank_calls, "fallback_used": rerank_fallback},
        "turns": turns,
        "conversation": conversation,
    }


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _retrieve_and_score(
    *,
    collection,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    llm_client,
) -> tuple[list[RetrievedEvidence], list[FacultyCandidate], int, bool]:
    evidence, outcome = retrieve_for_research_query(
        collection,
        research_query,
        llm_client=llm_client,
        k_in=config.DEFAULT_K_IN,
        k_out=config.DEFAULT_K_OUT,
    )
    weights = ScoringWeights.from_override(constraints.weights_override)
    candidates = score_faculty_candidates(
        evidence,
        faculty_by_id,
        research_query=research_query,
        weights=weights,
        recency_preference=constraints.recency_preference,
    )
    candidates = filter_candidates(candidates, faculty_by_id, constraints)
    return evidence, candidates, outcome.calls, outcome.fallback_used


def _build(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    research_query: ResearchQuery,
    constraints: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    collection,
    manifest: IndexManifest,
    llm_client,
    evidence: list[RetrievedEvidence] | None = None,
    candidates: list[FacultyCandidate] | None = None,
    rerank_calls: int = 0,
    rerank_fallback: bool = False,
    turns: list[dict] | None = None,
    conversation: list[dict] | None = None,
) -> tuple[dict, list[RetrievedEvidence], list[FacultyCandidate], ResearchQuery]:
    started = time.perf_counter()
    if evidence is None or candidates is None:
        evidence, candidates, rerank_calls, rerank_fallback = _retrieve_and_score(
            collection=collection,
            research_query=research_query,
            constraints=constraints,
            faculty_by_id=faculty_by_id,
            llm_client=llm_client,
        )
    else:
        candidates = filter_candidates(candidates, faculty_by_id, constraints)
    if not candidates:
        raise RuntimeError(
            "No faculty candidates matched the retrieval and explicit constraints."
        )
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
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    payload = _run_payload(
        run_id=run_id,
        opportunity=opportunity,
        focus=focus,
        research_query=research_query,
        constraints=constraints,
        query=research_query.problem_statement,
        team_size=constraints.team_size,
        k_in=config.DEFAULT_K_IN,
        k_out=config.DEFAULT_K_OUT,
        manifest=manifest,
        proposal=proposal,
        evidence=evidence,
        candidates=candidates,
        faculty_by_id=faculty_by_id,
        elapsed_ms=elapsed_ms,
        llm_client=llm_client,
        rerank_calls=rerank_calls,
        rerank_fallback=rerank_fallback,
        turns=turns or [],
        conversation=conversation or [],
    )
    config.ensure_directories()
    write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
    return payload, evidence, candidates, research_query


def _render_evidence(
    member: dict,
    evidence_by_id: dict[str, dict],
    candidate: dict | None,
) -> None:
    with st.expander("Roster facts and publication evidence", expanded=False):
        if candidate:
            st.markdown("**Roster facts (authoritative)**")
            roster = candidate.get("roster", {})
            st.caption(
                f"Department: {roster.get('department') or member['department']} | "
                f"Rank: {roster.get('rank') or 'unknown'} | "
                f"Tenure: {roster.get('tenure') or 'unknown'} | "
                f"h-index: {roster.get('h_index') if roster.get('h_index') is not None else 'unknown'} | "
                f"H5: {roster.get('h5_index') if roster.get('h5_index') is not None else 'unknown'} | "
                f"citations: {roster.get('citation_count') if roster.get('citation_count') is not None else 'unknown'} | "
                "Top Journal Quartile % is author-level | Inferred suitability: yes"
            )
        else:
            st.caption("Roster facts are available in the run metadata.")
        st.markdown("**Publication evidence (untrusted excerpts)**")
        if not member.get("evidence_ids"):
            st.caption("No evidence IDs were returned for this member.")
            return
        for evidence_id in member["evidence_ids"]:
            item = evidence_by_id.get(evidence_id)
            if not item:
                st.warning(f"Unresolved evidence ID: {evidence_id}")
                continue
            metadata = item.get("metadata", {})
            st.markdown(
                f"**{metadata.get('title') or 'Untitled'}** "
                f"({metadata.get('year') or 'unknown'})"
            )
            st.caption(
                f"Evidence ID: `{evidence_id}` | chunk: "
                f"{metadata.get('chunk_type') or 'publication'} | "
                f"EID: `{metadata.get('eid') or 'n/a'}` | "
                f"DOI: `{metadata.get('doi') or 'n/a'}` | "
                f"matched concepts: {metadata.get('matched_concepts') or []} | "
                f"rerank: {metadata.get('rerank_score')}"
            )
            if metadata.get("provenance_url"):
                st.caption(f"Provenance: {metadata['provenance_url']}")
            st.text(item.get("text", "")[:1600])
            st.divider()


def _render_run(payload: dict) -> None:
    proposal = payload["proposal"]
    metadata = payload["metadata"]
    candidates_by_id = _candidate_by_id(payload)
    evidence_by_id = {
        item["evidence_id"]: item for item in payload.get("evidence", [])
    }
    st.subheader("Proposed team")
    st.caption(
        f"Run `{metadata['run_id']}` | validator: **{proposal['validator_status']}** | "
        f"LLM used: **{proposal['used_llm']}** | "
        f"repair attempts: {proposal.get('repair_attempts', 0)} | "
        f"latency: {metadata['latency_ms'] / 1000:.1f}s"
    )
    if proposal.get("validation_issues"):
        st.warning(
            "Validation issues were detected; the deterministic baseline is shown "
            "instead of the rejected LLM proposal."
        )
        with st.expander("Validation details"):
            for issue in proposal["validation_issues"]:
                st.write(f"- {issue}")

    for member in proposal["members"]:
        is_pi = member["scopus_id"] == proposal["proposed_pi_id"]
        title = f"{member['name']} — {member['role']}"
        if is_pi:
            title = f"{title} (Proposed PI)"
        with st.container(border=True):
            st.markdown(f"### {title}")
            st.caption(
                f"{member['department']} | Scopus ID: `{member['scopus_id']}` | "
                f"score: {member['score']:.3f} | inferred suitability"
            )
            st.write(member["rationale"])
            _render_evidence(
                member,
                evidence_by_id,
                candidates_by_id.get(member["scopus_id"]),
            )

    with st.expander("Coverage, rationale, and limitations", expanded=False):
        if proposal.get("coverage"):
            for concept, member_ids in proposal["coverage"].items():
                st.write(f"- **{concept}**: {', '.join(member_ids) or 'no member'}")
        st.write(proposal.get("team_rationale") or "No team rationale returned.")
        for limitation in proposal.get("limitations", []):
            st.write(f"- {limitation}")

    with st.expander("Deterministic candidate ranking", expanded=False):
        rows = [
            {
                "Scopus ID": candidate["scopus_id"],
                "Name": candidate["author_name"],
                "Department": candidate["department"],
                "Score": round(candidate["score"], 3),
                "Relevance": round(candidate["relevance"], 3),
                "Coverage": (
                    None
                    if candidate.get("coverage") is None
                    else round(candidate["coverage"], 3)
                ),
                "Recency": round(candidate["recency"], 3),
                "Metrics": (
                    None
                    if candidate["metrics"] is None
                    else round(candidate["metrics"], 3)
                ),
                "Collaboration": round(candidate.get("collaboration", 0.0), 3),
                "Diversity": round(candidate.get("diversity", 0.0), 3),
                "Evidence count": len(candidate["evidence_ids"]),
            }
            for candidate in payload.get("candidates", [])
        ]
        st.dataframe(rows, width="stretch", hide_index=True)

    with st.expander("Optimized research query", expanded=False):
        st.json(payload.get("research_query", {}))

    with st.expander("Run metadata and prompts", expanded=False):
        st.json(metadata)


def _render_conversation(conversation: list[dict]) -> None:
    for message in conversation:
        with st.chat_message(message["role"]):
            st.write(message["content"])


def _run_followup(
    *,
    state: dict,
    message: str,
    opportunity: Opportunity,
    faculty_by_id: dict[str, Faculty],
    collection,
    manifest: IndexManifest,
    llm_client,
) -> None:
    state["conversation"].append({"role": "user", "content": message})
    if len(state["turns"]) >= config.MAX_CONVERSATION_TURNS:
        reply = (
            f"Conversation limit reached ({config.MAX_CONVERSATION_TURNS} turns). "
            "Clear the run to start over."
        )
        state["conversation"].append({"role": "assistant", "content": reply})
        st.rerun()

    result = apply_followup(
        message,
        state["constraints"],
        faculty_by_id,
        llm_client=llm_client,
    )
    if result.clarification_needed or not result.applied:
        reply = result.assistant_message or "Please clarify the requested change."
        state["conversation"].append({"role": "assistant", "content": reply})
        state["turns"].append(
            {
                "turn_index": len(state["turns"]) + 1,
                "user_message": message,
                "assistant_message": reply,
                "clarification_needed": reply,
                "constraint_state": state["constraints"].model_dump(mode="json"),
            }
        )
        st.rerun()

    state["constraints"] = result.state
    research_query = augment_research_query(
        state["research_query"],
        result.state,
    )
    known_ids = {candidate.scopus_id for candidate in state["candidates"]}
    missing_required = [
        faculty_id
        for faculty_id in result.state.required_members
        if faculty_id not in known_ids
    ]
    redo = result.redo_retrieval or bool(missing_required)
    evidence = None if redo else state["evidence"]
    candidates = None if redo else state["candidates"]
    rerank_calls = 0 if redo else state.get("rerank_calls", 0)
    rerank_fallback = False if redo else state.get("rerank_fallback", False)

    turn_index = len(state["turns"]) + 1
    run_id = f"{state['run_id']}-t{turn_index}"
    try:
        payload, evidence, candidates, research_query = _build(
            run_id=run_id,
            opportunity=opportunity,
            focus=state["focus"],
            research_query=research_query,
            constraints=result.state,
            faculty_by_id=faculty_by_id,
            collection=collection,
            manifest=manifest,
            llm_client=llm_client,
            evidence=evidence,
            candidates=candidates,
            rerank_calls=rerank_calls,
            rerank_fallback=rerank_fallback,
            turns=state["turns"],
            conversation=state["conversation"],
        )
    except Exception as exc:
        reply = f"I could not rebuild the team: {exc}"
        state["conversation"].append({"role": "assistant", "content": reply})
        state["turns"].append(
            {
                "turn_index": turn_index,
                "user_message": message,
                "assistant_message": reply,
                "constraint_state": result.state.model_dump(mode="json"),
            }
        )
        st.rerun()

    turns = payload["turns"] + [
        {
            "turn_index": turn_index,
            "user_message": message,
            "assistant_message": (
                "Revised team "
                + ", ".join(
                    member["name"] for member in payload["proposal"]["members"]
                )
            ),
            "run_id": run_id,
            "constraint_state": result.state.model_dump(mode="json"),
        }
    ]
    conversation = payload["conversation"] + [
        {
            "role": "assistant",
            "content": f"Revised the team using: {state_summary(result.state)}",
        }
    ]
    payload["turns"] = turns
    payload["conversation"] = conversation
    write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
    state.update(
        {
            "payload": payload,
            "research_query": research_query,
            "evidence": evidence,
            "candidates": candidates,
            "rerank_calls": payload["metadata"]["rerank_calls"],
            "rerank_fallback": payload["rerank"]["fallback_used"],
            "turns": turns,
            "conversation": conversation,
        }
    )
    st.rerun()


def _render_followup(
    *,
    state: dict,
    opportunity: Opportunity,
    faculty_by_id: dict[str, Faculty],
    collection,
    manifest: IndexManifest,
    llm_client,
) -> None:
    st.divider()
    st.subheader("Refine the team")
    st.caption(
        "Examples: “replace Dr. X with a chemist”, “add climate-change expertise "
        "and keep the team at four”, “shrink to three”, “only tenure-track faculty”."
    )
    _render_conversation(state["conversation"])
    if len(state["turns"]) >= config.MAX_CONVERSATION_TURNS:
        st.info("Conversation turn limit reached. Clear the run to start over.")
        return
    message = st.chat_input("Ask for a constraint change or refinement")
    if message:
        with st.spinner("Applying the constraint change..."):
            _run_followup(
                state=state,
                message=message,
                opportunity=opportunity,
                faculty_by_id=faculty_by_id,
                collection=collection,
                manifest=manifest,
                llm_client=llm_client,
            )


def render_team_builder(
    *,
    opportunity: Opportunity,
    faculty_by_id: dict[str, Faculty],
    collection,
    manifest: IndexManifest,
    llm_client,
) -> None:
    st.header("Build a team")
    st.caption(
        "Phase 4: optimized retrieval, batched reranking, deterministic scoring, "
        "grounded generation, validation/repair, and conversational constraints."
    )
    with st.container(border=True):
        st.subheader(opportunity.title)
        st.write(
            f"**Agency:** {opportunity.agency or 'Unknown'} | "
            f"**Status:** {opportunity.status} | "
            f"**Number:** {opportunity.opportunity_number or 'Unknown'}"
        )
        if opportunity.open_date or opportunity.close_date:
            st.caption(
                f"Open: {opportunity.open_date or 'unknown'} | "
                f"Close: {opportunity.close_date or 'unknown'}"
            )
        if opportunity.award_ceiling or opportunity.award_floor:
            st.caption(
                f"Award ceiling: {opportunity.award_ceiling or 'unknown'} | "
                f"Floor: {opportunity.award_floor or 'unknown'}"
            )
        with st.expander("Opportunity description", expanded=False):
            st.write(opportunity.description or "No description available.")
        if opportunity.synopsis_url:
            st.caption(f"Official synopsis: {opportunity.synopsis_url}")

    focus = st.text_area(
        "Optional focus for this team",
        placeholder=(
            "Example: prioritize ecology, evolutionary biology, and long-term "
            "data synthesis."
        ),
        height=90,
    )
    team_size = st.slider(
        "Team size",
        min_value=config.MIN_TEAM_SIZE,
        max_value=config.MAX_TEAM_SIZE,
        value=config.DEFAULT_TEAM_SIZE,
    )

    state = st.session_state.get("team_state")
    if state and state.get("opportunity_id") != opportunity.opportunity_id:
        st.session_state.pop("team_state", None)
        state = None
    if state is None:
        st.session_state.pop("team_run", None)

    if st.button("Build Team", type="primary"):
        run_id = _new_run_id()
        constraints = UserConstraints(team_size=team_size)
        with st.spinner("Optimizing the query, retrieving evidence, and building a team..."):
            try:
                research_query = optimize_query(
                    opportunity,
                    focus,
                    llm_client=llm_client,
                )
                payload, evidence, candidates, research_query = _build(
                    run_id=run_id,
                    opportunity=opportunity,
                    focus=focus,
                    research_query=research_query,
                    constraints=constraints,
                    faculty_by_id=faculty_by_id,
                    collection=collection,
                    manifest=manifest,
                    llm_client=llm_client,
                )
            except Exception as exc:
                st.error(f"Team build failed: {type(exc).__name__}: {exc}")
                return
        conversation = [
            {
                "role": "assistant",
                "content": (
                    "Built an initial team. Ask for a size, member, expertise, "
                    "department, PI, or tenure-track change."
                ),
            }
        ]
        payload["conversation"] = conversation
        write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
        st.session_state["team_run"] = payload
        st.session_state["team_state"] = {
            "opportunity_id": opportunity.opportunity_id,
            "run_id": run_id,
            "payload": payload,
            "constraints": constraints,
            "research_query": research_query,
            "evidence": evidence,
            "candidates": candidates,
            "focus": focus,
            "turns": [],
            "conversation": conversation,
            "rerank_calls": payload["metadata"]["rerank_calls"],
            "rerank_fallback": payload["rerank"]["fallback_used"],
        }
        st.rerun()

    state = st.session_state.get("team_state")
    if state:
        _render_run(state["payload"])
        _render_followup(
            state=state,
            opportunity=opportunity,
            faculty_by_id=faculty_by_id,
            collection=collection,
            manifest=manifest,
            llm_client=llm_client,
        )
        if st.button("Clear current run"):
            st.session_state.pop("team_state", None)
            st.session_state.pop("team_run", None)
            st.rerun()
    elif "team_run" in st.session_state:
        _render_run(st.session_state["team_run"])
        if st.button("Clear current run"):
            st.session_state.pop("team_run", None)
            st.rerun()
    else:
        st.info("Select Build Team to retrieve evidence and generate a proposal.")
