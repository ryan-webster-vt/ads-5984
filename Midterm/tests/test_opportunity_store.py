# Offline test module: Atomic opportunity storage, backups, locking, and archives.
from __future__ import annotations

import json
from datetime import date

import pytest

import src.io_utils as io_utils
from models import Opportunity, OpportunityDatabase
from src.grants.store import (
    is_expired,
    is_visible_open,
    load_database,
    merge_open_feed,
    save_database,
)


def _record(identifier: str, *, status: str = "posted", close_date=None) -> Opportunity:
    return Opportunity(
        opportunity_id=identifier,
        title=f"Opportunity {identifier}",
        status=status,
        close_date=close_date,
    )


# Verifies: atomic write interruption preserves original.
def test_atomic_write_interruption_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "opportunities.json"
    path.write_text(json.dumps({"value": "original"}), encoding="utf-8")

    def broken_replace(source, destination):
        raise OSError("simulated interruption")

    monkeypatch.setattr(io_utils.os, "replace", broken_replace)
    with pytest.raises(OSError):
        io_utils.write_json_atomic(path, {"value": "new"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "original"}
    assert list(tmp_path.glob("*.tmp")) == []


# Verifies: store roundtrip and backup.
def test_store_roundtrip_and_backup(tmp_path):
    path = tmp_path / "opportunities.json"
    backup = tmp_path / "opportunities.json.bak"
    lock = tmp_path / ".lock"
    database = OpportunityDatabase(opportunities=[_record("1")])
    save_database(database, path=path, backup_path=backup, lock_path=lock)
    loaded = load_database(path)
    assert loaded.opportunities[0].opportunity_id == "1"

    database.opportunities.append(_record("2"))
    save_database(database, path=path, backup_path=backup, lock_path=lock)
    first = json.loads(backup.read_text(encoding="utf-8"))
    assert [item["opportunity_id"] for item in first["opportunities"]] == ["1"]
    assert [item.opportunity_id for item in load_database(path).opportunities] == [
        "1",
        "2",
    ]


# Verifies: complete feed archives absent records and preserves first seen.
def test_complete_feed_archives_absent_records_and_preserves_first_seen():
    existing = [
        _record("1"),
        _record("2"),
        _record("9", status="archived"),
    ]
    existing[0].first_seen = "2026-01-01T00:00:00+00:00"
    fresh = [_record("1")]
    merged, archived_count = merge_open_feed(
        existing,
        fresh,
        complete_feed=True,
        now="2026-10-22T12:00:00+00:00",
    )
    by_id = {item.opportunity_id: item for item in merged}
    assert archived_count == 1
    assert by_id["1"].status == "posted"
    assert by_id["1"].first_seen == "2026-01-01T00:00:00+00:00"
    assert by_id["2"].status == "archived"
    assert by_id["2"].archive_reason == "absent_from_complete_open_feed"
    assert by_id["2"].archived_at == "2026-10-22T12:00:00+00:00"
    assert by_id["9"].status == "archived"


# Verifies: partial feed never archives.
def test_partial_feed_never_archives():
    existing = [_record("1"), _record("2")]
    merged, archived_count = merge_open_feed(
        existing,
        [_record("1")],
        complete_feed=False,
        now="2026-10-22T12:00:00+00:00",
    )
    by_id = {item.opportunity_id: item for item in merged}
    assert archived_count == 0
    assert by_id["2"].status == "posted"


# Verifies: complete feed converts closed to archived.
def test_complete_feed_converts_closed_to_archived():
    merged, archived_count = merge_open_feed(
        [_record("1", status="closed")],
        [],
        complete_feed=True,
        now="2026-10-22T12:00:00+00:00",
    )
    assert archived_count == 1
    assert merged[0].status == "archived"
    assert merged[0].archive_reason == "reported_closed"


# Verifies: archived record reopens when it returns.
def test_archived_record_reopens_when_it_returns():
    existing = [_record("1", status="archived")]
    existing[0].archive_reason = "old"
    existing[0].archived_at = "2026-01-01T00:00:00+00:00"
    merged, _ = merge_open_feed(
        existing,
        [_record("1", status="forecasted")],
        complete_feed=True,
        now="2026-10-22T12:00:00+00:00",
    )
    assert merged[0].status == "forecasted"
    assert merged[0].archive_reason is None
    assert merged[0].archived_at is None


# Verifies: fresh non open status is archived.
def test_fresh_non_open_status_is_archived():
    merged, _ = merge_open_feed(
        [],
        [_record("1", status="closed")],
        complete_feed=True,
        now="2026-10-22T12:00:00+00:00",
    )
    assert merged[0].status == "archived"
    assert merged[0].archive_reason == "reported_closed"


# Verifies: visible open hides expired and archived.
def test_visible_open_hides_expired_and_archived():
    expired = _record("1", close_date="2026-10-01")
    open_ended = _record("2", close_date=None)
    archived = _record("3", status="archived")
    today = date(2026, 10, 22)
    assert is_expired(expired, today=today) is True
    assert is_visible_open(expired, today=today) is False
    assert is_visible_open(open_ended, today=today) is True
    assert is_visible_open(archived, today=today) is False
