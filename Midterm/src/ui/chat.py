"""Streamlit team-builder view."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import streamlit as st

import config
from models import Faculty, IndexManifest, Opportunity, RunMetadata
from src.io_utils import write_json_atomic
from src.retrieval.retrieve import (
    aggregate_faculty_candidates,
    build_query,
    retrieve_evidence,
)
from src.team.generate import generate_team_proposal


def _run_payload(
    *,
    run_id: str,
    opportunity: Opportunity,
    focus: str,
    query: str,
    team_size: int,
    k_in: int,
    manifest: IndexManifest,
    proposal,
    evidence,
    candidates,
    elapsed_ms: int,
    llm_client,
) -> dict:
    metadata = RunMetadata(
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        opportunity_id=opportunity.opportunity_id,
        index_version=manifest.index_version,
        embedding_model=manifest.embedding_model,
        chat_model=getattr(llm_client, "model", "unavailable"),
        query=query,
        k_in=k_in,
        k_out=min(config.DEFAULT_K_OUT, len(candidates)),
        team_size=team_size,
        llm_calls=getattr(llm_client, "calls", 0),
        latency_ms=elapsed_ms,
        validation_issues=proposal.validation_issues,
        fallback_used=not proposal.used_llm,
        weights={"relevance": 0.60, "recency": 0.20, "metrics": 0.20},
    )
    return {
        "metadata": metadata.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        "focus": focus,
    }


def _render_evidence(
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict],
) -> None:
    with st.expander("Source evidence", expanded=False):
        if not evidence_ids:
            st.caption("No evidence IDs were returned for this member.")
            return
        for evidence_id in evidence_ids:
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
                f"Evidence ID: `{evidence_id}` | "
                f"EID: `{metadata.get('eid') or 'n/a'}` | "
                f"DOI: `{metadata.get('doi') or 'n/a'}` | "
                f"Source: {metadata.get('source_title') or 'unknown'}"
            )
            text = item.get("text", "")
            st.text(text[:1200] + ("..." if len(text) > 1200 else ""))
            st.divider()


def _render_run(payload: dict) -> None:
    proposal = payload["proposal"]
    metadata = payload["metadata"]
    evidence_by_id = {
        item["evidence_id"]: item for item in payload.get("evidence", [])
    }
    st.subheader("Proposed team")
    st.caption(
        f"Run `{metadata['run_id']}` | validator: **{proposal['validator_status']}** | "
        f"LLM used: **{proposal['used_llm']}** | "
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
            _render_evidence(member.get("evidence_ids", []), evidence_by_id)

    with st.expander("Team rationale and limitations", expanded=False):
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
                "Recency": round(candidate["recency"], 3),
                "Metrics": (
                    None
                    if candidate["metrics"] is None
                    else round(candidate["metrics"], 3)
                ),
                "Evidence count": len(candidate["evidence_ids"]),
            }
            for candidate in payload.get("candidates", [])
        ]
        st.dataframe(rows, width="stretch", hide_index=True)

    with st.expander("Run metadata", expanded=False):
        st.json(metadata)


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
        "Phase 3 uses the selected live Grants.gov opportunity and the full "
        "attributed publication corpus."
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
        placeholder="Example: prioritize ecology, evolutionary biology, and long-term data synthesis.",
        height=90,
    )
    team_size = st.slider("Team size", min_value=3, max_value=5, value=3)
    if st.button("Build Team", type="primary"):
        run_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        with st.spinner("Retrieving publication evidence and building a team..."):
            try:
                query = build_query(opportunity, focus)
                evidence = retrieve_evidence(
                    collection,
                    query,
                    k_in=config.DEFAULT_K_IN,
                )
                candidates = aggregate_faculty_candidates(
                    evidence,
                    faculty_by_id,
                )
                if not candidates:
                    st.error(
                        "No faculty candidates were retrieved. Rebuild the demo "
                        "index or broaden the opportunity focus."
                    )
                    return
                proposal = generate_team_proposal(
                    run_id=run_id,
                    opportunity=opportunity,
                    focus=focus,
                    candidates=candidates,
                    evidence=evidence,
                    faculty_by_id=faculty_by_id,
                    team_size=team_size,
                    llm_client=llm_client,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                payload = _run_payload(
                    run_id=run_id,
                    opportunity=opportunity,
                    focus=focus,
                    query=query,
                    team_size=team_size,
                    k_in=config.DEFAULT_K_IN,
                    manifest=manifest,
                    proposal=proposal,
                    evidence=evidence,
                    candidates=candidates,
                    elapsed_ms=elapsed_ms,
                    llm_client=llm_client,
                )
                config.ensure_directories()
                write_json_atomic(config.RUNS_DIR / f"{run_id}.json", payload)
                st.session_state["team_run"] = payload
            except Exception as exc:
                st.error(f"Team build failed: {type(exc).__name__}: {exc}")
                return

    if "team_run" in st.session_state:
        _render_run(st.session_state["team_run"])
        if st.button("Clear current run"):
            st.session_state.pop("team_run", None)
            st.rerun()
    else:
        st.info("Select Build Team to retrieve evidence and generate a proposal.")
