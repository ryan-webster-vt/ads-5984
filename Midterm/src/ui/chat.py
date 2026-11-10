"""Streamlit team-builder, evidence inspector, and conversational refinement."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import streamlit as st

import config
from models import (
    Faculty,
    IndexManifest,
    Opportunity,
    UserConstraints,
)
from src.io_utils import write_json_atomic
from src.llm import ensure_budgeted
from src.redaction import redact_secrets
from src.retrieval.optimize import optimize_query
from src.team.constraints import state_summary
from src.team.followup import apply_followup, augment_research_query
from src.team.pipeline import build_team_run, new_run_id

TEAM_BUILD_EXPECTED_SECONDS = 300.0
# Follow-ups reuse cached evidence, so they are expected to finish faster than a
# full build; a fresh retrieval pass still completes well within this bound.
FOLLOWUP_EXPECTED_SECONDS = 120.0
# Safety margin over the LLM wall budget; a stale build flag older than this is
# cleared so a crashed script cannot disable the Build Team button forever.
BUILD_FLAG_STALE_SECONDS = config.LLM_MAX_WALL_SECONDS + 300.0


def _build_running() -> bool:
    """True while a build is active; self-heals a stale flag after a crash."""
    started_at = st.session_state.get("build_running_since")
    if started_at is not None:
        if time.monotonic() - started_at < BUILD_FLAG_STALE_SECONDS:
            return True
        # The build script died without clearing the flag; reset it.
        st.session_state.pop("build_running_since", None)
    return False


def _mark_build_running(running: bool) -> None:
    if running:
        st.session_state["build_running_since"] = time.monotonic()
    else:
        st.session_state.pop("build_running_since", None)


def _format_elapsed(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _run_with_progress(
    task: Callable[[Callable[[str, float], None]], Any],
    *,
    expected_seconds: float = TEAM_BUILD_EXPECTED_SECONDS,
    done_label: str = "Team ready",
    failed_label: str = "Team generation failed",
) -> Any:
    # Run the blocking pipeline in a worker while the main Streamlit thread keeps
    # rendering the progress bar and timer. The worker only updates plain state;
    # all Streamlit calls stay on the main thread.
    state = {"label": "Starting team generation", "fraction": 0.0}

    def report(label: str, fraction: float) -> None:
        state["label"] = label
        state["fraction"] = max(0.0, min(0.98, float(fraction)))

    bar = st.progress(0.0, text=str(state["label"]))
    timer = st.empty()
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(task, report)
            while not future.done():
                elapsed = time.monotonic() - started
                timed = (
                    min(0.95, elapsed / expected_seconds)
                    if expected_seconds > 0
                    else 0.0
                )
                value = max(float(state["fraction"]), timed)
                bar.progress(min(value, 0.98), text=str(state["label"]))
                timer.markdown(f"Elapsed: **{_format_elapsed(elapsed)}**")
                time.sleep(0.25)
            result = future.result()
    except Exception:
        elapsed = time.monotonic() - started
        bar.progress(1.0, text=failed_label)
        timer.markdown(f"Elapsed: **{_format_elapsed(elapsed)}**")
        raise
    elapsed = time.monotonic() - started
    bar.progress(1.0, text=done_label)
    timer.markdown(f"Elapsed: **{_format_elapsed(elapsed)}**")
    return result


def _candidate_by_id(payload: dict) -> dict[str, dict]:
    # The payload is plain JSON (persisted to disk), not Pydantic models.
    return {
        candidate["scopus_id"]: candidate for candidate in payload.get("candidates", [])
    }


def _render_evidence(
    member: dict,
    evidence_by_id: dict[str, dict],
    candidate: dict | None,
) -> None:
    # Keep roster facts, publication excerpts, and inference labels visually
    # distinct so users can see what is source data versus interpretation.
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
        # Retrieved text is untrusted evidence and is rendered as text, never HTML.
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
    # This renderer reads only the persisted run payload, making the UI and saved
    # metadata consistent.
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
        f"LLM calls: {metadata.get('llm_calls', 0)} | "
        f"cached evidence: {metadata.get('cache_hits', 0)} | "
        f"repair attempts: {proposal.get('repair_attempts', 0)} | "
        f"latency: {metadata['latency_ms'] / 1000:.1f}s"
    )
    # Fallback is reported explicitly instead of presenting it as a normal model
    # result.
    if proposal.get("validation_issues"):
        st.warning(
            "Validation issues were detected; the deterministic baseline is shown "
            "instead of the rejected LLM proposal."
        )
        with st.expander("Validation details"):
            for issue in proposal["validation_issues"]:
                st.write(f"- {issue}")

    # Render one card per member with role, evidence, and deterministic score.
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

    # The full candidate table exposes the deterministic ranking the LLM saw.
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


# Follow-up turns patch explicit state, reuse evidence when possible, then
# regenerate and revalidate the team.
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
    # Cap conversation length so follow-ups cannot grow state/budget unbounded.
    if len(state["turns"]) >= config.MAX_CONVERSATION_TURNS:
        reply = (
            f"Conversation limit reached ({config.MAX_CONVERSATION_TURNS} turns). "
            "Clear the run to start over."
        )
        state["conversation"].append({"role": "assistant", "content": reply})
        st.rerun()

    # Each turn gets its own shared budget for parsing + regeneration.
    budgeted_client = ensure_budgeted(llm_client)

    def _apply_and_rebuild(report: Callable[[str, float], None]):
        report("Parsing the requested change", 0.05)
        result = apply_followup(
            message,
            state["constraints"],
            faculty_by_id,
            llm_client=budgeted_client,
        )
        if result.clarification_needed or not result.applied:
            return result, None, None
        report("Rebuilding the team", 0.3)
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
        # Reuse evidence unless new expertise or an unseen required member demands a
        # fresh retrieval pass.
        redo = result.redo_retrieval or bool(missing_required)
        evidence = None if redo else state["evidence"]
        candidates = None if redo else state["candidates"]
        rerank_calls = 0 if redo else state.get("rerank_calls", 0)
        rerank_fallback = False if redo else state.get("rerank_fallback", False)
        rerank_cache_hit = False if redo else state.get("rerank_cache_hit", False)

        turn_index = len(state["turns"]) + 1
        run_id = f"{state['run_id']}-t{turn_index}"
        # The pipeline persists the run and returns evidence/candidates/query for
        # the next turn.
        build = build_team_run(
            run_id=run_id,
            opportunity=opportunity,
            focus=state["focus"],
            research_query=research_query,
            constraints=result.state,
            faculty_by_id=faculty_by_id,
            collection=collection,
            manifest=manifest,
            llm_client=budgeted_client,
            evidence=evidence,
            candidates=candidates,
            rerank_calls=rerank_calls,
            rerank_fallback=rerank_fallback,
            rerank_cache_hit=rerank_cache_hit,
            turns=state["turns"],
            conversation=state["conversation"],
            progress=report,
        )
        return result, build, run_id

    try:
        result, build, run_id = _run_with_progress(
            _apply_and_rebuild,
            expected_seconds=FOLLOWUP_EXPECTED_SECONDS,
            done_label="Revised team ready",
            failed_label="Team revision failed",
        )
    except Exception as exc:
        # Store a redacted, bounded reply so an API failure cannot leak a key.
        reply = f"I could not rebuild the team: {redact_secrets(exc)}"
        state["conversation"].append({"role": "assistant", "content": reply})
        state["turns"].append(
            {
                "turn_index": len(state["turns"]) + 1,
                "user_message": message,
                "assistant_message": reply,
                "constraint_state": state["constraints"].model_dump(mode="json"),
            }
        )
        st.rerun()
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

    payload = build.payload
    evidence = build.evidence
    candidates = build.candidates
    research_query = build.research_query
    turn_index = len(state["turns"]) + 1

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
    # Persist the full turn snapshot atomically before updating session state.
    write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
    state.update(
        {
            "payload": payload,
            "research_query": research_query,
            "evidence": evidence,
            "candidates": candidates,
            "rerank_calls": payload["metadata"]["rerank_calls"],
            "rerank_fallback": payload["rerank"]["fallback_used"],
            "rerank_cache_hit": payload["rerank"].get("cache_hit", False),
            "turns": turns,
            "conversation": conversation,
        }
    )
    st.rerun()


# Renders conversation history and the chat input for the current team run.
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
    # Chat input is disabled implicitly once the turn cap is reached above.
    followup_running = st.session_state.get("followup_running", False)
    message = st.chat_input(
        "Ask for a constraint change or refinement",
        disabled=followup_running,
    )
    if message and not followup_running:
        st.session_state["followup_running"] = True
        try:
            _run_followup(
                state=state,
                message=message,
                opportunity=opportunity,
                faculty_by_id=faculty_by_id,
                collection=collection,
                manifest=manifest,
                llm_client=llm_client,
            )
        finally:
            # _run_followup reruns on success/failure; the finally also covers a
            # crash before its own rerun so the input never stays disabled.
            st.session_state.pop("followup_running", None)


# Entry point called by app.py once an opportunity is selected.
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
        "Phase 6: bounded retrieval/generation budgets, cached or uncached "
        "evidence, grounded validation, and conversational constraints."
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

    # Focus text is optional context for the optimizer; it does not override the
    # selected opportunity or evidence constraints.
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

    # A run is tied to one opportunity; switching opportunities clears old state.
    state = st.session_state.get("team_state")
    if state and state.get("opportunity_id") != opportunity.opportunity_id:
        st.session_state.pop("team_state", None)
        _mark_build_running(False)
        state = None
    if state is None:
        st.session_state.pop("team_run", None)

    # The button stays disabled while the build runs so the user cannot start a
    # second concurrent build against the same budget and session state.
    build_running = _build_running()
    if st.button(
        "Build Team",
        type="primary",
        disabled=build_running,
        help=(
            "A team build is in progress."
            if build_running
            else "Retrieve evidence and generate a team proposal."
        ),
    ):
        _mark_build_running(True)
        # Every explicit build gets a new run ID and fresh budget.
        run_id = new_run_id()
        constraints = UserConstraints(team_size=team_size)

        def _generate(report: Callable[[str, float], None]):
            budgeted_client = ensure_budgeted(llm_client)
            report("Optimizing the query", 0.05)
            research_query = optimize_query(
                opportunity,
                focus,
                llm_client=budgeted_client,
            )
            report("Retrieving and reranking evidence", 0.25)
            build = build_team_run(
                run_id=run_id,
                opportunity=opportunity,
                focus=focus,
                research_query=research_query,
                constraints=constraints,
                faculty_by_id=faculty_by_id,
                collection=collection,
                manifest=manifest,
                llm_client=budgeted_client,
                progress=report,
            )
            return build.research_query, build

        try:
            research_query, build = _run_with_progress(_generate)
        except Exception as exc:
            _mark_build_running(False)
            st.error(
                f"Team build failed: {type(exc).__name__}: {redact_secrets(exc)}"
            )
            return
        payload = build.payload
        evidence = build.evidence
        candidates = build.candidates
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
        # Persist before rendering so a crash does not lose a completed build.
        write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
        _mark_build_running(False)
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
            "rerank_cache_hit": payload["rerank"].get("cache_hit", False),
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
            _mark_build_running(False)
            st.rerun()
    elif "team_run" in st.session_state:
        _render_run(st.session_state["team_run"])
        if st.button("Clear current run"):
            st.session_state.pop("team_run", None)
            _mark_build_running(False)
            st.rerun()
    else:
        st.info("Select Build Team to retrieve evidence and generate a proposal.")
