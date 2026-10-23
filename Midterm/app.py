"""Streamlit entry point: browse live opportunities, then build a cited team."""

from __future__ import annotations

import streamlit as st

import config
from models import OpportunityDatabase
from src.grants.client import load_opportunity_fixture
from src.grants.refresh import refresh_open_opportunities
from src.grants.store import is_visible_open, load_database
from src.llm import GenerationClient
from src.research.identity import build_eligible_faculty_index
from src.research.index import (
    current_corpus_hashes,
    get_collection,
    index_compatibility_issues,
    load_manifest,
)
from src.research.loaders import load_faculty
from src.ui.chat import render_team_builder
from src.ui.opportunities import render_opportunity_browser

st.set_page_config(
    page_title="VT CoS Grant Team Builder",
    layout="wide",
)


@st.cache_resource
def _collection():
    return get_collection()


@st.cache_resource
def _llm_client():
    try:
        return GenerationClient()
    except Exception:
        return None


def _load_opportunities() -> OpportunityDatabase:
    if config.OPPORTUNITIES_PATH.exists():
        return load_database()
    fixture = load_opportunity_fixture()
    return OpportunityDatabase(opportunities=[fixture])


def _run_refresh() -> None:
    config.ensure_directories()
    progress_bar = st.progress(0.0)
    status_line = st.empty()

    def progress(stage: str, done: int, total: int) -> None:
        fraction = min(1.0, done / total) if total else 0.0
        progress_bar.progress(fraction)
        label = "Searching Grants.gov" if stage == "search" else "Fetching details"
        status_line.caption(f"{label}: {done}/{total}")

    result = refresh_open_opportunities(progress=progress)
    progress_bar.progress(1.0)
    if result.written:
        st.session_state["refresh_notice"] = (
            "success" if result.summary.status == "success" else "warning",
            f"Refresh {result.summary.status}: {result.summary.open_count} open, "
            f"{result.summary.archived_count} archived, "
            f"{result.summary.detail_failed} detail failures.",
        )
    else:
        message = result.summary.errors[0] if result.summary.errors else "unknown error"
        st.session_state["refresh_notice"] = (
            "error",
            f"Refresh failed; the previous snapshot was preserved. {message}",
        )
    st.rerun()


def main() -> None:
    st.title("VT College of Science Grant Team Builder")
    st.caption(
        "Phase 4: refresh and filter live Grants.gov opportunities, then build and "
        "conversationally refine a grounded team from the full faculty/publication "
        "corpus."
    )

    notice = st.session_state.pop("refresh_notice", None)
    if notice:
        getattr(st, notice[0])(notice[1])

    manifest = load_manifest()
    if manifest is None:
        st.error("The research index has not been built yet.")
        st.code("python3 scripts/build_index.py --rebuild", language="bash")
        st.stop()

    _, _, corpus_hash = current_corpus_hashes()
    compatibility_issues = index_compatibility_issues(
        manifest,
        expected_corpus_hash=corpus_hash,
    )
    if compatibility_issues:
        st.error("The research index is incompatible with the current configuration.")
        for issue in compatibility_issues:
            st.write(f"- {issue}")
        st.code("python3 scripts/build_index.py --rebuild", language="bash")
        st.stop()

    try:
        collection = _collection()
        collection_count = collection.count()
    except Exception as exc:
        st.error(
            "The research index could not be opened. Rebuild it and verify the "
            f"embedding service. ({type(exc).__name__})"
        )
        st.code("python3 scripts/build_index.py --rebuild", language="bash")
        st.stop()

    faculty = load_faculty()
    faculty_by_id = build_eligible_faculty_index(faculty)
    llm_client = _llm_client()
    database = _load_opportunities()
    records_by_id = {
        record.opportunity_id: record for record in database.opportunities
    }
    feed_open_count = sum(
        1
        for record in database.opportunities
        if record.status in config.OPPORTUNITY_OPEN_STATUSES
    )
    open_count = sum(1 for record in database.opportunities if is_visible_open(record))

    with st.sidebar:
        st.header("Opportunity data")
        st.write(f"Active open opportunities: `{open_count}`")
        st.caption(f"Grants.gov open feed records: `{feed_open_count}`")
        if database.last_refresh:
            st.caption(f"Last refresh: {database.last_refresh.completed_at}")
            st.caption(f"Status: `{database.last_refresh.status}`")
        else:
            st.caption("No live refresh yet; showing the saved fixture opportunity.")
        if st.button("Refresh opportunities", type="primary"):
            _run_refresh()
        st.divider()
        st.header("Index status")
        st.write(f"Index: `{manifest.index_version}`")
        st.write(f"Collection vectors: `{collection_count}`")
        st.write(f"Indexed publications: `{manifest.selected_publications}`")
        st.write(f"Eligible faculty: `{len(faculty_by_id)}`")
        if manifest.is_full_corpus:
            st.caption("The index covers the full attributed publication corpus.")
        else:
            st.warning(
                "This is a reduced-scope index. Rebuild without --limit for the "
                "full publication corpus."
            )
        if manifest.embedding_dimension:
            st.caption(
                f"Embedding model: `{manifest.embedding_model}` "
                f"(dimension {manifest.embedding_dimension})"
            )
        if llm_client is None:
            st.warning(
                "VT ARC generation is unavailable or not configured. The app will "
                "use the deterministic baseline team."
            )
        else:
            st.caption(f"Generation model: `{llm_client.model}`")

    selected_id = st.session_state.get("selected_opportunity_id")
    selected = records_by_id.get(selected_id) if selected_id else None
    if selected_id and selected is None:
        st.session_state.pop("selected_opportunity_id", None)
        selected = None

    if selected is not None:
        if st.button("← Back to opportunities"):
            st.session_state.pop("selected_opportunity_id", None)
            st.rerun()
        render_team_builder(
            opportunity=selected,
            faculty_by_id=faculty_by_id,
            collection=collection,
            manifest=manifest,
            llm_client=llm_client,
        )
    else:
        chosen = render_opportunity_browser(database)
        if chosen is not None:
            st.session_state["selected_opportunity_id"] = chosen.opportunity_id
            st.session_state.pop("team_run", None)
            st.session_state.pop("team_state", None)
            st.rerun()


if __name__ == "__main__":
    main()
