"""Streamlit front end for the daily slate, monitoring, and upset rankings."""

from __future__ import annotations

from datetime import date

import streamlit as st

from upset_profiler.config import get_settings
from upset_profiler.context import RunContext
from upset_profiler.errors import UpsetProfilerError
from upset_profiler.models.games import DailyGame
from upset_profiler.models.progress import ProgressEvent
from upset_profiler.models.scoring import UpsetRanking
from upset_profiler.orchestration.progress import InMemoryProgressSink
from upset_profiler.services.cache import AnalysisOutcome
from upset_profiler.services.slate import SlateService
from upset_profiler.ui.components.progress_panel import render_progress
from upset_profiler.ui.components.results import render_ranking
from upset_profiler.ui.components.slate_table import (
    SORT_OPTIONS,
    STATUS_OPTIONS,
    conference_options,
    filter_games,
    selection_label,
    slate_rows,
    sort_games,
)
from upset_profiler.ui.state import (
    FIXTURE_SLATE_DATE,
    FIXTURE_SOURCE,
    SOURCE_OPTIONS,
    UiServices,
    build_ui_services,
)

_RANKING_KEY = "ui_last_ranking"
_EVENTS_KEY = "ui_last_events"
_CACHE_KEY = "ui_last_from_cache"
_ERROR_KEY = "ui_last_error"
_DATE_KEY = "ui_last_date"


def _default_date(source: str) -> date:
    if source == FIXTURE_SOURCE:
        return date.fromisoformat(FIXTURE_SLATE_DATE)
    return date.today()


def _run_analysis(
    services: UiServices,
    *,
    slate_date: date,
    selected_game_ids: list[str],
    worker_count: int,
    use_cache: bool,
    force_refresh: bool,
) -> tuple[AnalysisOutcome, list[ProgressEvent]]:
    if force_refresh:
        services.monitoring.invalidate(
            slate_date=slate_date,
            selected_game_ids=selected_game_ids,
            worker_count=worker_count,
        )
    sink = InMemoryProgressSink()
    outcome = services.monitoring.analyze(
        slate_date=slate_date,
        selected_game_ids=selected_game_ids,
        run_context=RunContext.create(actor="streamlit"),
        worker_count=worker_count,
        progress_sink=sink,
        use_cache=use_cache,
    )
    return outcome, list(sink.events)


def _games_by_id(games: list[DailyGame]) -> dict[str, DailyGame]:
    return {game.game_id: game for game in games}


def _render_saved_result(games: list[DailyGame]) -> None:
    error = st.session_state.get(_ERROR_KEY)
    if error:
        st.error(f"Analysis failed: {error}")

    events = st.session_state.get(_EVENTS_KEY, [])
    st.subheader("Agent activity")
    if isinstance(events, list):
        render_progress(events)

    st.subheader("Ranked upset watch")
    ranking = st.session_state.get(_RANKING_KEY)
    if isinstance(ranking, UpsetRanking):
        if st.session_state.get(_CACHE_KEY):
            st.caption(
                "Served from cache: this exact slate, selection, worker count, "
                "and precondition version were analyzed before."
            )
        saved_date = st.session_state.get(_DATE_KEY)
        if saved_date is not None:
            st.caption(f"Analysis date: {saved_date}")
        render_ranking(ranking, _games_by_id(games))
    elif not error:
        st.caption("Run an analysis to see ranked upset alerts.")


