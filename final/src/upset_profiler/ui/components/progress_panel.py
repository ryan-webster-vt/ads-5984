"""Agent-activity and progress rendering for the Streamlit front end."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from upset_profiler.models.progress import ProgressEvent


def render_progress(events: Sequence[ProgressEvent]) -> None:
    """Render the progress bar and ordered agent-activity table."""
    if not events:
        st.caption("No analysis run yet. Choose games and select Execute Search.")
        return

    latest = events[-1]
    fraction = latest.fraction
    if fraction is not None:
        st.progress(fraction, text=f"{latest.stage.value}: {latest.message}")
    else:
        st.caption(f"{latest.stage.value}: {latest.message}")

    with st.expander(f"Agent activity ({len(events)} events)", expanded=False):
        st.dataframe(
            [
                {
                    "#": event.event_index,
                    "Stage": event.stage.value,
                    "Agent": event.agent,
                    "Status": event.status.value,
                    "Game": event.game_id or "",
                    "Message": event.message,
                    "Progress": (
                        ""
                        if event.total is None
                        else f"{event.completed}/{event.total}"
                    ),
                }
                for event in events
            ],
            hide_index=True,
            use_container_width=True,
        )
