"""Historical-Stats worker: structured box scores for assigned game ids."""

from __future__ import annotations

from upset_profiler.agents.base import BaseAgent
from upset_profiler.errors import AgentError
from upset_profiler.integrations.college_basketball import CollegeBasketballProvider
from upset_profiler.models.common import AgentError as AgentErrorRecord
from upset_profiler.models.progress import ProgressStatus
from upset_profiler.models.research import HistoricalStatsResult, ResearchBatch
from upset_profiler.orchestration.progress import ProgressReporter


class HistoricalStatsAgent(BaseAgent):
    """Retrieve and validate historical game statistics for one batch."""

    name = "historical-stats"

    def __init__(
        self,
        provider: CollegeBasketballProvider,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        self._provider = provider

    def run(self, batch: ResearchBatch) -> HistoricalStatsResult:
        """Fetch every game in the batch, isolating per-game failures."""
        if batch.season is None:
            raise AgentError(
                "historical stats batch requires a season",
                context={"batch_id": batch.batch_id},
            )
        games = []
        errors: list[AgentErrorRecord] = []
        self._report(
            f"fetching historical stats for {len(batch.game_ids)} games",
            status=ProgressStatus.STARTED,
            total=len(batch.game_ids),
        )
        for index, game_id in enumerate(batch.game_ids, start=1):
            try:
                game = self._provider.get_game(game_id, batch.season)
            except Exception as exc:
                errors.append(
                    AgentErrorRecord.from_exception(
                        exc,
                        agent=self.name,
                        stage="research",
                        context={"game_id": game_id, "batch_id": batch.batch_id},
                    )
                )
            else:
                games.append(game)
            self._report(
                f"processed historical stats for game {game_id}",
                completed=index,
                total=len(batch.game_ids),
                game_id=game_id,
            )
        self._report(
            f"historical stats complete: {len(games)} games, {len(errors)} errors",
            status=ProgressStatus.COMPLETED,
            completed=len(batch.game_ids),
            total=len(batch.game_ids),
        )
        return HistoricalStatsResult(
            run_id=batch.run_id,
            batch_id=batch.batch_id,
            games=games,
            errors=errors,
        )
