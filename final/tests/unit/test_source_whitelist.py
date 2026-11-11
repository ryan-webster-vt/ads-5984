"""Tests for deterministic source whitelisting and URL canonicalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from upset_profiler.errors import ConfigurationError
from upset_profiler.security.source_whitelist import (
    SourcePolicy,
    SourceWhitelist,
    canonicalize_url,
    domain_from_url,
)


def _whitelist() -> SourceWhitelist:
    return SourceWhitelist(
        SourcePolicy(
            allow_subdomains=True,
            allowed_domains=["espn.com", "sports-reference.com"],
            blocked_domains=["bit.ly"],
            rules={"max_documents_per_game": 5, "require_published_at": True},
        )
    )


def test_canonicalize_url_normalizes_host_path_and_fragment() -> None:
    assert (
        canonicalize_url("HTTPS://WWW.ESPN.COM:443/mens-college-basketball?x=1#section")
        == "https://www.espn.com/mens-college-basketball?x=1"
    )
    assert canonicalize_url("http://espn.com") == "http://espn.com/"
    assert domain_from_url("https://www.espn.com/story") == "www.espn.com"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://espn.com/file", "unsupported URL scheme"),
        ("https://", "hostname"),
        ("", "non-empty"),
    ],
)
def test_canonicalize_url_rejects_invalid_input(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        canonicalize_url(url)


def test_whitelist_allows_domains_and_subdomains() -> None:
    whitelist = _whitelist()
    assert whitelist.is_allowed_url("https://www.espn.com/story")
    assert whitelist.is_allowed_url("https://espn.com/story")
    assert whitelist.is_allowed_url("https://sports-reference.com/cbb")
    assert not whitelist.is_allowed_url("https://example.com/story")
    assert not whitelist.is_allowed_url("https://bit.ly/short")
    assert not whitelist.is_allowed_url("not a url")


def test_whitelist_can_disable_subdomains() -> None:
    whitelist = SourceWhitelist(
        SourcePolicy(allow_subdomains=False, allowed_domains=["espn.com"])
    )
    assert whitelist.is_allowed_domain("espn.com")
    assert not whitelist.is_allowed_domain("www.espn.com")


def test_whitelist_reads_checked_in_policy(project_root: Path) -> None:
    whitelist = SourceWhitelist.from_path(project_root / "config" / "sources.yaml")
    assert whitelist.is_allowed_url("https://www.espn.com/mens-college-basketball")
    assert not whitelist.is_allowed_url("https://example.com/unverified")
    assert whitelist.require_published_at is True


def test_whitelist_rejects_missing_or_invalid_policy(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        SourceWhitelist.from_path(tmp_path / "missing.yaml")

    invalid = tmp_path / "sources.yaml"
    invalid.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        SourceWhitelist.from_path(invalid)


def test_policy_rejects_blank_domains() -> None:
    with pytest.raises(ValueError):
        SourcePolicy(allowed_domains=["  "])
