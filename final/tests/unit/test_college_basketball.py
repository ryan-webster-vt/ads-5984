"""Tests for historical College Basketball provider adapters."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.base import HttpClientConfig, create_http_client
from upset_profiler.integrations.college_basketball import (
    EspnHistoricalStatsProvider,
    FixtureHistoricalStatsProvider,
    parse_espn_summary,
)


def test_fixture_provider_lists_and_loads_games() -> None:
    provider = FixtureHistoricalStatsProvider()
    assert provider.list_game_ids("2024-2025") == [
        "401000001",
        "401000002",
        "401000003",
        "401000004",
    ]
    game = provider.get_game("401000001", "2024-2025")
    assert game.winner_team_id == "team-duke"
    assert game.odds is not None
    assert game.odds.favorite_team_id == "team-duke"


def test_fixture_provider_surfaces_invalid_records() -> None:
    provider = FixtureHistoricalStatsProvider()
    with pytest.raises(ValidationError):
        provider.get_game("401000004", "2024-2025")


def test_fixture_provider_rejects_unknown_game() -> None:
    with pytest.raises(IntegrationError, match="not found"):
        FixtureHistoricalStatsProvider().get_game("missing", "2024-2025")


def test_parse_espn_summary_fixture() -> None:
    payload = load_json("college_basketball", "espn_summary_401000001.json")
    game = parse_espn_summary(payload, game_id="401000001", season="2024-2025")

    assert game.home_score == 78
    assert game.away_score == 70
    assert game.winner_team_id == "team-duke"
    assert game.venue == "Cameron Indoor Stadium"
    assert game.odds is not None
    assert game.odds.favorite_team_id == "team-duke"

    duke = next(item for item in game.team_statistics if item.team_id == "team-duke")
    assert duke.field_goal_pct == pytest.approx(0.475)
    assert duke.stats["totalRebounds"] == 36.0

    assert game.player_statistics[0].player_id == "player-duke-1"
    assert game.player_statistics[0].stats["rebounds"] == 4.0


def _summary_payload_without_competition_odds() -> dict[str, Any]:
    payload = load_json("college_basketball", "espn_summary_401000001.json")
    assert isinstance(payload, dict)
    competition = payload["header"]["competitions"][0]
    competition.pop("odds", None)
    return payload


def test_parse_espn_summary_reads_odds_from_pickcenter() -> None:
    payload = _summary_payload_without_competition_odds()
    payload["pickcenter"] = [
        {
            "provider": {"name": "consensus"},
            "details": "DUKE -1.5",
            "spread": -1.5,
            "overUnder": 140.5,
            "homeTeamOdds": {"favorite": True, "moneyLine": -119},
            "awayTeamOdds": {"favorite": False, "moneyLine": -101},
            "moneyline": {"home": {}, "away": {}},
        }
    ]

    game = parse_espn_summary(payload, game_id="401000001", season="2024-2025")

    assert game.odds is not None
    assert game.odds.provider == "consensus"
    assert game.odds.favorite_team_id == "team-duke"
    assert game.odds.underdog_team_id == "team-clemson"
    assert game.odds.spread == pytest.approx(-1.5)
    assert game.odds.total == pytest.approx(140.5)
    assert game.odds.favorite_moneyline == -119
    assert game.odds.underdog_moneyline == -101


def test_parse_espn_summary_prefers_competition_odds() -> None:
    payload = load_json("college_basketball", "espn_summary_401000001.json")
    payload["pickcenter"] = [
        {
            "provider": {"name": "consensus"},
            "spread": -99.5,
            "overUnder": 999.0,
            "homeTeamOdds": {"favorite": False},
            "awayTeamOdds": {"favorite": True},
        }
    ]

    game = parse_espn_summary(payload, game_id="401000001", season="2024-2025")

    assert game.odds is not None
    assert game.odds.provider == "ESPN BET"
    assert game.odds.spread == pytest.approx(-5.5)
    assert game.odds.favorite_team_id == "team-duke"


def test_parse_espn_summary_ignores_pickcenter_without_lines() -> None:
    payload = _summary_payload_without_competition_odds()
    payload["pickcenter"] = [{"provider": {"name": "consensus"}, "moneyline": {}}]

    game = parse_espn_summary(payload, game_id="401000001", season="2024-2025")

    assert game.odds is None


def test_espn_provider_fetches_summary_over_http() -> None:
    payload = load_json("college_basketball", "espn_summary_401000001.json")
    client = create_http_client(
        HttpClientConfig(base_url="https://site.api.espn.com/test"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    provider = EspnHistoricalStatsProvider(client)

    game = provider.get_game("401000001", "2024-2025")
    assert game.game_id == "401000001"
    with pytest.raises(ConfigurationError, match="explicit --game-id"):
        provider.list_game_ids("2024-2025")
