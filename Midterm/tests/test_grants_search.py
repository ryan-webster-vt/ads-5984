from __future__ import annotations

import json

import pytest

import config
from src.grants.client import (
    fetch_search_hits,
    load_opportunity_fixture,
    search_hits_to_opportunities,
)
from src.grants.normalize import parse_amount, parse_bool, parse_date


def _fixture_pages() -> list[dict]:
    payload = json.loads(
        (config.FIXTURES_DIR / "grants_search_page.json").read_text(encoding="utf-8")
    )
    return payload["pages"]


def test_recorded_search_pages_normalize_all_hits():
    pages = _fixture_pages()
    hits = [hit for page in pages for hit in page["response"]["data"]["oppHits"]]
    opportunities = search_hits_to_opportunities(hits)
    assert len(opportunities) == 6
    assert len({item.opportunity_id for item in opportunities}) == 6
    assert all(item.detail_status == "search_only" for item in opportunities)
    assert all(item.open_date for item in opportunities)
    assert {item.status for item in opportunities} <= {"posted", "forecasted"}


def test_pagination_covers_two_pages_and_reports_complete():
    pages = _fixture_pages()
    state = {"calls": 0}

    def page_fn(**kwargs):
        state["calls"] += 1
        index = kwargs["start"] // 3
        data = dict(pages[index]["response"]["data"])
        data["hitCount"] = 6
        return data

    hits, hit_count, pages_fetched, complete = fetch_search_hits(rows=3, page_fn=page_fn)
    assert hit_count == 6
    assert len(hits) == 6
    assert pages_fetched == 2
    assert complete is True
    assert state["calls"] == 2


def test_pagination_marks_page_limit_partial():
    pages = _fixture_pages()

    def page_fn(**kwargs):
        index = kwargs["start"] // 3
        data = dict(pages[index]["response"]["data"])
        data["hitCount"] = 10
        return data

    hits, hit_count, pages_fetched, complete = fetch_search_hits(
        rows=3, max_pages=1, page_fn=page_fn
    )
    assert hit_count == 10
    assert len(hits) == 3
    assert pages_fetched == 1
    assert complete is False


def test_pagination_propagates_page_failure():
    def page_fn(**kwargs):
        raise RuntimeError("page failed")

    with pytest.raises(RuntimeError):
        fetch_search_hits(rows=3, page_fn=page_fn)


def test_recorded_forecast_detail_normalizes():
    path = config.FIXTURES_DIR / "grants_fetch_forecast.json"
    record = load_opportunity_fixture(path)
    assert record.opportunity_id == "361447"
    assert record.status == "forecasted"
    assert record.doc_type == "forecast"
    assert record.agency == "National Institutes of Health"
    assert record.open_date == "2026-03-11"
    assert record.close_date == "2026-10-05"
    assert record.revision == 0
    assert record.detail_status == "success"
    assert record.award_ceiling is None
    assert record.cost_sharing is False
    assert "<p>" not in record.description


def test_date_amount_and_bool_parsing():
    assert parse_date("Dec 10, 2020 12:00:00 AM EST") == "2020-12-10"
    assert parse_date("08/01/2024") == "2024-08-01"
    assert parse_date("2026-10-22") == "2026-10-22"
    assert parse_date("Proposals accepted anytime") is None
    assert parse_date("") is None
    assert parse_amount("none") is None
    assert parse_amount("$1,250,000") == 1250000.0
    assert parse_bool("false") is False
    assert parse_bool("TRUE") is True
    assert parse_bool(None) is None
