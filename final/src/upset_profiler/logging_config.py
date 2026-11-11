"""Structured logging setup with secret redaction.

The application prefers small, dependency-free logging helpers over a custom
framework. Two protections are layered on top of the standard library:

* :class:`SecretRedactionFilter` removes known secret values from log records.
* :class:`PatternRedactionFilter` catches common credential-shaped strings such
  as ``api_key=...`` or ``Bearer ...`` that were not part of the settings object.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import BaseModel, SecretStr

from upset_profiler.config import Settings, get_settings

_REDACTION_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s&,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
)

_MASK = "**********"


def _iter_secret_values(model: BaseModel) -> Iterable[str]:
    """Yield every non-empty secret value contained in a settings model."""
    for value in model.__dict__.values():
        if isinstance(value, BaseModel):
            yield from _iter_secret_values(value)
        elif isinstance(value, SecretStr):
            secret = value.get_secret_value()
            if secret:
                yield secret
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, SecretStr) and item.get_secret_value():
                    yield item.get_secret_value()


class SecretRedactionFilter(logging.Filter):
    """Replace known secret values with a fixed mask."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, _MASK)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class PatternRedactionFilter(logging.Filter):
    """Scrub credential-shaped substrings from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern in _REDACTION_PATTERNS:
            redacted = pattern.sub(_MASK, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = str(run_id)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def setup_logging(
    settings: Settings | None = None,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    """Configure the root logger once.

    Args:
        settings: Application settings; defaults to the cached settings.
        stream: Destination stream; defaults to ``sys.stderr``.
        force: Remove existing handlers before configuring.
    """
    resolved = settings or get_settings()
    root = logging.getLogger()
    if root.handlers and not force:
        root.setLevel(resolved.logging.log_level)
        return

    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    formatter = JsonFormatter() if resolved.logging.log_format == "json" else ConsoleFormatter()
    handler.setFormatter(formatter)
    handler.addFilter(PatternRedactionFilter())
    handler.addFilter(SecretRedactionFilter(_iter_secret_values(resolved)))

    root.setLevel(resolved.logging.log_level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)
