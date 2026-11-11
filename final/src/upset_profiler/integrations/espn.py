"""ESPN daily slate and schedule adapters.

All network access for the live daily path lives here. The adapter converts the
public ESPN scoreboard and team-schedule payloads into the strict contracts used
by the slate and monitoring services; downstream code never sees ESPN JSON.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from upset_profiler.config import Settings
from upset_profiler.context import utc_now
from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.integrations.base import (
    BaseHttpClient,
    HttpClientConfig,
    create_http_client,
)
from upset_profiler.models.common import TeamRef
from upset_profiler.models.games import DailyGame, DailySlate, GameOdds
from upset_profiler.models.monitoring import GameContext

_STATUS_MAP = {
    "pre": "scheduled",
    "in": "in_progress",
    "post": "final",
}
_TERMINAL_STATUS_NAMES = {
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled",
    "STATUS_CANCELLED": "canceled",
}


def _as_mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_moneyline(value: Any) -> int | None:
    if isinstance(value, str):
        value = value.strip()
        if not value or value.upper() in {"EVEN", "EV"}:
            return 0 if value else None
        try:
            return int(value)
        except ValueError:
            return None
    return _parse_int(value)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _team_ref(team: dict[str, Any]) -> TeamRef | None:
    team_id = team.get("id")
    name = team.get("displayName") or team.get("name")
    if not isinstance(team_id, str) or not team_id or not isinstance(name, str) or not name:
        return None
    abbreviation = team.get("abbreviation")
    return TeamRef(
        team_id=team_id,
        name=name,
        abbreviation=abbreviation if isinstance(abbreviation, str) else None,
        conference=None,
        division="D1",
    )


def _odds_from_competition(
    competition: dict[str, Any],
    home_team_id: str,
    away_team_id: str,
) -> GameOdds | None:
    odds_items = _as_list(competition.get("odds"))
    if not odds_items:
        return None
    odds = _as_mapping(odds_items[0])
    if odds is None:
        return None

    home_odds = _as_mapping(odds.get("homeTeamOdds")) or {}
    away_odds = _as_mapping(odds.get("awayTeamOdds")) or {}
    favorite_team_id: str | None
    underdog_team_id: str | None
    if home_odds.get("favorite") is True:
        favorite_team_id, underdog_team_id = home_team_id, away_team_id
    elif away_odds.get("favorite") is True:
        favorite_team_id, underdog_team_id = away_team_id, home_team_id
    else:
        favorite_team_id, underdog_team_id = _favorite_from_moneyline(
            odds,
            home_team_id,
            away_team_id,
        )
        if favorite_team_id is None or underdog_team_id is None:
            return None

    home_moneyline = _moneyline_close(odds, "home")
    away_moneyline = _moneyline_close(odds, "away")

    provider = _as_mapping(odds.get("provider")) or {}
    provider_name = provider.get("name")
    return GameOdds(
        provider=provider_name if isinstance(provider_name, str) else "espn",
        favorite_team_id=favorite_team_id,
        underdog_team_id=underdog_team_id,
        favorite_moneyline=(
            home_moneyline if favorite_team_id == home_team_id else away_moneyline
        ),
        underdog_moneyline=(
            away_moneyline if favorite_team_id == home_team_id else home_moneyline
        ),
        spread=_parse_float(odds.get("spread")),
        total=_parse_float(odds.get("overUnder")),
        captured_at=utc_now(),
    )


def _moneyline_close(odds: dict[str, Any], side: str) -> int | None:
    moneyline = _as_mapping(odds.get("moneyline")) or {}
    side_mapping = _as_mapping(moneyline.get(side)) or {}
    close = _as_mapping(side_mapping.get("close")) or {}
    return _parse_moneyline(close.get("odds"))


def _favorite_from_moneyline(
    odds: dict[str, Any],
    home_team_id: str,
    away_team_id: str,
) -> tuple[str | None, str | None]:
    home_moneyline = _moneyline_close(odds, "home")
    away_moneyline = _moneyline_close(odds, "away")
    if home_moneyline is None or away_moneyline is None:
        return None, None
    if home_moneyline == away_moneyline:
        return None, None
    if home_moneyline < away_moneyline:
        return home_team_id, away_team_id
    return away_team_id, home_team_id


def _status_for(competition: dict[str, Any]) -> str:
    status = _as_mapping(competition.get("status")) or {}
    status_type = _as_mapping(status.get("type")) or {}
    name = str(status_type.get("name") or "")
    if name in _TERMINAL_STATUS_NAMES:
        return _TERMINAL_STATUS_NAMES[name]
    state = status_type.get("state")
    if isinstance(state, str) and state in _STATUS_MAP:
        return _STATUS_MAP[state]
    if status_type.get("completed") is True:
        return "final"
    return "scheduled"


def _parse_event(event: dict[str, Any], slate_date: date) -> DailyGame | None:
    event_id = event.get("id")
    competitions = _as_list(event.get("competitions"))
    if not isinstance(event_id, str) or not event_id or not competitions:
        return None
    competition = _as_mapping(competitions[0])
    if competition is None:
        return None

    home: dict[str, Any] | None = None
    away: dict[str, Any] | None = None
    home_score: int | None = None
    away_score: int | None = None
    for raw_competitor in _as_list(competition.get("competitors")):
        competitor = _as_mapping(raw_competitor)
        if competitor is None:
            continue
        side = competitor.get("homeAway")
        if side == "home":
            home = competitor
            home_score = _parse_int(competitor.get("score"))
        elif side == "away":
            away = competitor
            away_score = _parse_int(competitor.get("score"))
    if home is None or away is None:
        return None

    home_team = _team_ref(_as_mapping(home.get("team")) or {})
    away_team = _team_ref(_as_mapping(away.get("team")) or {})
    if home_team is None or away_team is None:
        return None
    if (home_score is None) != (away_score is None):
        home_score = None
        away_score = None

    tipoff_at = _parse_datetime(event.get("date") or competition.get("date"))
    if tipoff_at is None:
        return None
    venue = _as_mapping(competition.get("venue")) or {}
    venue_name = venue.get("fullName")
    try:
        return DailyGame(
            game_id=event_id,
            slate_date=slate_date,
            tipoff_at=tipoff_at,
            home_team=home_team,
            away_team=away_team,
            neutral_site=bool(competition.get("neutralSite", False)),
            venue=venue_name if isinstance(venue_name, str) else None,
            status=_status_for(competition),
            home_score=home_score,
            away_score=away_score,
            odds=_odds_from_competition(
                competition,
                home_team.team_id,
                away_team.team_id,
            ),
        )
    except ValueError:
        return None


def parse_espn_scoreboard(payload: Any, slate_date: date) -> DailySlate:
    """Convert an ESPN scoreboard payload into a validated daily slate."""
    if not isinstance(payload, dict):
        raise IntegrationError("ESPN scoreboard payload must be an object", retryable=False)
    events = payload.get("events")
    if events is None:
        events = []
    if not isinstance(events, list):
        raise IntegrationError("ESPN scoreboard events must be a list", retryable=False)

    games: list[DailyGame] = []
    for raw_event in events:
        event = _as_mapping(raw_event)
        if event is None:
            continue
        game = _parse_event(event, slate_date)
        if game is not None:
            games.append(game)
    return DailySlate(
        slate_date=slate_date,
        games=games,
        source="espn",
        retrieved_at=utc_now(),
    )


class EspnSlateProvider:
    """Fetch one day's Division I scoreboard from ESPN."""

    def __init__(
        self,
        client: BaseHttpClient,
        *,
        groups: str = "50",
        limit: int = 500,
    ) -> None:
        if limit < 1:
            raise ConfigurationError("ESPN scoreboard limit must be at least 1")
        self._client = client
        self._groups = groups
        self._limit = limit

    def get_slate(self, slate_date: date) -> DailySlate:
        """Return ESPN's scoreboard converted to a validated slate."""
        payload = self._client.get_json(
            "scoreboard",
            params={
                "dates": slate_date.strftime("%Y%m%d"),
                "groups": self._groups,
                "limit": str(self._limit),
            },
        )
        return parse_espn_scoreboard(payload, slate_date)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()


