"""Atomic local opportunity storage and complete-feed archive policy."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import config
from models import Opportunity, OpportunityDatabase
from src.io_utils import exclusive_file_lock, read_json, write_json_atomic

# Sets are intentionally frozen: these status decisions must not be mutated by
# callers while a refresh is in progress.
OPEN_STATUSES = frozenset(config.OPPORTUNITY_OPEN_STATUSES)
ARCHIVED_STATUSES = frozenset({"archived", "closed"})


def load_database(path: Path | None = None) -> OpportunityDatabase:
    """Load the typed opportunity store, returning an empty one if absent."""
    path = path or config.OPPORTUNITIES_PATH
    if not path.exists():
        return OpportunityDatabase(schema_version=config.OPPORTUNITY_SCHEMA_VERSION)
    payload = read_json(path)
    return OpportunityDatabase.model_validate(payload)


def save_database(
    database: OpportunityDatabase,
    *,
    path: Path | None = None,
    backup_path: Path | None = None,
    lock_path: Path | None = None,
) -> None:
    path = path or config.OPPORTUNITIES_PATH
    backup_path = backup_path or config.OPPORTUNITIES_BACKUP_PATH
    lock_path = lock_path or config.OPPORTUNITIES_LOCK_PATH
    # The lock serializes writers across processes; the atomic writer keeps the
    # previous JSON intact if the process dies mid-save.
    with exclusive_file_lock(lock_path):
        write_json_atomic(
            path,
            database.model_dump(mode="json"),
            backup_path=backup_path,
        )


def is_expired(record: Opportunity, *, today: date | None = None) -> bool:
    """Hide records whose explicit close date is already in the past."""
    if not record.close_date:
        return False
    today = today or date.today()
    try:
        close = date.fromisoformat(record.close_date)
    except ValueError:
        return False
    return close < today


def is_visible_open(record: Opportunity, *, today: date | None = None) -> bool:
    """Return whether a record belongs in the active browsing list."""
    # Status alone is insufficient: Grants.gov sometimes leaves a past-due record
    # marked as posted/forecasted.
    return record.status in OPEN_STATUSES and not is_expired(record, today=today)


def _clear_archive_fields(record: Opportunity) -> None:
    # A record that reappears in the open feed should not retain stale archive
    # metadata from an earlier refresh.
    record.archive_reason = None
    record.archived_at = None


def _archive(record: Opportunity, *, now: str, reason: str) -> None:
    # Archive reasons are persisted so operators can audit why a record vanished.
    record.status = "archived"
    record.archive_reason = reason
    record.archived_at = now


def merge_open_feed(
    existing: list[Opportunity],
    fresh: list[Opportunity],
    *,
    complete_feed: bool,
    now: str,
) -> tuple[list[Opportunity], int]:
    """Merge a refresh into the store and return (records, newly_archived_count)."""
    existing_by_id = {item.opportunity_id: item for item in existing}
    merged: list[Opportunity] = []
    seen: set[str] = set()
    archived_count = 0

    # First pass: upsert every fresh record and preserve first-seen provenance.
    for record in fresh:
        if not record.opportunity_id or record.opportunity_id in seen:
            continue
        seen.add(record.opportunity_id)
        old = existing_by_id.get(record.opportunity_id)
        if old is not None:
            record.first_seen = old.first_seen or old.last_seen or now
        else:
            record.first_seen = now
        record.last_seen = now
        if record.status not in OPEN_STATUSES:
            # The API itself marked the record closed/archived; archive it even
            # when the feed was partial.
            _archive(record, now=now, reason=f"reported_{record.status}")
        else:
            _clear_archive_fields(record)
        merged.append(record)

    # Second pass: only a complete feed may archive previously open records.
    for old in existing:
        if old.opportunity_id in seen:
            continue
        if complete_feed and old.status in OPEN_STATUSES:
            _archive(old, now=now, reason="absent_from_complete_open_feed")
            archived_count += 1
        elif complete_feed and old.status == "closed":
            _archive(old, now=now, reason="reported_closed")
            archived_count += 1
        merged.append(old)

    return merged, archived_count
