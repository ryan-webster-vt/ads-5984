"""Tests for the deterministic fixture-backed Phase 2 adapters."""

from __future__ import annotations

import pytest

from tests.factories import FIXED_DATE, make_daily_game, make_flag, make_signal_match
from upset_profiler.errors import IntegrationError
from upset_profiler.fixtures import load_json
from upset_profiler.integrations.fakes import (
    FixtureContextProvider,
    FixtureDecisionModel,
    FixtureSlateProvider,
)
from upset_profiler.models.preconditions import ValidatedPrecondition


@pytest.mark.unit
def test_fixture_slate_provider_loads_valid_slate() -> None:
    slate = FixtureSlateProvider().get_slate(FIXED_DATE)
    assert slate.slate_date == FIXED_DATE
    assert [game.game_id for game in slate.games] == [
        "401000001",
        "401000002",
        "401000003",
        "401000004",
    ]


@pytest.mark.unit
def test_fixture_context_provider_builds_features_and_documents() -> None:
    game = make_daily_game("401000001")
    context = FixtureContextProvider().get_context(game)
    assert context.features["rest_days_difference"] == 3
    assert context.features["underdog_back_to_back"] is True
    assert context.documents[0].document_id == "doc-401000001-rest"


@pytest.mark.unit
def test_fixture_context_provider_rejects_unknown_game() -> None:
    game = make_daily_game("999999999")
    with pytest.raises(IntegrationError, match="no fixture context"):
        FixtureContextProvider().get_context(game)


@pytest.mark.unit
def test_fixture_decision_model_is_deterministic_and_bounded() -> None:
    model = FixtureDecisionModel()
    one_match = make_flag()
    many = one_match.model_copy(
        update={
            "signals": [
                make_signal_match("rest_disadvantage"),
                make_signal_match("back_to_back"),
            ]
        }
    )
    assert 0.0 <= model.score(one_match) <= 1.0
    assert model.score(many) > model.score(one_match)
    assert model.score(many) == model.score(many)


@pytest.mark.unit
def test_fixture_validated_preconditions_are_contract_valid() -> None:
    payloads = load_json("historical", "validated_preconditions.json")
    preconditions = [ValidatedPrecondition.model_validate(item) for item in payloads]
    assert {item.precondition_id for item in preconditions} == {
        "rest_disadvantage",
        "back_to_back",
        "travel_distance",
    }