def main() -> None:
    """Render the app and execute the daily monitoring pipeline on demand."""
    st.set_page_config(
        page_title="Upset Condition Profiler",
        layout="wide",
    )
    settings = get_settings()

    st.title("NCAA Upset Condition Profiler")
    st.write(
        "Load the day's Division I slate, select games, and run the Monitor "
        "workers against validated upset preconditions."
    )

    with st.sidebar:
        st.header("Run controls")
        source = st.radio(
            "Data source",
            options=SOURCE_OPTIONS,
            index=0,
            key="ui_source",
            help=(
                "Fixture mode is deterministic and offline. Live ESPN fetches "
                "the current slate and schedule-derived rest features."
            ),
        )
        slate_date = st.date_input(
            "Slate date",
            value=_default_date(source),
            key=f"ui_date_{'fixture' if source == FIXTURE_SOURCE else 'live'}",
        )
        worker_count = st.number_input(
            "Monitor workers",
            min_value=1,
            max_value=8,
            value=settings.pipeline.monitor_worker_count,
            step=1,
            key="ui_workers",
        )
        use_cache = st.checkbox(
            "Reuse cached analyses",
            value=True,
            key="ui_use_cache",
            help="Identical selections reuse their stored ranking.",
        )
        force_refresh = st.checkbox(
            "Force refresh",
            value=False,
            key="ui_force_refresh",
            help="Ignore and replace existing slate and analysis cache entries.",
        )
        st.caption(
            "Fixture default date: "
            f"{FIXTURE_SLATE_DATE}. Live mode requires ESPN network access."
        )

    services: UiServices | None = None
    try:
        services = build_ui_services(settings, source)
        for warning in services.warnings:
            st.info(warning)

        if force_refresh:
            services.slate_provider.invalidate(slate_date)

        try:
            slate = SlateService(services.slate_provider).get_slate(slate_date)
        except UpsetProfilerError as exc:
            st.error(f"Could not load the slate: {exc}")
            _render_saved_result([])
            return

        if not slate.games:
            st.warning(
                "No Division I vs. Division I games were found for this date."
            )
            _render_saved_result([])
            return

        total_games = len(slate.games)
        metric_cols = st.columns(3)
        metric_cols[0].metric("D1 games", total_games)
        metric_cols[1].metric("Data source", source)
        metric_cols[2].metric("Slate date", slate_date.isoformat())

        st.subheader("Today's slate")
        filter_cols = st.columns([2, 1, 1, 1])
        query = filter_cols[0].text_input(
            "Search teams, venue, or game id",
            key="ui_filter_query",
        )
        conference = filter_cols[1].selectbox(
            "Conference",
            options=conference_options(slate.games),
            key="ui_filter_conference",
        )
        status = filter_cols[2].selectbox(
            "Status",
            options=STATUS_OPTIONS,
            key="ui_filter_status",
        )
        sort_by = filter_cols[3].selectbox(
            "Sort by",
            options=SORT_OPTIONS,
            key="ui_sort_by",
        )
        descending = st.checkbox("Sort descending", value=False, key="ui_sort_desc")

        visible_games = sort_games(
            filter_games(
                slate.games,
                query=query,
                conference=conference,
                status=status,
            ),
            sort_by=sort_by,
            descending=descending,
        )
        if not visible_games:
            st.warning("No games match the current filters.")
            _render_saved_result(slate.games)
            return

        st.dataframe(
            slate_rows(visible_games),
            hide_index=True,
            use_container_width=True,
        )

        visible_by_id = _games_by_id(visible_games)
        selected_game_ids = st.multiselect(
            "Select games to analyze (all visible games are selected by default)",
            options=list(visible_by_id),
            default=list(visible_by_id),
            format_func=lambda game_id: selection_label(visible_by_id[game_id]),
            key="ui_selected_games",
        )

        if st.button("Execute Search", type="primary", key="ui_execute"):
            if not selected_game_ids:
                st.warning("Select at least one game before running the analysis.")
            else:
                try:
                    outcome, events = _run_analysis(
                        services,
                        slate_date=slate_date,
                        selected_game_ids=list(selected_game_ids),
                        worker_count=int(worker_count),
                        use_cache=use_cache,
                        force_refresh=force_refresh,
                    )
                except (UpsetProfilerError, ValueError) as exc:
                    st.session_state[_ERROR_KEY] = str(exc)
                    st.session_state[_EVENTS_KEY] = []
                    st.session_state.pop(_RANKING_KEY, None)
                except Exception as exc:  # pragma: no cover - defensive UI boundary
                    st.session_state[_ERROR_KEY] = f"{type(exc).__name__}: {exc}"
                    st.session_state[_EVENTS_KEY] = []
                    st.session_state.pop(_RANKING_KEY, None)
                else:
                    st.session_state[_RANKING_KEY] = outcome.ranking
                    st.session_state[_EVENTS_KEY] = events
                    st.session_state[_CACHE_KEY] = outcome.from_cache
                    st.session_state[_ERROR_KEY] = None
                    st.session_state[_DATE_KEY] = slate_date.isoformat()

        _render_saved_result(slate.games)
    except UpsetProfilerError as exc:
        st.error(f"Application error: {exc}")
    finally:
        if services is not None:
            services.close()


if __name__ == "__main__":
    main()
