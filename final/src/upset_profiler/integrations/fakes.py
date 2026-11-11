"""Deterministic fixture-backed adapters for offline demos and tests.

These adapters implement the same structural interfaces the live providers will
use in later phases. They never touch the network and read only from the fixture
root configured by :mod:`upset_profiler.fixtures`.
"""

from __future__ import annotations

from datetime import date

from upset_profiler.errors import IntegrationError
from upset_profiler.fixtures import load_json, load_model
from upset_profiler.models.games import DailyGame, DailySlate
from upset_profiler.models.monitoring import GameContext, GameFlag
from upset_profiler.models.research import SourceDocument


class FixtureSlateProvider:
    """Return a daily slate stored under ``tests/fixtures/slates``."""

    def get_slate(self, slate_date: date) -> DailySlate:
        """Load and validate the fixture slate for one date."""
        return load_model(DailySlate, "slates", f"{slate_date.isoformat()}.json")


class FixtureContextProvider:
    """Return deterministic pregame features and documents per fixture game."""

    def __init__(self) -> None:
        self._contexts = load_json("mcp", "game_contexts.json")

    def get_context(self, game: DailyGame) -> GameContext:
        """Build a validated game context from the fixture mapping."""
        raw = self._contexts.get(game.game_id) if isinstance(self._contexts, dict) else None
        if not isinstance(raw, dict):
            raise IntegrationError(
                "no fixture context for game",
                context={"game_id": game.game_id},
                retryable=False,
            )
        documents = [
            SourceDocument.model_validate(document)
            for document in raw.get("documents", [])
        ]
        return GameContext(
            game=game,
            features=raw.get("features", {}),
            documents=documents,
        )


class FixtureDecisionModel:
    """Deterministic stand-in for the ``d3-mini`` decision model."""

    model_name: str = "d3-mini"

    def score(self, flag: GameFlag) -> float:
        """Score a flag from the number of matched validated preconditions."""
        matched = sum(1 for signal in flag.signals if signal.matched)
        return min(0.99, 0.35 + 0.15 * matched)
