"""Unit tests for the ESPN scoreboard and schedule adapters."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from upset_profiler.errors import IntegrationError
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.base import HttpClientConfig, create_http_client
from upset_profiler.integrations.espn import (
    EspnContextProvider,
    EspnSlateProvider,
    parse_espn_scoreboard,
)
from upset_profiler.models.games import DailySlate

_BASE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
)
_SLATE_DATE = date(2026, 11, 10)


def _scoreboard_payload() -> dict[str, object]:
    payload = load_json("espn", "scoreboard_2026-11-10.json")
    assert isinstance(payload, dict)
    return payload


@pytest.mark.unit
def test_parse_espn_scoreboard_maps_games_odds_and_status() -> None:
    slate = parse_espn_scoreboard(_scoreboard_payload(), _SLATE_DATE)

    assert isinstance(slate, DailySlate)
    assert slate.source == "espn"
    assert [game.game_id for game in slate.games] == [
        "401920866",
        "401909514",
        "401909564",
        "401928828",
    ]
    first = slate.games[0]
    assert first.home_team.team_id == "164"
    assert first.home_team.division == "D1"
    assert first.away_team.team_id == "2885"
    assert first.status == "scheduled"
    assert first.venue is not None
    assert "Jersey Mike" in first.venue
    assert first.odds is not None
    assert first.odds.favorite_team_id == "164"
    assert first.odds.underdog_team_id == "2885"
    assert first.odds.favorite_moneyline == -8000
    assert first.odds.underdog_moneyline == 2200
    assert first.odds.spread == -25.5
    assert first.odds.total == 143.5

    away_favorite = slate.games[3]
    assert away_favorite.odds is not None
    assert away_favorite.odds.favorite_team_id == "2750"


@pytest.mark.unit
def test_parse_espn_scoreboard_accepts_empty_events() -> None:
    slate = parse_espn_scoreboard({"events": []}, _SLATE_DATE)

    assert slate.games == []
    assert slate.slate_date == _SLATE_DATE


@pytest.mark.unit
def test_parse_espn_scoreboard_rejects_non_object_payload() -> None:
    with pytest.raises(IntegrationError):
        parse_espn_scoreboard([], _SLATE_DATE)


@pytest.mark.unit
def test_parse_espn_scoreboard_skips_malformed_events() -> None:
    payload = _scoreboard_payload()
    payload["events"] = [{"id": "broken"}, *_scoreboard_payload()["events"]]  # type: ignore[index]

    slate = parse_espn_scoreboard(payload, _SLATE_DATE)

    assert len(slate.games) == 4


@pytest.mark.unit
@respx.mock
def test_espn_slate_provider_calls_scoreboard_endpoint() -> None:
    route = respx.get(f"{_BASE_URL}/scoreboard").mock(
        return_value=httpx.Response(200, json=_scoreboard_payload())
    )
    client = create_http_client(HttpClientConfig(base_url=_BASE_URL))
    provider = EspnSlateProvider(client)
    try:
        slate = provider.get_slate(_SLATE_DATE)
    finally:
        provider.close()

    assert route.called
    request = route.calls[0].request
    assert request.url.params["dates"] == "20261110"
    assert request.url.params["groups"] == "50"
    assert len(slate.games) == 4


def _schedule_payload(team_id: str, dates: list[str]) -> dict[str, object]:
    events = []
    for index, event_date in enumerate(dates):
        events.append(
            {
                "id": f"{team_id}-{index}",
                "date": event_date,
                "competitions": [
                    {
                        "status": {
                            "type": {
                                "name": "STATUS_FINAL",
                                "state": "post",
                                "completed": True,
                            }
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"id": team_id},
                            },
                            {
                                "homeAway": "away",
                                "team": {"id": "opponent"},
                            },
                        ],
                    }
                ],
            }
        )
    return {"events": events}


@pytest.mark.unit
@respx.mock
def test_espn_context_provider_derives_rest_features() -> None:
    respx.get(f"{_BASE_URL}/teams/164/schedule").mock(
        return_value=httpx.Response(
            200,
            json=_schedule_payload("164", ["2026-11-07T00:00Z", "2026-11-01T00:00Z"]),
        )
    )
    respx.get(f"{_BASE_URL}/teams/2885/schedule").mock(
        return_value=httpx.Response(
            200,
            json=_schedule_payload("2885", ["2026-11-09T00:00Z"]),
        )
    )
    slate = parse_espn_scoreboard(_scoreboard_payload(), _SLATE_DATE)
    client = create_http_client(HttpClientConfig(base_url=_BASE_URL))
    provider = EspnContextProvider(client)
    try:
        context = provider.get_context(slate.games[0])
    finally:
        provider.close()

    assert context.features["rest_days_difference"] == -2
    assert context.features["underdog_back_to_back"] is True
