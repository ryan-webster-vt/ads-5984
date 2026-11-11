"""Contract validation, serialization, and JSON-schema tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.factories import (
    make_citation,
    make_daily_game,
    make_daily_slate,
    make_effect_size,
    make_flag,
    make_historical_game,
    make_out_of_sample,
    make_score,
    make_signal_match,
    make_statistical_test,
)
from upset_profiler.config import PROJECT_ROOT
from upset_profiler.errors import NetworkError
from upset_profiler.models import (
    AgentError,
    BacktestReport,
    Citation,
    Comparison,
    DailySlate,
    EvaluationMetrics,
    GameFlag,
    MonitorAggregate,
    PreconditionDecision,
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    SignalMatch,
    StatisticalTest,
    TeamRef,
    UpsetRanking,
)
from upset_profiler.models.export import MODEL_REGISTRY, check_schemas
from upset_profiler.models.validation import CalibrationBin


@pytest.mark.unit
def test_strict_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TeamRef(team_id="1", name="Team", unexpected="value")  # type: ignore[call-arg]


@pytest.mark.unit
def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Citation(
            url="https://example.com/report",
            source_domain="example.com",
            retrieved_at=datetime(2025, 1, 1),
        )


@pytest.mark.unit
def test_timestamp_is_normalized_to_utc() -> None:
    citation = Citation(
        url="https://example.com/report",
        source_domain="example.com",
        retrieved_at=datetime(2025, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
    )
    assert citation.retrieved_at.tzinfo is UTC
    assert citation.retrieved_at.hour == 5


@pytest.mark.unit
def test_invalid_season_is_rejected() -> None:
    game = make_historical_game()
    payload = game.model_dump(mode="python")
    payload["season"] = "2024-2026"
    with pytest.raises(ValidationError, match="season"):
        type(game).model_validate(payload)


@pytest.mark.unit
def test_historical_result_must_match_winner() -> None:
    game = make_historical_game()
    payload = game.model_dump(mode="python")
    payload["winner_team_id"] = "away-1"
    with pytest.raises(ValidationError, match="winner_team_id"):
        type(game).model_validate(payload)


@pytest.mark.unit
def test_statistical_significance_must_match_p_value() -> None:
    with pytest.raises(ValidationError, match="significant"):
        StatisticalTest(
            test_name="chi_square",
            statistic=1.0,
            p_value=0.5,
            alpha=0.05,
            significant=True,
        )


@pytest.mark.unit
def test_accepted_decision_requires_complete_evidence() -> None:
    with pytest.raises(ValidationError, match="effect size"):
        PreconditionDecision(
            precondition_id="rest_disadvantage",
            accepted=True,
            justification="Missing evidence.",
        )

    with pytest.raises(ValidationError, match="citation"):
        PreconditionDecision(
            precondition_id="rest_disadvantage",
            accepted=True,
            justification="Missing citations.",
            effect_size=make_effect_size(),
            statistical_test=make_statistical_test(),
            out_of_sample=make_out_of_sample(),
            citations=[],
        )


@pytest.mark.unit
def test_rejected_decision_only_requires_justification() -> None:
    decision = PreconditionDecision(
        precondition_id="spurious_signal",
        accepted=False,
        justification="Failed multiple-testing correction.",
    )
    assert decision.accepted is False
    assert decision.citations == []


@pytest.mark.unit
def test_ranking_requires_consecutive_unique_ranks() -> None:
    with pytest.raises(ValidationError, match="consecutive"):
        UpsetRanking(
            run_id=uuid4(),
            model_name="d3-mini",
            scores=[make_score("a", rank=1), make_score("b", rank=3)],
        )


@pytest.mark.unit
def test_flag_requires_matched_signal_and_citations() -> None:
    unmatched = make_signal_match().model_copy(update={"matched": False})
    with pytest.raises(ValidationError, match="matched signal"):
        GameFlag(
            game_id="401000001",
            worker_id="worker-1",
            signals=[unmatched],
            rationale="No matched signal.",
            citations=[make_citation()],
        )

    with pytest.raises(ValidationError, match="citation"):
        SignalMatch(
            precondition_id="rest_disadvantage",
            matched=True,
            comparison=Comparison.GREATER_THAN_OR_EQUAL,
            threshold=2,
            explanation="Missing citation.",
        )


@pytest.mark.unit
def test_progress_event_rejects_impossible_counts() -> None:
    with pytest.raises(ValidationError, match="completed"):
        ProgressEvent(
            run_id=uuid4(),
            event_index=0,
            stage=ProgressStage.MONITOR,
            status=ProgressStatus.RUNNING,
            agent="monitor-worker",
            message="working",
            completed=3,
            total=2,
        )


@pytest.mark.unit
def test_backtest_season_splits_must_be_disjoint() -> None:
    metrics = EvaluationMetrics(
        log_loss=0.5,
        brier_score=0.2,
        sample_size=100,
        positive_rate=0.3,
    )
    with pytest.raises(ValidationError, match="disjoint"):
        BacktestReport(
            run_id=uuid4(),
            model_name="d3-mini",
            train_seasons=["2024-2025"],
            validation_season="2024-2025",
            test_season="2025-2026",
            validation_metrics=metrics,
            test_metrics=metrics,
        )


@pytest.mark.unit
def test_calibration_bin_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError, match="lower bound"):
        CalibrationBin(
            lower=0.5,
            upper=0.5,
            count=10,
            mean_predicted=0.5,
            observed_upset_rate=0.4,
        )


@pytest.mark.unit
def test_contract_round_trip_preserves_payloads() -> None:
    slate = make_daily_slate()
    assert DailySlate.model_validate(slate.model_dump(mode="json")) == slate

    aggregate = MonitorAggregate(
        run_id=uuid4(),
        flags=[make_flag("401000001")],
        evaluated_game_ids=["401000001", "401000002"],
    )
    assert MonitorAggregate.model_validate(aggregate.model_dump(mode="json")) == aggregate

    ranking = UpsetRanking(
        run_id=uuid4(),
        model_name="d3-mini",
        scores=[make_score("401000001", rank=1), make_score("401000002", rank=2)],
    )
    assert UpsetRanking.model_validate(ranking.model_dump(mode="json")) == ranking


@pytest.mark.unit
def test_schema_version_defaults_and_rejects_invalid_values() -> None:
    game = make_daily_game()
    assert game.schema_version == 1
    payload = game.model_dump(mode="python")
    payload["schema_version"] = 0
    with pytest.raises(ValidationError, match="schema_version"):
        type(game).model_validate(payload)


@pytest.mark.unit
def test_agent_error_from_exception_carries_retryability() -> None:
    error = NetworkError("offline", context={"game_id": "401"})
    record = AgentError.from_exception(error, agent="historical-stats", stage="research")
    assert record.retryable is True
    assert record.context == {"game_id": "401"}
    assert record.error_type == "NetworkError"


@pytest.mark.unit
def test_checked_in_schemas_are_current() -> None:
    assert check_schemas(PROJECT_ROOT / "docs" / "contracts") == []


@pytest.mark.unit
def test_every_registered_schema_is_serializable() -> None:
    assert len(MODEL_REGISTRY) >= 40
    for model in MODEL_REGISTRY.values():
        schema = model.model_json_schema()
        assert schema["type"] == "object"