def _season_for_date(value: date) -> int:
    return value.year + 1 if value.month >= 7 else value.year


def _previous_game_date(
    payload: Any,
    *,
    team_id: str,
    before: datetime,
) -> date | None:
    if not isinstance(payload, dict):
        raise IntegrationError("ESPN schedule payload must be an object", retryable=False)
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise IntegrationError("ESPN schedule events must be a list", retryable=False)

    previous: date | None = None
    for raw_event in events:
        event = _as_mapping(raw_event)
        if event is None:
            continue
        competitions = _as_list(event.get("competitions"))
        if not competitions:
            continue
        competition = _as_mapping(competitions[0]) or {}
        status = _as_mapping(competition.get("status")) or {}
        status_type = _as_mapping(status.get("type")) or {}
        if status_type.get("completed") is not True:
            continue
        teams = {
            str((_as_mapping(competitor) or {}).get("team", {}).get("id"))
            for competitor in _as_list(competition.get("competitors"))
        }
        if team_id not in teams:
            continue
        event_date = _parse_datetime(event.get("date"))
        if event_date is None or event_date >= before:
            continue
        candidate = event_date.date()
        if previous is None or candidate > previous:
            previous = candidate
    return previous


class EspnContextProvider:
    """Build deterministic rest features from ESPN team schedules."""

    def __init__(self, client: BaseHttpClient) -> None:
        self._client = client

    def get_context(self, game: DailyGame) -> GameContext:
        """Return schedule-derived pregame features for one daily game."""
        season = _season_for_date(game.slate_date)
        favorite = game.odds.favorite_team_id if game.odds else None
        underdog = game.odds.underdog_team_id if game.odds else None
        rest_by_team: dict[str, int] = {}
        for team in (game.home_team, game.away_team):
            payload = self._client.get_json(
                f"teams/{team.team_id}/schedule",
                params={"season": str(season)},
            )
            previous = _previous_game_date(
                payload,
                team_id=team.team_id,
                before=game.tipoff_at,
            )
            if previous is not None:
                rest_by_team[team.team_id] = (game.slate_date - previous).days

        features: dict[str, int | bool] = {}
        if favorite is not None and underdog is not None:
            favorite_rest = rest_by_team.get(favorite)
            underdog_rest = rest_by_team.get(underdog)
            if favorite_rest is not None and underdog_rest is not None:
                features["rest_days_difference"] = underdog_rest - favorite_rest
            if underdog_rest is not None:
                features["underdog_back_to_back"] = underdog_rest == 1
        return GameContext(game=game, features=features, retrieved_at=utc_now())

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()


def build_espn_slate_provider(settings: Settings) -> EspnSlateProvider:
    """Build the live slate provider from typed settings."""
    client = create_http_client(
        HttpClientConfig(
            base_url=settings.espn.espn_base_url,
            timeout_seconds=settings.espn.espn_timeout_seconds,
        )
    )
    return EspnSlateProvider(client)


def build_espn_context_provider(settings: Settings) -> EspnContextProvider:
    """Build the live context provider from typed settings."""
    client = create_http_client(
        HttpClientConfig(
            base_url=settings.espn.espn_base_url,
            timeout_seconds=settings.espn.espn_timeout_seconds,
        )
    )
    return EspnContextProvider(client)
