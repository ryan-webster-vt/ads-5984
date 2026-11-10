"""Secret redaction for logs, errors, run metadata, and UI messages."""

from __future__ import annotations

import logging
import os
import re

REDACTED = "[REDACTED]"

# Values from these variables are scrubbed verbatim. Only names are listed here;
# values are read at call time and never persisted.
SECRET_ENV_NAMES = (
    "OPEN_AI_API_KEY",
    "ELSEVIER_API_KEY",
    "ELSEVIER_INSTTOKEN",
)

# Pattern-based fallbacks catch credentials that leak through error text even
# when they did not come from one of the known environment variables.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), REDACTED),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(x-els-apikey\s*[:=]\s*)[^\s,;]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)[^\s,;]+"), r"\1" + REDACTED),
    (
        re.compile(r"(?i)\b(https?://)([^/@\s:]+):([^/@\s]+)@"),
        r"\1" + REDACTED + ":" + REDACTED + "@",
    ),
)


def _environment_secrets() -> list[str]:
    # Longest-first replacement prevents a short secret from partially masking a
    # longer one that contains it.
    values = {
        value
        for name in SECRET_ENV_NAMES
        if (value := os.environ.get(name)) and len(value) >= 6
    }
    return sorted(values, key=len, reverse=True)


def redact_secrets(text: object, *, extra_secrets: tuple[str, ...] | list[str] = ()) -> str:
    """Replace configured and recognizable credentials with a stable marker."""
    result = "" if text is None else str(text)
    # Environment values are removed before regexes so exact matches win even if
    # the secret does not look like a standard API-key format.
    for secret in (*_environment_secrets(), *extra_secrets):
        if secret and len(secret) >= 4:
            result = result.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def safe_exception_message(exc: BaseException, *, limit: int = 500) -> str:
    """Return a redacted, bounded exception description suitable for storage."""
    message = redact_secrets(f"{type(exc).__name__}: {exc}")
    # Collapse whitespace and bound length so one failure cannot flood the UI or
    # run-metadata file.
    return " ".join(message.split())[:limit]


class RedactingFilter(logging.Filter):
    """Scrub secrets from formatted log messages before handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Filter both the format string and its arguments; either path can carry
        # a provider error containing a credential.
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_secrets(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def get_logger(name: str = "vt_team") -> logging.Logger:
    """Return a project logger with redaction attached exactly once."""
    logger = logging.getLogger(name)
    # Attach once per logger; repeated calls from imports would otherwise stack
    # filters and repeatedly scrub the same message.
    if not any(isinstance(item, RedactingFilter) for item in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
