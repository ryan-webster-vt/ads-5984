# Offline test module: Secret redaction in logs, errors, and run metadata.
from __future__ import annotations

import logging

from src.redaction import (
    REDACTED,
    RedactingFilter,
    get_logger,
    redact_secrets,
    safe_exception_message,
)


# Verifies: redacts configured environment secret.
def test_redacts_configured_environment_secret(monkeypatch):
    monkeypatch.setenv("OPEN_AI_API_KEY", "super-secret-value-12345")
    text = redact_secrets("request failed with super-secret-value-12345")
    assert "super-secret-value-12345" not in text
    assert REDACTED in text


# Verifies: redacts common credential shapes.
def test_redacts_common_credential_shapes():
    text = redact_secrets(
        "Authorization: Bearer abcdefghijklmnop "
        "api_key=sk-1234567890abcdef "
        "url=https://user:password@example.test/path"
    )
    assert "abcdefghijklmnop" not in text
    assert "sk-1234567890abcdef" not in text
    assert "password" not in text
    assert text.count(REDACTED) >= 3


# Verifies: safe exception message is bounded and redacted.
def test_safe_exception_message_is_bounded_and_redacted(monkeypatch):
    monkeypatch.setenv("OPEN_AI_API_KEY", "another-secret-value")
    message = safe_exception_message(
        RuntimeError("another-secret-value " + "x" * 1000),
        limit=80,
    )
    assert "another-secret-value" not in message
    assert len(message) <= 80


# Verifies: redacting filter scrubs message arguments.
def test_redacting_filter_scrubs_message_arguments(monkeypatch):
    monkeypatch.setenv("OPEN_AI_API_KEY", "filter-secret-value")
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="failed with %s",
        args=("filter-secret-value",),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert "filter-secret-value" not in record.getMessage()


# Verifies: get logger attaches one filter.
def test_get_logger_attaches_one_filter():
    logger = get_logger("vt_team.test.redaction")
    get_logger("vt_team.test.redaction")
    filters = [item for item in logger.filters if isinstance(item, RedactingFilter)]
    assert len(filters) == 1
