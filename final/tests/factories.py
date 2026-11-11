"""Factory helpers that build valid contracts for tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from upset_profiler.models.common import Citation, TeamRef
from upset_profiler.models.games import DailyGame, DailySlate, GameOdds, HistoricalGame
from upset_profiler.models.monitoring import (
    GameFlag,
    MonitorAggregate,
    MonitorRequest,
    SignalMatch,
)
from upset_profiler.models.preconditions import (
    CandidatePrecondition,
    Comparison,
    EffectSize,
    OutOfSampleResult,
    PreconditionDecision,
    StatisticalTest,
    ValidatedPrecondition,
)
from upset_profiler.models.scoring import UpsetScore

FIXED_TIME = datetime(2025, 1, 15, 18, 30, tzinfo=UTC)
FIXED_DATE = date(2025, 1, 15)


def make_team(team_id: str = "home-1", name: str = "Home Team") -> TeamRef:
    return TeamRef(team_id=team_id, name=name, conference="ACC")


def make_citation(url: str = "https://example.com/report") -> Citation:
    return Citation(
        url=url,
        title="Pregame report",
        source_domain="example.com",
        published_at=FIXED_TIME,
        retrieved_at=FIXED_TIME,
    )


def make_odds() -> GameOdds:
    return GameOdds(
        favorite_team_id="home-1",
        underdog_team_id="away-1",
        favorite_moneyline=-220,
        underdog_moneyline=180,
        spread=-5.5,
        total=145.5,
        captured_at=FIXED_TIME,
    )


def make_historical_game(game_id: str = "401000001") -> HistoricalGame:
    return HistoricalGame(
        game_id=game_id,
        season="2024-2025",
        game_date=FIXED_DATE,
        tipoff_at=FIXED_TIME,
        home_team=make_team("home-1", "Home Team"),
        away_team=make_team("away-1", "Away Team"),
        home_score=80,
        away_score=70,
        winner_team_id="home-1",
        odds=make_odds(),
    )


def make_daily_game(game_id: str = "401000001") -> DailyGame:
    return DailyGame(
        game_id=game_id,
        slate_date=FIXED_DATE,
        tipoff_at=FIXED_TIME,
        home_team=make_team("home-1", "Home Team"),
        away_team=make_team("away-1", "Away Team"),
        odds=make_odds(),
    )


def make_daily_slate() -> DailySlate:
    return DailySlate(
        slate_date=FIXED_DATE,
        games=[make_daily_game("401000001"), make_daily_game("401000002")],
        retrieved_at=FIXED_TIME,
    )


def make_candidate(precondition_id: str = "rest_disadvantage") -> CandidatePrecondition:
    return CandidatePrecondition(
        precondition_id=precondition_id,
        name="Rest disadvantage",
        description="The underdog has more rest than the favorite.",
        feature="rest_days_difference",
        comparison=Comparison.GREATER_THAN_OR_EQUAL,
        threshold=2,
        tags=["schedule", "fatigue"],
        rationale="Uneven rest can compound fatigue.",
    )


def make_effect_size() -> EffectSize:
    return EffectSize(
        metric="risk_difference",
        value=0.2,
        ci_lower=0.05,
        ci_upper=0.35,
        method="bootstrap",
        treatment_sample_size=40,
        control_sample_size=60,
    )


def make_statistical_test() -> StatisticalTest:
    return StatisticalTest(
        test_name="chi_square",
        statistic=6.4,
        p_value=0.01,
        alpha=0.05,
        significant=True,
    )


def make_out_of_sample() -> OutOfSampleResult:
    return OutOfSampleResult(
        train_seasons=["2021-2022", "2022-2023", "2023-2024"],
        validation_season="2024-2025",
        metric_name="log_loss",
        value=0.61,
        baseline_value=0.65,
        passed=True,
    )


def make_validated_precondition(
    precondition_id: str = "rest_disadvantage",
) -> ValidatedPrecondition:
    return ValidatedPrecondition(
        precondition_id=precondition_id,
        candidate=make_candidate(precondition_id),
        effect_size=make_effect_size(),
        statistical_test=make_statistical_test(),
        out_of_sample=make_out_of_sample(),
        citations=[make_citation()],
        justification="Signal is significant and passes out-of-sample validation.",
    )


def make_accepted_decision(
    precondition_id: str = "rest_disadvantage",
) -> PreconditionDecision:
    return PreconditionDecision(
        precondition_id=precondition_id,
        accepted=True,
        justification="Signal is significant and passes out-of-sample validation.",
        effect_size=make_effect_size(),
        statistical_test=make_statistical_test(),
        out_of_sample=make_out_of_sample(),
        citations=[make_citation()],
    )


def make_signal_match(precondition_id: str = "rest_disadvantage") -> SignalMatch:
    return SignalMatch(
        precondition_id=precondition_id,
        matched=True,
        comparison=Comparison.GREATER_THAN_OR_EQUAL,
        threshold=2,
        observed_value=3,
        explanation="Underdog has three more rest days.",
        citations=[make_citation()],
    )


def make_flag(game_id: str = "401000001") -> GameFlag:
    return GameFlag(
        game_id=game_id,
        worker_id="monitor-worker-1",
        signals=[make_signal_match()],
        rationale="Rest disadvantage matches a validated precondition.",
        citations=[make_citation()],
    )


def make_monitor_request(run_id: UUID) -> MonitorRequest:
    return MonitorRequest(
        run_id=run_id,
        slate_date=FIXED_DATE,
        games=[make_daily_game("401000001"), make_daily_game("401000002")],
    )


def make_monitor_aggregate(run_id: UUID) -> MonitorAggregate:
    return MonitorAggregate(
        run_id=run_id,
        flags=[make_flag("401000001")],
        evaluated_game_ids=["401000001", "401000002"],
    )


def make_score(
    game_id: str = "401000001",
    rank: int = 1,
    score: float = 0.72,
    model_name: str = "d3-mini",
) -> UpsetScore:
    return UpsetScore(
        game_id=game_id,
        score=score,
        rank=rank,
        model_name=model_name,
        rationale="Multiple validated signals align for this game.",
        citations=[make_citation()],
    )
