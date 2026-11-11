"""Game, odds, team-statistic, and daily-slate contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from upset_profiler.context import utc_now
from upset_profiler.models.common import (
    Provenance,
    SeasonStr,
    StrictModel,
    TeamRef,
    UtcDatetime,
    VersionedModel,
)


class GameOdds(StrictModel):
    """Pregame odds used to define the favorite and the upset label."""

    provider: str = "espn"
    favorite_team_id: str = Field(min_length=1)
    underdog_team_id: str = Field(min_length=1)
    favorite_moneyline: int | None = None
    underdog_moneyline: int | None = None
    spread: float | None = None
    total: float | None = Field(default=None, ge=0)
    captured_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _favorite_and_underdog_differ(self) -> Self:
        if self.favorite_team_id == self.underdog_team_id:
            raise ValueError("favorite_team_id and underdog_team_id must differ")
        return self


class TeamStatistics(StrictModel):
    """Structured team statistics with a provider-specific extension mapping."""

    team_id: str = Field(min_length=1)
    games_played: int | None = Field(default=None, ge=0)
    points_per_game: float | None = Field(default=None, ge=0)
    points_allowed_per_game: float | None = Field(default=None, ge=0)
    field_goal_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    three_point_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    free_throw_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    rebounds_per_game: float | None = Field(default=None, ge=0)
    assists_per_game: float | None = Field(default=None, ge=0)
    turnovers_per_game: float | None = Field(default=None, ge=0)
    pace: float | None = Field(default=None, ge=0)
    offensive_efficiency: float | None = Field(default=None, ge=0)
    defensive_efficiency: float | None = Field(default=None, ge=0)
    adjusted_efficiency_margin: float | None = None
    stats: dict[str, float] = Field(default_factory=dict)
    captured_at: UtcDatetime = Field(default_factory=utc_now)


class PlayerStatistics(StrictModel):
    """Structured player statistics retained for completeness and context."""

    player_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    position: str | None = None
    games_played: int | None = Field(default=None, ge=0)
    minutes_per_game: float | None = Field(default=None, ge=0)
    points_per_game: float | None = Field(default=None, ge=0)
    rebounds_per_game: float | None = Field(default=None, ge=0)
    assists_per_game: float | None = Field(default=None, ge=0)
    stats: dict[str, float] = Field(default_factory=dict)
    captured_at: UtcDatetime = Field(default_factory=utc_now)


class HistoricalGame(VersionedModel):
    """A completed historical game used for research and extraction."""

    game_id: str = Field(min_length=1)
    season: SeasonStr
    game_date: date
    tipoff_at: UtcDatetime
    home_team: TeamRef
    away_team: TeamRef
    neutral_site: bool = False
    venue: str | None = None
    completed: bool = True
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    winner_team_id: str | None = None
    odds: GameOdds | None = None
    team_statistics: list[TeamStatistics] = Field(default_factory=list)
    player_statistics: list[PlayerStatistics] = Field(default_factory=list)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _validate_game_result(self) -> Self:
        if self.home_team.team_id == self.away_team.team_id:
            raise ValueError("home_team and away_team must differ")
        if (self.home_score is None) != (self.away_score is None):
            raise ValueError("home_score and away_score must both be set or both be absent")
        if self.completed and self.home_score is None:
            raise ValueError("completed games require home_score and away_score")
        if self.home_score is not None and self.away_score is not None:
            if self.home_score == self.away_score:
                raise ValueError("completed games must not end in a tie")
            home_won = self.home_score > self.away_score
            expected_winner = (
                self.home_team.team_id if home_won else self.away_team.team_id
            )
            if self.winner_team_id is not None and self.winner_team_id != expected_winner:
                raise ValueError("winner_team_id does not match the final score")
        if self.odds is not None:
            known_team_ids = {self.home_team.team_id, self.away_team.team_id}
            odds_team_ids = {self.odds.favorite_team_id, self.odds.underdog_team_id}
            if odds_team_ids != known_team_ids:
                raise ValueError("odds teams must match the game's home and away teams")
        return self


class DailyGame(VersionedModel):
    """A game on the current day's slate, including pregame context."""

    game_id: str = Field(min_length=1)
    slate_date: date
    tipoff_at: UtcDatetime
    home_team: TeamRef
    away_team: TeamRef
    neutral_site: bool = False
    venue: str | None = None
    status: Literal["scheduled", "in_progress", "final", "postponed", "canceled"] = "scheduled"
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    odds: GameOdds | None = None
    team_statistics: list[TeamStatistics] = Field(default_factory=list)
    player_statistics: list[PlayerStatistics] = Field(default_factory=list)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _validate_daily_game(self) -> Self:
        if self.home_team.team_id == self.away_team.team_id:
            raise ValueError("home_team and away_team must differ")
        if (self.home_score is None) != (self.away_score is None):
            raise ValueError("home_score and away_score must both be set or both be absent")
        if self.odds is not None:
            known_team_ids = {self.home_team.team_id, self.away_team.team_id}
            odds_team_ids = {self.odds.favorite_team_id, self.odds.underdog_team_id}
            if odds_team_ids != known_team_ids:
                raise ValueError("odds teams must match the game's home and away teams")
        return self


class DailySlate(VersionedModel):
    """The Division I vs. Division I slate for one calendar date."""

    slate_date: date
    games: list[DailyGame] = Field(default_factory=list)
    source: str = "espn"
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_slate(self) -> Self:
        game_ids = [game.game_id for game in self.games]
        if len(game_ids) != len(set(game_ids)):
            raise ValueError("daily slate contains duplicate game_id values")
        for game in self.games:
            if game.slate_date != self.slate_date:
                raise ValueError("every game must match the slate_date")
        return self
