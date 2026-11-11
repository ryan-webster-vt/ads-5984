"""Monitor orchestrator: precondition loading, partitioning, and aggregation."""

from __future__ import annotations

from functools import partial
from typing import Protocol

from upset_profiler.agents.base import BaseAgent
from upset_profiler.agents.monitor_worker import GameContextProvider, MonitorWorker
from upset_profiler.models.common import AgentError
from upset_profiler.models.games import DailyGame
from upset_profiler.models.monitoring import (
    GameFlag,
    MonitorAggregate,
    MonitorAssignment,
    MonitorRequest,
)
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.models.progress import ProgressStatus
from upset_profiler.orchestration.progress import ProgressReporter
from upset_profiler.orchestration.runtime import run_parallel


class PreconditionSource(Protocol):
    """Source of validated preconditions, normally the SQLite repository."""

    def list_all(self) -> list[ValidatedPrecondition]:
        """Return every validated precondition."""
        ...


def _worker_id(index: int) -> str:
    return f"monitor-worker-{index + 1}"


def partition_games(
    games: list[DailyGame],
    worker_count: int,
) -> list[tuple[str, list[DailyGame]]]:
    """Partition games round-robin into deterministic worker batches."""
    if worker_count < 1:
        raise ValueError("worker_count must be at least 1")
    buckets: list[list[DailyGame]] = [[] for _ in range(worker_count)]
    for index, game in enumerate(games):
        buckets[index % worker_count].append(game)
    return [
        (_worker_id(index), bucket)
        for index, bucket in enumerate(buckets)
        if bucket
    ]


class MonitorOrchestrator(BaseAgent):
    """Load preconditions, run Monitor workers in parallel, and aggregate."""

    name = "monitor-orchestrator"

    def __init__(
        self,
        *,
        context_provider: GameContextProvider,
        precondition_source: PreconditionSource,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        self._context_provider = context_provider
        self._precondition_source = precondition_source

    def run(self, request: MonitorRequest) -> MonitorAggregate:
        """Evaluate every requested game and return a deduplicated aggregate."""
        preconditions = self._precondition_source.list_all()
        assignments = [
            MonitorAssignment(
                run_id=request.run_id,
                worker_id=worker_id,
                games=games,
                preconditions=preconditions,
            )
            for worker_id, games in partition_games(
                request.games,
                request.worker_count,
            )
        ]

        self._report(
            f"monitoring {len(request.games)} games with {len(assignments)} workers",
            status=ProgressStatus.STARTED,
            completed=0,
            total=len(request.games),
        )

        workers = {
            assignment.worker_id: MonitorWorker(
                worker_id=assignment.worker_id,
                context_provider=self._context_provider,
                progress=self.progress,
            )
            for assignment in assignments
        }
        outcomes = run_parallel(
            {
                assignment.worker_id: partial(
                    workers[assignment.worker_id].evaluate,
                    assignment,
                )
                for assignment in assignments
            },
            max_workers=max(1, len(assignments)),
        )

        flags: list[GameFlag] = []
        evaluated_ids: set[str] = set()
        failures: list[AgentError] = []
        for outcome in outcomes:
            if outcome.error is not None:
                failures.append(
                    AgentError.from_exception(
                        outcome.error,
                        agent=outcome.key,
                        stage="monitor",
                        context={"worker_id": outcome.key},
                    )
                )
                continue
            if outcome.value is None:
                continue
            flags.extend(outcome.value.flags)
            evaluated_ids.update(outcome.value.evaluated_game_ids)
            failures.extend(outcome.value.failures)

        flags_by_game = {flag.game_id: flag for flag in flags}
        aggregate = MonitorAggregate(
            run_id=request.run_id,
            flags=[flags_by_game[game_id] for game_id in sorted(flags_by_game)],
            evaluated_game_ids=[
                game.game_id
                for game in request.games
                if game.game_id in evaluated_ids
            ],
            failures=failures,
        )
        self._report(
            f"monitoring complete: {len(aggregate.flags)} flags, "
            f"{len(aggregate.failures)} failures",
            status=ProgressStatus.COMPLETED,
            completed=len(request.games),
            total=len(request.games),
        )
        return aggregate
