"""Unit tests for pure Streamlit slate helpers."""

from __future__ import annotations

import pytest

from tests.factories import make_daily_game
from upset_profiler.ui.components.slate_table import (
    conference_options,
    filter_games,
    matchup_label,
    slate_rows,
    sort_games,
)


@pytest.mark.unit
def test_filter_games_matches_query_conference_and_status() -> None:
    game = make_daily_game("401000001")
    other = make_daily_game("401000002").model_copy(
        update={"venue": "Allen Fieldhouse", "status": "final"}
    )

    assert filter_games([game, other], query="home team") == [game, other]
    assert filter_games([game, other], query="allen") == [other]
    assert filter_games([game, other], status="final") == [other]
    assert filter_games([game, other], conference="ACC") == [game, other]
    assert filter_games([game, other], conference="Big 12") == []


@pytest.mark.unit
def test_conference_options_are_sorted_with_all_first() -> None:
    games = [make_daily_game("1"), make_daily_game("2")]
    games[1].home_team.conference = "Big 12"

    assert conference_options(games) == ["All", "ACC", "Big 12"]


@pytest.mark.unit
def test_sort_games_is_stable_and_supports_matchup() -> None:
    first = make_daily_game("401000002")
    second = make_daily_game("401000001")

    by_id = sort_games([first, second], sort_by="Matchup")
    by_tipoff = sort_games([first, second], sort_by="Tip-off")

    assert [game.game_id for game in by_id] == ["401000001", "401000002"]
    assert [game.game_id for game in by_tipoff] == ["401000001", "401000002"]


@pytest.mark.unit
def test_sort_games_rejects_unknown_option() -> None:
    with pytest.raises(ValueError, match="unsupported sort option"):
        sort_games([make_daily_game()], sort_by="Random")


@pytest.mark.unit
def test_slate_rows_include_odds_and_game_id() -> None:
    game = make_daily_game("401000001")

    row = slate_rows([game])[0]

    assert row["Game ID"] == "401000001"
    assert row["Favorite"] == "Home Team"
    assert row["Spread"] == -5.5
    assert row["Matchup"] == matchup_label(game)
