"""Daily slate retrieval, Division I filtering, and game selection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from upset_profiler.errors import ConfigurationError
from upset_profiler.models.games import DailyGame, DailySlate


class SlateProvider(Protocol):
    """Provider of one day's college basketball slate."""

    def get_slate(self, slate_date: date) -> DailySlate:
        """Return the slate for one calendar date."""
        ...


def _is_division_one(game: DailyGame) -> bool:
    return (
        game.home_team.division.upper() == "D1"
        and game.away_team.division.upper() == "D1"
    )


class SlateService:
    """Apply slate policy on top of a slate provider."""

    def __init__(self, provider: SlateProvider) -> None:
        self._provider = provider

    def get_slate(self, slate_date: date) -> DailySlate:
        """Return the D1-vs-D1 slate for one date."""
        slate = self._provider.get_slate(slate_date)
        games = [game for game in slate.games if _is_division_one(game)]
        return DailySlate(
            schema_version=slate.schema_version,
            slate_date=slate.slate_date,
            games=games,
            source=slate.source,
            retrieved_at=slate.retrieved_at,
        )

    def select_games(
        self,
        slate: DailySlate,
        game_ids: Sequence[str] | None = None,
    ) -> list[DailyGame]:
        """Select games by id, preserving slate order; all games by default."""
        if game_ids is None:
            return list(slate.games)
        selected = set(game_ids)
        available = {game.game_id for game in slate.games}
        missing = sorted(selected - available)
        if missing:
            raise ConfigurationError(
                "selected games are not on the slate",
                context={"missing_game_ids": ", ".join(missing)},
            )
        return [game for game in slate.games if game.game_id in selected]
