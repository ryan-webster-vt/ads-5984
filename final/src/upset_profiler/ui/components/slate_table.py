"""Slate filtering, sorting, and table-row helpers.

These functions are pure so the Streamlit UI and its tests can verify slate
behavior without running the full app.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from upset_profiler.models.games import DailyGame

SORT_OPTIONS = ("Tip-off", "Matchup", "Venue")
STATUS_OPTIONS = ("All", "scheduled", "in_progress", "final", "postponed", "canceled")
_ALL = "All"
_UNKNOWN = "Unknown"


def conference_options(games: Iterable[DailyGame]) -> list[str]:
    """Return sorted conference names present on the slate."""
    conferences = {
        game.home_team.conference or _UNKNOWN for game in games
    } | {
        game.away_team.conference or _UNKNOWN for game in games
    }
    return [_ALL, *sorted(conferences)]


def filter_games(
    games: Sequence[DailyGame],
    *,
    query: str = "",
    conference: str = _ALL,
    status: str = _ALL,
) -> list[DailyGame]:
    """Filter games by free text, conference, and status."""
    needle = query.strip().lower()
    filtered: list[DailyGame] = []
    for game in games:
        if status != _ALL and game.status != status:
            continue
        if conference != _ALL:
            conferences = {
                game.home_team.conference or _UNKNOWN,
                game.away_team.conference or _UNKNOWN,
            }
            if conference not in conferences:
                continue
        if needle:
            haystack = " ".join(
                [
                    game.home_team.name,
                    game.home_team.abbreviation or "",
                    game.away_team.name,
                    game.away_team.abbreviation or "",
                    game.venue or "",
                    game.game_id,
                ]
            ).lower()
            if needle not in haystack:
                continue
        filtered.append(game)
    return filtered


def sort_games(
    games: Sequence[DailyGame],
    *,
    sort_by: str = "Tip-off",
    descending: bool = False,
) -> list[DailyGame]:
    """Sort games with a stable game-id tie-breaker."""
    if sort_by not in SORT_OPTIONS:
        raise ValueError(f"unsupported sort option {sort_by!r}")
    key = {
        "Tip-off": lambda game: (game.tipoff_at, game.game_id),
        "Matchup": lambda game: (
            f"{game.away_team.name} at {game.home_team.name}",
            game.game_id,
        ),
        "Venue": lambda game: (game.venue or "", game.game_id),
    }[sort_by]
    return sorted(games, key=key, reverse=descending)


def matchup_label(game: DailyGame) -> str:
    """Return a compact ``away at home`` label."""
    return f"{game.away_team.name} at {game.home_team.name}"


def selection_label(game: DailyGame) -> str:
    """Return the label used by the game-selection widget."""
    tipoff = game.tipoff_at.strftime("%H:%M UTC")
    return f"{matchup_label(game)} ({tipoff})"


def slate_rows(games: Sequence[DailyGame]) -> list[dict[str, object]]:
    """Build display rows for ``st.dataframe``."""
    rows: list[dict[str, object]] = []
    for game in games:
        favorite = game.odds.favorite_team_id if game.odds else None
        favorite_name = "Unknown"
        if favorite is not None:
            favorite_name = (
                game.home_team.name
                if favorite == game.home_team.team_id
                else game.away_team.name
            )
        rows.append(
            {
                "Tip-off (UTC)": game.tipoff_at.strftime("%Y-%m-%d %H:%M"),
                "Matchup": matchup_label(game),
                "Venue": game.venue or "",
                "Status": game.status,
                "Favorite": favorite_name,
                "Spread": None if game.odds is None else game.odds.spread,
                "Total": None if game.odds is None else game.odds.total,
                "Game ID": game.game_id,
            }
        )
    return rows
