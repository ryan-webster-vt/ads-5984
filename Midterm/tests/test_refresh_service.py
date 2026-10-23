# Offline test module: Refresh orchestration, detail caching, partial failures, and archiving.
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from models import Opportunity, OpportunityDatabase
from src.grants.refresh import refresh_open_opportunities
from src.grants.store import load_database, save_database

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _hit(identifier: str, status: str = "posted") -> dict:
    return {
        "id": identifier,
        "number": f"NUM-{identifier}",
        "title": f"Opportunity {identifier}",
        "agency": "Test Agency",
        "agencyCode": "TA",
        "openDate": "01/01/2026",
        "closeDate": "",
        "oppStatus": status,
        "docType": "synopsis" if status == "posted" else "forecast",
        "cfdaList": ["00.001"],
    }


def _detail(
    identifier: str,
    status: str = "posted",
    *,
    ceiling: str = "100000",
    title: str | None = None,
    close_date: str | None = None,
) -> dict:
    detail = {
        "agencyCode": "TA",
        "postingDate": "Jan 01, 2026 12:00:00 AM EST",
        "applicantEligibilityDesc": "Eligible organizations",
        "costSharing": False,
        "awardCeiling": ceiling,
        "awardFloor": "none",
        "estimatedFunding": "500000",
    }
    if status == "posted":
        detail["responseDate"] = close_date or "Dec 31, 2027 12:00:00 AM EST"
        detail["synopsisDesc"] = f"<p>Description for {identifier}</p>"
        document = "synopsis"
    else:
        detail["estApplicationResponseDate"] = "Nov 30, 2027 12:00:00 AM EST"
        detail["forecastDesc"] = f"<p>Forecast for {identifier}</p>"
        document = "forecast"
    return {
        "id": identifier,
        "opportunityNumber": f"NUM-{identifier}",
        "opportunityTitle": title or f"Opportunity {identifier}",
        "ost": status.upper(),
        "docType": document,
        "revision": 3,
        "agencyDetails": {"agencyName": "Test Agency", "agencyCode": "TA"},
        "cfdas": [{"cfdaNumber": "00.001"}],
        document: detail,
    }


def _page_fn(hits: list[dict], hit_count: int, calls: list | None = None):
    def page_fn(**kwargs):
        if calls is not None:
            calls.append(kwargs["start"])
        start = kwargs["start"]
        rows = kwargs["rows"]
        return {"hitCount": hit_count, "oppHits": hits[start : start + rows]}

    return page_fn


def _detail_fn(details: dict[str, dict], calls: list | None = None, fail: set[str] | None = None):
    fail = fail or set()

    def fetch(identifier: str) -> dict:
        if calls is not None:
            calls.append(identifier)
        if identifier in fail:
            raise RuntimeError("detail unavailable")
        return details[identifier]

    return fetch


# Verifies: refresh fetches details and writes store.
def test_refresh_fetches_details_and_writes_store(tmp_path):
    hits = [_hit("1"), _hit("2", "forecasted")]
    details = {"1": _detail("1"), "2": _detail("2", "forecasted")}
    result = refresh_open_opportunities(
        store_path=tmp_path / "opportunities.json",
        cache_dir=tmp_path / "cache",
        rows=100,
        search_page_fn=_page_fn(hits, 2),
        fetch_detail_fn=_detail_fn(details),
        now_fn=lambda: NOW,
    )
    assert result.written is True
    assert result.summary.status == "success"
    assert result.summary.open_count == 2
    assert result.summary.detail_fetched == 2
    stored = load_database(tmp_path / "opportunities.json")
    by_id = {item.opportunity_id: item for item in stored.opportunities}
    assert by_id["1"].agency == "Test Agency"
    assert by_id["1"].open_date == "2026-01-01"
    assert by_id["1"].close_date == "2027-12-31"
    assert by_id["1"].award_ceiling == 100000.0
    assert by_id["1"].description == "Description for 1"
    assert by_id["2"].status == "forecasted"
    assert by_id["2"].close_date == "2027-11-30"
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


# Verifies: detail failure marks partial but still archives.
def test_detail_failure_marks_partial_but_still_archives(tmp_path):
    store = tmp_path / "opportunities.json"
    save_database(
        OpportunityDatabase(
            opportunities=[Opportunity(opportunity_id="3", title="Old", status="posted")]
        ),
        path=store,
        backup_path=tmp_path / "backup.json",
        lock_path=tmp_path / "lock",
    )
    hits = [_hit("1"), _hit("2")]
    details = {"1": _detail("1"), "2": _detail("2")}
    result = refresh_open_opportunities(
        store_path=store,
        cache_dir=tmp_path / "cache",
        rows=100,
        search_page_fn=_page_fn(hits, 2),
        fetch_detail_fn=_detail_fn(details, fail={"2"}),
        now_fn=lambda: NOW,
    )
    assert result.summary.status == "partial"
    assert result.summary.detail_failed == 1
    stored = load_database(store)
    by_id = {item.opportunity_id: item for item in stored.opportunities}
    assert by_id["2"].detail_status == "failed"
    assert "detail unavailable" in by_id["2"].detail_error
    assert by_id["3"].status == "archived"


