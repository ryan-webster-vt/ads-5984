"""Unit tests for the shared HTTP client base."""

from __future__ import annotations

import httpx
import pytest
import respx

from upset_profiler.errors import (
    IntegrationError,
    ProviderTimeoutError,
    RateLimitError,
)
from upset_profiler.integrations import (
    BaseHttpClient,
    HttpClientConfig,
    RateLimiter,
    RetryPolicy,
    create_http_client,
    redact_text,
)


def _client(*, max_attempts: int = 1) -> BaseHttpClient:
    return create_http_client(
        HttpClientConfig(
            base_url="https://api.example.com",
            retry=RetryPolicy(
                max_attempts=max_attempts,
                initial_wait_seconds=0.0,
                max_wait_seconds=0.0,
            ),
        ),
        sleep=lambda _: None,
    )


@pytest.mark.unit
def test_get_json_success() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = _client()
        assert client.get_json("/data") == {"ok": True}


@pytest.mark.unit
def test_retry_on_server_error_then_success() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(503, json={"error": "unavailable"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = _client(max_attempts=2)
        assert client.get_json("/data") == {"ok": True}
        assert route.call_count == 2


@pytest.mark.unit
def test_timeout_maps_to_provider_timeout() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.example.com/data").mock(side_effect=httpx.ReadTimeout("slow"))
        client = _client()
        with pytest.raises(ProviderTimeoutError) as exc_info:
            client.get_json("/data")
        assert exc_info.value.retryable is True


@pytest.mark.unit
def test_client_error_is_not_retried() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.example.com/data").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )
        client = _client(max_attempts=3)
        with pytest.raises(IntegrationError) as exc_info:
            client.get_json("/data")
        assert exc_info.value.retryable is False
        assert route.call_count == 1


@pytest.mark.unit
def test_rate_limit_is_retried() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(429, json={"error": "slow down"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = _client(max_attempts=2)
        assert client.get_json("/data") == {"ok": True}
        assert route.call_count == 2


@pytest.mark.unit
def test_invalid_json_is_not_retried() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, text="not json")
        )
        client = _client(max_attempts=3)
        with pytest.raises(IntegrationError, match="invalid JSON"):
            client.get_json("/data")


@pytest.mark.unit
def test_rate_limit_error_is_retryable() -> None:
    assert RateLimitError("slow down").retryable is True


@pytest.mark.unit
def test_rate_limiter_waits_for_window() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(max_calls=2, period_seconds=1.0, clock=clock, sleep=sleep)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert sleeps == [1.0]


@pytest.mark.unit
def test_rate_limiter_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0, period_seconds=1.0)
    with pytest.raises(ValueError):
        RateLimiter(max_calls=1, period_seconds=-1.0)


@pytest.mark.unit
def test_redact_text_removes_secrets_and_patterns() -> None:
    redacted = redact_text("api_key=abc Bearer xyz", ("abc",))
    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert redacted.count("**********") >= 2


@pytest.mark.unit
def test_retry_policy_validation() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(initial_wait_seconds=2.0, max_wait_seconds=1.0)
