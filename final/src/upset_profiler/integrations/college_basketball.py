"""Historical College Basketball statistics providers.

The provider interface abstracts the unspecified historical sports API from the
rest of the system. The initial live implementation uses ESPN's public game
summary endpoint; the fixture implementation keeps ingestion deterministic and
offline. Workers never see provider-specific JSON: both paths return validated
:class:`HistoricalGame` contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from upset_profiler.config import Settings
from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.base import (
    BaseHttpClient,
    HttpClientConfig,
    create_http_client,
)
from upset_profiler.integrations.espn import parse_espn_scoreboard
from upset_profiler.models.games import (
    DailyGame,
    HistoricalGame,
    PlayerStatistics,
    TeamStatistics,
)


class CollegeBasketballProvider(Protocol):
    """Provider of completed historical games by id."""

    source: str

    def list_game_ids(self, season: str) -> list[str]:
        """Return known game ids for a season when the provider can enumerate."""
        ...

    def get_game(self, game_id: str, season: str) -> HistoricalGame:
        """Return one completed historical game."""
        ...


def _as_mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
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


def _parse_pct(value: Any) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or parsed < 0:
        return None
    pct = parsed / 100 if parsed > 1 else parsed
    return pct if pct <= 1 else None


def _stats_mapping(item: dict[str, Any]) -> dict[str, float]:
    stats: dict[str, float] = {}
    for raw in _as_list(item.get("statistics")):
        stat = _as_mapping(raw)
        if stat is None:
            continue
        name = stat.get("name")
        value = _parse_float(stat.get("displayValue", stat.get("value")))
        if isinstance(name, str) and name and value is not None:
            stats[name] = value
    return stats


def _team_statistics(payload: dict[str, Any], game: DailyGame) -> list[TeamStatistics]:
    boxscore = _as_mapping(payload.get("boxscore")) or {}
    known = {game.home_team.team_id, game.away_team.team_id}
    result: list[TeamStatistics] = []
    for raw in _as_list(boxscore.get("teams")):
        item = _as_mapping(raw)
        if item is None:
            continue
        team = _as_mapping(item.get("team")) or {}
        team_id = team.get("id")
        if not isinstance(team_id, str) or team_id not in known:
            continue
        stats = _stats_mapping(item)
        result.append(
            TeamStatistics(
                team_id=team_id,
                field_goal_pct=_parse_pct(stats.get("fieldGoalPct")),
                three_point_pct=_parse_pct(stats.get("threePointFieldGoalPct")),
                free_throw_pct=_parse_pct(stats.get("freeThrowPct")),
                stats=stats,
            )
        )
    result.sort(key=lambda item: item.team_id)
    return result


def _player_statistics(payload: dict[str, Any], game: DailyGame) -> list[PlayerStatistics]:
    boxscore = _as_mapping(payload.get("boxscore")) or {}
    known = {game.home_team.team_id, game.away_team.team_id}
    players: dict[str, PlayerStatistics] = {}
    for raw_team in _as_list(boxscore.get("players")):
        team_group = _as_mapping(raw_team)
        if team_group is None:
            continue
        team = _as_mapping(team_group.get("team")) or {}
        team_id = team.get("id")
        if not isinstance(team_id, str) or team_id not in known:
            continue
        for raw_category in _as_list(team_group.get("statistics")):
            category = _as_mapping(raw_category)
            if category is None:
                continue
            keys = [key for key in _as_list(category.get("keys")) if isinstance(key, str)]
            for raw_athlete in _as_list(category.get("athletes")):
                entry = _as_mapping(raw_athlete)
                if entry is None:
                    continue
                athlete = _as_mapping(entry.get("athlete")) or {}
                player_id = athlete.get("id")
                name = athlete.get("displayName") or athlete.get("shortName")
                if not isinstance(player_id, str) or not isinstance(name, str):
                    continue
                existing = players.get(player_id)
                stats = dict(existing.stats) if existing is not None else {}
                for key, value in zip(keys, _as_list(entry.get("stats")), strict=False):
                    parsed = _parse_float(value)
                    if parsed is not None:
                        stats[key] = parsed
                players[player_id] = PlayerStatistics(
                    player_id=player_id,
                    name=name,
                    team_id=team_id,
                    position=athlete.get("position")
                    if isinstance(athlete.get("position"), str)
                    else None,
                    stats=stats,
                    captured_at=game.tipoff_at,
                )
    return [players[player_id] for player_id in sorted(players)]


def _competition_with_odds(
    payload: dict[str, Any],
    competition: dict[str, Any],
) -> dict[str, Any]:
    """Attach betting lines to a summary competition when it has none.

    The scoreboard shape the parser expects keeps odds on the competition, but
    ESPN summary payloads expose them in a top-level ``pickcenter`` (or
    ``odds``) list instead, so live ingestion produced games with ``odds=None``.
    """
    if _as_list(competition.get("odds")):
        return competition
    for key in ("pickcenter", "odds"):
        for item in _as_list(payload.get(key)):
            candidate = _as_mapping(item)
            if candidate is None:
                continue
            has_line = (
                _as_mapping(candidate.get("homeTeamOdds")) is not None
                or _as_mapping(candidate.get("awayTeamOdds")) is not None
                or candidate.get("spread") is not None
            )
            if not has_line:
                continue
            enriched = dict(competition)
            enriched["odds"] = [candidate]
            return enriched
    return competition


def parse_espn_summary(
    payload: Any,
    *,
    game_id: str,
    season: str,
) -> HistoricalGame:
    """Convert an ESPN game-summary payload into a validated historical game."""
    if not isinstance(payload, dict):
        raise IntegrationError("ESPN summary payload must be an object", retryable=False)
    header = _as_mapping(payload.get("header"))
    if header is None:
        raise IntegrationError("ESPN summary is missing header data", retryable=False)
    competitions = _as_list(header.get("competitions"))
    if not competitions:
        raise IntegrationError("ESPN summary is missing competitions", retryable=False)
    competition = _as_mapping(competitions[0])
    if competition is None:
        raise IntegrationError("ESPN summary competition is malformed", retryable=False)
    competition = _competition_with_odds(payload, competition)
    tipoff_at = _parse_datetime(competition.get("date") or header.get("date"))
    if tipoff_at is None:
        raise IntegrationError("ESPN summary is missing a game date", retryable=False)

    event = {
        "id": game_id,
        "date": tipoff_at.isoformat(),
        "competitions": [competition],
    }
    slate = parse_espn_scoreboard({"events": [event]}, tipoff_at.date())
    if not slate.games:
        raise IntegrationError(
            "ESPN summary did not contain a parseable final game",
            context={"game_id": game_id},
            retryable=False,
        )
    daily = slate.games[0]
    if daily.status != "final" or daily.home_score is None or daily.away_score is None:
        raise IntegrationError(
            "historical ingestion requires a completed game",
            context={"game_id": game_id, "status": daily.status},
            retryable=False,
        )
    winner = (
        daily.home_team.team_id
        if daily.home_score > daily.away_score
        else daily.away_team.team_id
    )
    return HistoricalGame(
        game_id=game_id,
        season=season,
        game_date=daily.slate_date,
        tipoff_at=daily.tipoff_at,
        home_team=daily.home_team,
        away_team=daily.away_team,
        neutral_site=daily.neutral_site,
        venue=daily.venue,
        completed=True,
        home_score=daily.home_score,
        away_score=daily.away_score,
        winner_team_id=winner,
        odds=daily.odds,
        team_statistics=_team_statistics(payload, daily),
        player_statistics=_player_statistics(payload, daily),
    )


class FixtureHistoricalStatsProvider:
    """Load historical games from ``tests/fixtures/college_basketball``."""

    source = "fixture"

    def __init__(self, fixture_name: str = "games.json") -> None:
        payload = load_json("college_basketball", fixture_name)
        if not isinstance(payload, list):
            raise ConfigurationError("historical games fixture must be a JSON list")
        self._raw: dict[str, Any] = {}
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("game_id"), str):
                self._raw[item["game_id"]] = item

    def list_game_ids(self, season: str) -> list[str]:
        """Return fixture game ids, optionally filtered to one season."""
        return sorted(
            game_id
            for game_id, raw in self._raw.items()
            if raw.get("season") == season
        )

    def get_game(self, game_id: str, season: str) -> HistoricalGame:
        """Validate and return one fixture game."""
        raw = self._raw.get(game_id)
        if raw is None:
            raise IntegrationError(
                "historical fixture game not found",
                context={"game_id": game_id},
                retryable=False,
            )
        return HistoricalGame.model_validate(raw)


class EspnHistoricalStatsProvider:
    """Fetch completed games from ESPN's public summary endpoint."""

    source = "espn"

    def __init__(self, client: BaseHttpClient) -> None:
        self._client = client

    def list_game_ids(self, season: str) -> list[str]:
        """ESPN summaries require explicit ids; callers must supply them."""
        raise ConfigurationError(
            "live ESPN ingestion requires explicit --game-id values",
            context={"season": season},
        )

    def get_game(self, game_id: str, season: str) -> HistoricalGame:
        """Fetch and parse one ESPN game summary."""
        payload = self._client.get_json("summary", params={"event": game_id})
        return parse_espn_summary(payload, game_id=game_id, season=season)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()


def build_espn_historical_provider(settings: Settings) -> EspnHistoricalStatsProvider:
    """Build the live historical provider from typed settings."""
    client = create_http_client(
        HttpClientConfig(
            base_url=settings.espn.espn_base_url,
            timeout_seconds=settings.espn.espn_timeout_seconds,
        )
    )
    return EspnHistoricalStatsProvider(client)