# Verifies: detail cache reuses fresh entries.
def test_detail_cache_reuses_fresh_entries(tmp_path):
    hits = [_hit("1")]
    details = {"1": _detail("1")}
    calls: list[str] = []
    kwargs = {
        "store_path": tmp_path / "opportunities.json",
        "cache_dir": tmp_path / "cache",
        "rows": 100,
        "search_page_fn": _page_fn(hits, 1),
        "fetch_detail_fn": _detail_fn(details, calls=calls),
        "now_fn": lambda: NOW,
    }
    refresh_open_opportunities(**kwargs)
    second = refresh_open_opportunities(**kwargs)
    assert calls == ["1"]
    assert second.summary.detail_cached == 1
    assert second.summary.detail_fetched == 0
    assert second.summary.status == "success"

    kwargs["now_fn"] = lambda: NOW + timedelta(hours=25)
    third = refresh_open_opportunities(**kwargs)
    assert calls == ["1", "1"]
    assert third.summary.detail_fetched == 1


# Verifies: search failure preserves last good snapshot.
def test_search_failure_preserves_last_good_snapshot(tmp_path):
    store = tmp_path / "opportunities.json"
    hits = [_hit("1")]
    details = {"1": _detail("1")}
    refresh_open_opportunities(
        store_path=store,
        cache_dir=tmp_path / "cache",
        rows=100,
        search_page_fn=_page_fn(hits, 1),
        fetch_detail_fn=_detail_fn(details),
        now_fn=lambda: NOW,
    )
    before = store.read_bytes()

    def failing_page(**kwargs):
        raise RuntimeError("search down")

    result = refresh_open_opportunities(
        store_path=store,
        cache_dir=tmp_path / "cache",
        rows=100,
        search_page_fn=failing_page,
        fetch_detail_fn=_detail_fn(details),
        now_fn=lambda: NOW,
    )
    assert result.written is False
    assert result.summary.status == "failed"
    assert store.read_bytes() == before


# Verifies: incomplete feed does not archive but keeps upserts.
def test_incomplete_feed_does_not_archive_but_keeps_upserts(tmp_path):
    store = tmp_path / "opportunities.json"
    save_database(
        OpportunityDatabase(
            opportunities=[Opportunity(opportunity_id="9", title="Old", status="posted")]
        ),
        path=store,
        backup_path=tmp_path / "backup.json",
        lock_path=tmp_path / "lock",
    )
    hits = [_hit("1"), _hit("2")]
    details = {"1": _detail("1"), "2": _detail("2")}
    result = refresh_open_opportunities(
        store_path=store,
        cache_dir=tmp_path / "cache",
        rows=1,
        max_pages=1,
        search_page_fn=_page_fn(hits, 5),
        fetch_detail_fn=_detail_fn(details),
        now_fn=lambda: NOW,
    )
    assert result.summary.complete_feed is False
    assert result.summary.status == "partial"
    assert result.summary.archived_count == 0
    stored = load_database(store)
    by_id = {item.opportunity_id: item for item in stored.opportunities}
    assert by_id["9"].status == "posted"
    assert "1" in by_id


# Verifies: no details mode keeps search fields.
def test_no_details_mode_keeps_search_fields(tmp_path):
    hits = [_hit("1")]
    result = refresh_open_opportunities(
        store_path=tmp_path / "opportunities.json",
        cache_dir=tmp_path / "cache",
        rows=100,
        fetch_details=False,
        search_page_fn=_page_fn(hits, 1),
        fetch_detail_fn=_detail_fn({}, fail={"1"}),
        now_fn=lambda: NOW,
    )
    assert result.summary.detail_fetched == 0
    stored = load_database(tmp_path / "opportunities.json")
    assert stored.opportunities[0].detail_status == "search_only"
    assert stored.opportunities[0].award_ceiling is None


# Verifies: summary separates feed open from active open.
def test_summary_separates_feed_open_from_active_open(tmp_path):
    hits = [_hit("1"), _hit("2")]
    details = {
        "1": _detail("1"),
        "2": _detail("2", close_date="Jan 01, 2020 12:00:00 AM EST"),
    }
    result = refresh_open_opportunities(
        store_path=tmp_path / "opportunities.json",
        cache_dir=tmp_path / "cache",
        rows=100,
        search_page_fn=_page_fn(hits, 2),
        fetch_detail_fn=_detail_fn(details),
        now_fn=lambda: NOW,
    )
    assert result.summary.open_count == 2
    assert result.summary.active_open_count == 1
