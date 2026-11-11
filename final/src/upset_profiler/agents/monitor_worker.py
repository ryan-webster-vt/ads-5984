"""Monitor worker: deterministic per-game evaluation against preconditions."""

from __future__ import annotations

from typing import Protocol

from upset_profiler.agents.base import BaseAgent
from upset_profiler.errors import AgentError as AgentErrorException
from upset_profiler.models.common import AgentError, Citation, ThresholdValue
from upset_profiler.models.games import DailyGame
from upset_profiler.models.monitoring import (
    GameContext,
    GameFlag,
    MonitorAssignment,
    MonitorWorkerResult,
    SignalMatch,
)
from upset_profiler.models.preconditions import Comparison, ValidatedPrecondition
from upset_profiler.orchestration.progress import ProgressReporter


class GameContextProvider(Protocol):
    """Provider of current matchup features and cited evidence for one game."""

    def get_context(self, game: DailyGame) -> GameContext:
        """Return the validated context for one game."""
        ...


def _as_number(value: ThresholdValue) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"comparison requires numeric values, got {type(value).__name__}")


def evaluate_comparison(
    observed: ThresholdValue,
    comparison: Comparison,
    threshold: ThresholdValue,
) -> bool:
    """Evaluate one deterministic precondition comparison."""
    if comparison is Comparison.EQUALS:
        return observed == threshold
    if comparison is Comparison.NOT_EQUALS:
        return observed != threshold
    if comparison in (Comparison.IN, Comparison.NOT_IN):
        raise ValueError("in/not_in comparisons are not supported by the current contract")

    left = _as_number(observed)
    right = _as_number(threshold)
    if comparison is Comparison.GREATER_THAN:
        return left > right
    if comparison is Comparison.GREATER_THAN_OR_EQUAL:
        return left >= right
    if comparison is Comparison.LESS_THAN:
        return left < right
    if comparison is Comparison.LESS_THAN_OR_EQUAL:
        return left <= right
    raise ValueError(f"unsupported comparison {comparison!r}")


def _context_citations(context: GameContext) -> list[Citation]:
    citations: list[Citation] = []
    for document in context.documents:
        citations.append(
            Citation(
                citation_id=document.document_id,
                title=document.title,
                url=document.url,
                source_domain=document.source_domain,
                published_at=document.published_at,
                retrieved_at=document.retrieved_at,
                quote=document.content[:240] if document.content else None,
            )
        )
    return citations


def _unique_citations(citations: list[Citation]) -> list[Citation]:
    unique: list[Citation] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for citation in citations:
        key = (
            str(citation.url),
            citation.title,
            citation.citation_id,
            citation.quote,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(citation)
    return unique


class MonitorWorker(BaseAgent):
    """Evaluate an assigned game batch against validated preconditions."""

    name = "monitor-worker"

    def __init__(
        self,
        *,
        worker_id: str,
        context_provider: GameContextProvider,
        progress: ProgressReporter | None = None,
    ) -> None:
        super().__init__(progress=progress)
        self.worker_id = worker_id
        self._context_provider = context_provider

    def evaluate(self, assignment: MonitorAssignment) -> MonitorWorkerResult:
        """Evaluate every assigned game and return flags plus failures."""
        flags: list[GameFlag] = []
        evaluated: list[str] = []
        failures: list[AgentError] = []

        for game in assignment.games:
            self._report(
                f"{self.worker_id} evaluating {game.game_id}",
                agent=self.worker_id,
                game_id=game.game_id,
            )
            try:
                context = self._context_provider.get_context(game)
            except Exception as exc:
                failures.append(
                    AgentError.from_exception(
                        exc,
                        agent=self.worker_id,
                        stage="monitor",
                        context={"game_id": game.game_id},
                    )
                )
                self._advance(
                    f"{self.worker_id} failed {game.game_id}",
                    agent=self.worker_id,
                    game_id=game.game_id,
                )
                continue

            signals = self._evaluate_preconditions(
                game_id=game.game_id,
                preconditions=assignment.preconditions,
                context=context,
                failures=failures,
            )
            matched = [signal for signal in signals if signal.matched]
            if matched:
                citations = _unique_citations(
                    [
                        citation
                        for signal in matched
                        for citation in signal.citations
                    ]
                    + _context_citations(context)
                )
                rationale = (
                    "Matched validated preconditions: "
                    + "; ".join(signal.explanation for signal in matched)
                    + "."
                )
                flags.append(
                    GameFlag(
                        game_id=game.game_id,
                        worker_id=self.worker_id,
                        signals=signals,
                        rationale=rationale,
                        citations=citations,
                    )
                )

            evaluated.append(game.game_id)
            self._advance(
                f"{self.worker_id} evaluated {game.game_id}",
                agent=self.worker_id,
                game_id=game.game_id,
            )

        return MonitorWorkerResult(
            run_id=assignment.run_id,
            worker_id=self.worker_id,
            flags=flags,
            evaluated_game_ids=evaluated,
            failures=failures,
        )

    def _evaluate_preconditions(
        self,
        *,
        game_id: str,
        preconditions: list[ValidatedPrecondition],
        context: GameContext,
        failures: list[AgentError],
    ) -> list[SignalMatch]:
        signals: list[SignalMatch] = []
        for precondition in preconditions:
            candidate = precondition.candidate
            if candidate.feature not in context.features:
                signals.append(
                    SignalMatch(
                        precondition_id=precondition.precondition_id,
                        matched=False,
                        comparison=candidate.comparison,
                        threshold=candidate.threshold,
                        observed_value=None,
                        explanation=(
                            f"{candidate.name}: no observed value for "
                            f"{candidate.feature}"
                        ),
                    )
                )
                continue

            observed = context.features[candidate.feature]
            try:
                matched = evaluate_comparison(
                    observed,
                    candidate.comparison,
                    candidate.threshold,
                )
            except (TypeError, ValueError) as exc:
                failures.append(
                    AgentError.from_exception(
                        AgentErrorException(
                            str(exc),
                            context={
                                "game_id": game_id,
                                "precondition_id": precondition.precondition_id,
                            },
                        ),
                        agent=self.worker_id,
                        stage="monitor",
                    )
                )
                continue

            signals.append(
                SignalMatch(
                    precondition_id=precondition.precondition_id,
                    matched=matched,
                    comparison=candidate.comparison,
                    threshold=candidate.threshold,
                    observed_value=observed,
                    explanation=(
                        f"{candidate.name}: observed {candidate.feature}="
                        f"{observed} {candidate.comparison.value} "
                        f"{candidate.threshold}"
                    ),
                    citations=list(precondition.citations) if matched else [],
                )
            )
        return signals
