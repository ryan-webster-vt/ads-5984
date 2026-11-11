"""Tests for the domain exception hierarchy."""

from __future__ import annotations

import pytest

from upset_profiler.errors import (
    AuthenticationError,
    ConfigurationError,
    IntegrationError,
    NetworkError,
    UpsetProfilerError,
)


@pytest.mark.unit
def test_base_error_is_not_retryable() -> None:
    assert UpsetProfilerError("boom").retryable is False


@pytest.mark.unit
def test_integration_errors_are_retryable_by_default() -> None:
    assert IntegrationError("temporary").retryable is True
    assert NetworkError("offline").retryable is True


@pytest.mark.unit
def test_authentication_error_is_not_retryable() -> None:
    assert AuthenticationError("bad key").retryable is False


@pytest.mark.unit
def test_retryable_can_be_overridden() -> None:
    error = ConfigurationError("bad config", retryable=True)
    assert error.retryable is True


@pytest.mark.unit
def test_context_is_rendered_in_message() -> None:
    error = UpsetProfilerError("failed", context={"game_id": "401", "stage": "monitor"})
    rendered = str(error)
    assert "failed" in rendered
    assert "game_id='401'" in rendered
    assert "stage='monitor'" in rendered
