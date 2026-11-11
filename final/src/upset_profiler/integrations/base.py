"""Shared HTTP client base with timeouts, retries, rate limiting, and redaction.

Provider adapters in later phases construct this client through
:func:`create_http_client` and call :meth:`BaseHttpClient.get_json`,
:meth:`BaseHttpClient.post_json`, or :meth:`BaseHttpClient.get_text`.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from upset_profiler.errors import (
    IntegrationError,
    NetworkError,
    ProviderTimeoutError,
    RateLimitError,
    UpsetProfilerError,
)

_REDACTION_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s&,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
)
_MASK = "**********"


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings for provider requests."""

    max_attempts: int = 3
    initial_wait_seconds: float = 0.25
    max_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_wait_seconds < 0:
            raise ValueError("initial_wait_seconds must be non-negative")
        if self.max_wait_seconds < self.initial_wait_seconds:
            raise ValueError("max_wait_seconds must be at least initial_wait_seconds")


@dataclass(frozen=True)
class HttpClientConfig:
    """Configuration for a provider HTTP client."""

    base_url: str = ""
    timeout_seconds: float = 30.0
    connect_timeout_seconds: float | None = None
    follow_redirects: bool = True
    user_agent: str = "upset-profiler/0.1"
    headers: Mapping[str, str] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.connect_timeout_seconds is not None and self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive when provided")


class RateLimiter:
    """Thread-safe sliding-window rate limiter."""

    def __init__(
        self,
        *,
        max_calls: int,
        period_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if period_seconds < 0:
            raise ValueError("period_seconds must be non-negative")
        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a call is permitted, then record it."""
        if self._period_seconds == 0:
            return
        with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self._period_seconds:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                wait_seconds = self._period_seconds - (now - self._calls[0])
                if wait_seconds > 0:
                    self._sleep(wait_seconds)


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove known secrets and credential-shaped substrings from text."""
    redacted = text
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, _MASK)
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(_MASK, redacted)
    return redacted


def _safe_url(url: httpx.URL | str) -> str:
    return str(url).split("?", maxsplit=1)[0]


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, UpsetProfilerError) and exc.retryable


class BaseHttpClient:
    """Synchronous HTTP client with shared reliability behavior."""

    def __init__(
        self,
        config: HttpClientConfig,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._rate_limiter = rate_limiter
        self._sleep = sleep
        self._owns_client = client is None
        headers = {"User-Agent": config.user_agent, **dict(config.headers)}
        timeout = httpx.Timeout(
            config.timeout_seconds,
            connect=config.connect_timeout_seconds or config.timeout_seconds,
        )
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=timeout,
            headers=headers,
            follow_redirects=config.follow_redirects,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying client when this object created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BaseHttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        """Send a request, retrying retryable failures."""
        retryer = Retrying(
            stop=stop_after_attempt(self._config.retry.max_attempts),
            wait=wait_exponential(
                multiplier=self._config.retry.initial_wait_seconds,
                max=self._config.retry.max_wait_seconds,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
            sleep=self._sleep,
        )

        def _attempt() -> httpx.Response:
            if self._rate_limiter is not None:
                self._rate_limiter.acquire()
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json,
                )
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    "provider request timed out",
                    context={"method": method.upper(), "url": _safe_url(url)},
                ) from exc
            except httpx.TransportError as exc:
                raise NetworkError(
                    "provider request failed",
                    context={"method": method.upper(), "url": _safe_url(url)},
                ) from exc
            return self._validate_response(response, method=method, url=url)

        return retryer(_attempt)

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send a GET request and decode a JSON response."""
        response = self.request("GET", url, params=params, headers=headers)
        return self._decode_json(response)

    def post_json(
        self,
        url: str,
        *,
        json: Any,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send a POST request with a JSON body and decode the response."""
        response = self.request("POST", url, params=params, headers=headers, json=json)
        return self._decode_json(response)

    def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Send a GET request and return text."""
        response = self.request("GET", url, params=params, headers=headers)
        return response.text

    def _validate_response(
        self,
        response: httpx.Response,
        *,
        method: str,
        url: str,
    ) -> httpx.Response:
        context = {
            "method": method.upper(),
            "url": _safe_url(url),
            "status": response.status_code,
        }
        if response.status_code == 429:
            raise RateLimitError("provider rate limit exceeded", context=context)
        if response.status_code >= 500:
            raise NetworkError("provider server error", context=context)
        if response.status_code >= 400:
            raise IntegrationError("provider rejected request", context=context, retryable=False)
        return response

    def _decode_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError(
                "provider returned invalid JSON",
                context={"url": _safe_url(response.url), "status": response.status_code},
                retryable=False,
            ) from exc


def create_http_client(
    config: HttpClientConfig,
    *,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> BaseHttpClient:
    """Build the shared client used by provider adapters."""
    return BaseHttpClient(
        config,
        client=client,
        transport=transport,
        rate_limiter=rate_limiter,
        sleep=sleep,
    )
