"""Domain exception hierarchy for the Upset Condition Profiler.

Every exception carries a ``retryable`` flag and an optional ``context`` mapping
so orchestration code can decide whether to retry, quarantine, or fail fast.
"""

from __future__ import annotations

from typing import Any


class UpsetProfilerError(Exception):
    """Base class for all application-specific errors."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context or {})
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:
        if not self.context:
            return self.message
        details = ", ".join(f"{key}={value!r}" for key, value in sorted(self.context.items()))
        return f"{self.message} ({details})"


class ConfigurationError(UpsetProfilerError):
    """Raised when required configuration is missing or invalid."""


class ContractValidationError(UpsetProfilerError):
    """Raised when an agent handoff or stored record fails schema validation."""


class AgentError(UpsetProfilerError):
    """Raised when an agent cannot complete its assigned work."""


class PipelineError(UpsetProfilerError):
    """Raised when a pipeline stage cannot continue."""


class IntegrationError(UpsetProfilerError):
    """Base class for provider and tool integration failures."""

    retryable = True


class NetworkError(IntegrationError):
    """Raised when a remote service is unreachable."""


class ProviderTimeoutError(IntegrationError):
    """Raised when a remote service exceeds its timeout budget."""


class RateLimitError(IntegrationError):
    """Raised when a provider rejects a request due to rate limiting."""


class AuthenticationError(IntegrationError):
    """Raised when credentials are missing or rejected."""

    retryable = False


class StorageError(UpsetProfilerError):
    """Raised for SQLite or vector-store failures."""


class LeakageError(UpsetProfilerError):
    """Raised when post-tip-off information would enter a pipeline."""


class CitationError(UpsetProfilerError):
    """Raised when a claim lacks verifiable supporting citations."""
