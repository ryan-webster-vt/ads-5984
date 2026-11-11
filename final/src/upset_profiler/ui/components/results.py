"""Ranked upset-alert rendering for the Streamlit front end."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from upset_profiler.models.games import DailyGame
from upset_profiler.models.scoring import UpsetRanking, UpsetScore
from upset_profiler.ui.components.slate_table import matchup_label


def _render_citations(score: UpsetScore) -> None:
    for citation in score.citations:
        title = citation.title or citation.source_domain
        st.markdown(f"- [{title}]({citation.url}) — {citation.source_domain}")
        if citation.quote:
            st.caption(f"“{citation.quote}”")


def _render_score(score: UpsetScore, game: DailyGame | None) -> None:
    matchup = matchup_label(game) if game is not None else score.game_id
    with st.container(border=True):
        rank_col, score_col = st.columns([4, 1])
        with rank_col:
            st.subheader(f"#{score.rank} {matchup}")
            st.caption(f"Game {score.game_id} · model {score.model_name}")
        with score_col:
            st.metric("Upset risk", f"{score.score:.3f}")
        st.write(score.rationale)
        _render_citations(score)


def render_ranking(
    ranking: UpsetRanking,
    games_by_id: Mapping[str, DailyGame] | None = None,
) -> None:
    """Render ranked alerts, or a clear empty-state message."""
    if not ranking.scores:
        st.info(
            "No qualifying upset alerts were found for the selected games. "
            "No validated precondition matched the available pregame evidence."
        )
        return
    st.success(f"{len(ranking.scores)} game(s) are on upset watch.")
    for score in sorted(ranking.scores, key=lambda item: item.rank):
        _render_score(score, None if games_by_id is None else games_by_id.get(score.game_id))
