"""Tests for deterministic search-cache normalization, keys, and TTL policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from upset_profiler.services.search_cache import (
    normalize_search_query,
    resolve_search_expiry,
    search_params_fingerprint,
    search_query_hash,
)


@pytest.mark.unit
def test_normalize_search_query_collapses_case_whitespace_and_punctuation() -> None:
    assert normalize_search_query("  Duke   AT North Carolina!!!  ") == (
        "duke at north carolina"
    )
    assert normalize_search_query("— Duke at UNC —") == "duke at unc"
    assert normalize_search_query("Ｄｕｋｅ　ＶＳ　ＵＮＣ") == "duke vs unc"  # noqa: RUF001
    assert normalize_search_query("Straße preview") == "strasse preview"


@pytest.mark.unit
def test_normalize_search_query_preserves_token_order_and_stopwords() -> None:
    forward = normalize_search_query("duke at north carolina preview")
    reverse = normalize_search_query("north carolina at duke preview")
    assert forward != reverse
    assert "at" in forward
    assert normalize_search_query("Duke's offense") == "duke's offense"


@pytest.mark.unit
def test_normalize_search_query_rejects_empty_input() -> None:
    for value in ("", "   ", "...", " — "):
        with pytest.raises(ValueError, match="at least one token"):
            normalize_search_query(value)
    with pytest.raises(TypeError, match="string"):
        normalize_search_query(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_params_fingerprint_is_order_insensitive_and_type_strict() -> None:
    first = search_params_fingerprint(
        {
            "search_depth": "basic",
            "include_domains": ["espn.com", "ncaa.com"],
            "topic": "general",
        }
    )
    reordered = search_params_fingerprint(
        {
            "topic": "general",
            "include_domains": ["ncaa.com", "espn.com"],
            "search_depth": "basic",
        }
    )
    assert first == reordered

    harder = search_params_fingerprint(
        {"search_depth": "advanced", "include_domains": ["espn.com", "ncaa.com"]}
    )
    assert first != harder

    assert search_params_fingerprint({"topic": None, "depth": "basic"}) == (
        search_params_fingerprint({"depth": "basic"})
    )
    with pytest.raises(TypeError, match="unsupported"):
        search_params_fingerprint({"window": object()})


@pytest.mark.unit
def test_query_hash_separates_provider_params_and_text() -> None:
    fingerprint = search_params_fingerprint({"depth": "basic"})
    base = search_query_hash(
        "duke at unc", provider="tavily", params_hash=fingerprint
    )
    assert base == search_query_hash(
        "duke at unc", provider="tavily", params_hash=fingerprint
    )
    assert base != search_query_hash(
        "duke at unc", provider="brave-mcp", params_hash=fingerprint
    )
    assert base != search_query_hash(
        "unc at duke", provider="tavily", params_hash=fingerprint
    )
    assert base != search_query_hash(
        "duke at unc",
        provider="tavily",
        params_hash=search_params_fingerprint({"depth": "advanced"}),
    )


@pytest.mark.unit
def test_expiry_policy_is_immutable_for_history_and_ttl_for_live() -> None:
    requested = datetime(2026, 11, 10, 12, tzinfo=UTC)
    assert (
        resolve_search_expiry(
            requested,
            tipoff_at=datetime(2024, 12, 10, tzinfo=UTC),
            completed=True,
            ttl_seconds=3600,
        )
        is None
    )
    assert (
        resolve_search_expiry(
            requested,
            tipoff_at=requested - timedelta(hours=1),
            completed=False,
            ttl_seconds=3600,
        )
        is None
    )
    assert resolve_search_expiry(
        requested,
        tipoff_at=requested + timedelta(hours=1),
        completed=False,
        ttl_seconds=3600,
    ) == requested + timedelta(seconds=3600)
    assert resolve_search_expiry(
        requested,
        tipoff_at=None,
        completed=False,
        ttl_seconds=3600,
    ) == requested + timedelta(seconds=3600)
    assert resolve_search_expiry(
        requested,
        tipoff_at=None,
        completed=False,
        ttl_seconds=0,
    ) == requested
    with pytest.raises(ValueError, match="non-negative"):
        resolve_search_expiry(
            requested,
            tipoff_at=None,
            completed=False,
            ttl_seconds=-1,
        )
