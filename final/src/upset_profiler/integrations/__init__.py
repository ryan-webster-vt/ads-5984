"""External provider and model adapters.

Phase 1 provides the shared HTTP client base; concrete provider adapters land in
later phases.
"""

from __future__ import annotations

from upset_profiler.integrations.base import (
    BaseHttpClient,
    HttpClientConfig,
    RateLimiter,
    RetryPolicy,
    create_http_client,
    redact_text,
)

__all__ = [
    "BaseHttpClient",
    "HttpClientConfig",
    "RateLimiter",
    "RetryPolicy",
    "create_http_client",
    "redact_text",
]
