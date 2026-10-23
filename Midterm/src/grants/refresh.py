"""Refresh orchestration: paginated search, cached details, and safe archiving."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import config
from models import Opportunity, OpportunityDatabase, RefreshSummary
from src.grants.client import (
    fetch_opportunity_detail,
    fetch_search_hits,
    search_hits_to_opportunities,
)
from src.grants.normalize import normalize_opportunity
from src.grants.store import (
    is_visible_open,
    load_database,
    merge_open_feed,
    save_database,
)
from src.io_utils import read_json, write_json_atomic
from src.redaction import safe_exception_message

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class RefreshResult:
    # ``database`` is always safe to render; ``written`` distinguishes a real
    # update from a failed refresh that preserved the previous snapshot.
    database: OpportunityDatabase
    summary: RefreshSummary
    written: bool


def _cache_path(cache_dir: Path, opportunity_id: str) -> Path:
    # Opportunity IDs are numeric strings from Grants.gov, so they are safe as
    # filenames and keep cache inspection straightforward.
    return cache_dir / f"{opportunity_id}.json"


def _load_cache(path: Path) -> dict[str, Any] | None:
    # Cache corruption must degrade to a refetch, never break the refresh.
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _cache_is_fresh(
    payload: dict[str, Any], *, now: datetime, ttl_hours: float
) -> bool:
    if ttl_hours <= 0:
        return False
    # Timestamps are stored with timezone information; tolerate older naive
    # entries by assuming UTC instead of failing the whole refresh.
    cached_at = payload.get("cached_at")
    if not cached_at:
        return False
    try:
        timestamp = datetime.fromisoformat(cached_at)
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return now - timestamp <= timedelta(hours=ttl_hours)


def _apply_detail(record: Opportunity, detail: Opportunity) -> None:
    # Detail data wins over search-level fields, except that an existing explicit
    # close date is kept if the detail payload omits it.
    record.agency = detail.agency or record.agency
    record.agency_code = detail.agency_code or record.agency_code
    record.open_date = detail.open_date or record.open_date
    record.close_date = record.close_date or detail.close_date
    record.description = detail.description or record.description
    record.description_html = detail.description_html or record.description_html
    record.eligibility = detail.eligibility or record.eligibility
    record.award_ceiling = detail.award_ceiling
    record.award_floor = detail.award_floor
    record.estimated_funding = detail.estimated_funding
    record.cost_sharing = detail.cost_sharing
    record.synopsis_url = detail.synopsis_url or record.synopsis_url
    if detail.revision is not None:
        record.revision = detail.revision
    record.detail_fetched_at = detail.detail_fetched_at
    record.detail_status = "success"
    record.detail_error = None


def _preserve_previous_detail(record: Opportunity, previous: Opportunity | None) -> None:
    # A transient detail failure must not erase good data from an earlier refresh.
    if previous is None or previous.detail_status != "success":
        return
    record.description = previous.description or record.description
    record.description_html = previous.description_html or record.description_html
    record.eligibility = previous.eligibility or record.eligibility
    record.award_ceiling = previous.award_ceiling
    record.award_floor = previous.award_floor
    record.estimated_funding = previous.estimated_funding
    record.cost_sharing = previous.cost_sharing
    record.synopsis_url = previous.synopsis_url or record.synopsis_url
    record.revision = previous.revision
    record.detail_fetched_at = previous.detail_fetched_at


def _enrich_details(
    fresh: list[Opportunity],
    previous_by_id: dict[str, Opportunity],
    *,
    cache_dir: Path,
    ttl_hours: float,
    force: bool,
    workers: int,
    now: datetime,
    fetch_fn: Callable[[str], dict[str, Any]],
    progress: ProgressCallback | None,
) -> tuple[int, int, int, list[str]]:
    fetched = 0
    cached = 0
    failed = 0
    errors: list[str] = []
    to_fetch: list[Opportunity] = []

    # First pass is cheap: reuse fresh cache entries and queue the rest.
    for record in fresh:
        cache = _load_cache(_cache_path(cache_dir, record.opportunity_id))
        if cache and not force and _cache_is_fresh(cache, now=now, ttl_hours=ttl_hours):
            try:
                detail = Opportunity.model_validate(cache["record"])
            except (KeyError, ValueError):
                to_fetch.append(record)
                continue
            _apply_detail(record, detail)
            cached += 1
        else:
            to_fetch.append(record)

    total = len(to_fetch)
    if progress and total:
        progress("details", 0, total)

    def _fetch(record: Opportunity) -> tuple[Opportunity, dict[str, Any]]:
        # Runs in a worker thread; only immutable inputs cross the thread boundary.
        return record, fetch_fn(record.opportunity_id)

    if to_fetch:
        # Concurrent detail fetches are bounded to keep Grants.gov load and memory
        # use predictable; results are applied on the main thread as they finish.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_fetch, record): record for record in to_fetch}
            for done, future in enumerate(as_completed(futures), start=1):
                record = futures[future]
                try:
                    _, payload = future.result()
                    detail = normalize_opportunity(
                        payload, raw_ref=config.GRANTS_FETCH_URL
                    )
                    _apply_detail(record, detail)
                    # Cache the normalized detail plus its revision so a later
                    # refresh can invalidate it when Grants.gov edits the record.
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    write_json_atomic(
                        _cache_path(cache_dir, record.opportunity_id),
                        {
                            "cached_at": now.isoformat(),
                            "revision": detail.revision,
                            "record": detail.model_dump(mode="json"),
                        },
                    )
                    fetched += 1
                except Exception as exc:
                    # Keep the search-level record and the last known detail, then
                    # report a partial refresh instead of inventing missing values.
                    failed += 1
                    _preserve_previous_detail(
                        record, previous_by_id.get(record.opportunity_id)
                    )
                    record.detail_status = "failed"
                    record.detail_error = safe_exception_message(exc)
                    if len(errors) < 20:
                        errors.append(
                            f"Detail fetch failed for {record.opportunity_id}: "
                            f"{type(exc).__name__}"
                        )
                if progress and (done == total or done % 25 == 0):
                    progress("details", done, total)

    return fetched, cached, failed, errors


def refresh_open_opportunities(
    *,
    store_path: Path | None = None,
    cache_dir: Path | None = None,
    rows: int = config.GRANTS_SEARCH_ROWS,
    max_pages: int | None = None,
    fetch_details: bool = True,
    force_details: bool = False,
    ttl_hours: float = config.GRANTS_DETAIL_CACHE_TTL_HOURS,
    workers: int = config.GRANTS_MAX_WORKERS,
    search_page_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_detail_fn: Callable[[str], dict[str, Any]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    progress: ProgressCallback | None = None,
) -> RefreshResult:
    # Defaults come from config so the UI and CLI share one budget and cache.
    store_path = store_path or config.OPPORTUNITIES_PATH
    cache_dir = cache_dir or config.DETAIL_CACHE_DIR
    fetch_detail_fn = fetch_detail_fn or fetch_opportunity_detail
    now_fn = now_fn or (lambda: datetime.now(UTC))
    started = now_fn()
    started_iso = started.isoformat()
    previous_db = load_database(store_path)

    # A search failure aborts before any write: the last good snapshot stays.
    try:
        hits, hit_count, _pages, complete_feed = fetch_search_hits(
            rows=rows,
            max_pages=max_pages,
            page_fn=search_page_fn,
            progress=(
                (lambda done, total: progress("search", done, total))
                if progress
                else None
            ),
        )
    except Exception as exc:
        summary = RefreshSummary(
            started_at=started_iso,
            completed_at=now_fn().isoformat(),
            status="failed",
            complete_feed=False,
            errors=[f"Search failed: {type(exc).__name__}"],
        )
        return RefreshResult(database=previous_db, summary=summary, written=False)

    fresh = search_hits_to_opportunities(hits)
    previous_by_id = {
        record.opportunity_id: record for record in previous_db.opportunities
    }
    detail_fetched = detail_cached = detail_failed = 0
    detail_errors: list[str] = []
    if fetch_details:
        detail_fetched, detail_cached, detail_failed, detail_errors = _enrich_details(
            fresh,
            previous_by_id,
            cache_dir=cache_dir,
            ttl_hours=ttl_hours,
            force=force_details,
            workers=workers,
            now=started,
            fetch_fn=fetch_detail_fn,
            progress=progress,
        )
    else:
        # Search-only mode deliberately leaves amounts unknown rather than
        # reusing stale detail data as if it were current.
        for record in fresh:
            _preserve_previous_detail(record, previous_by_id.get(record.opportunity_id))
            if previous_by_id.get(record.opportunity_id):
                record.detail_status = "search_only"

    # Archive decisions are centralized in the store merge, which also enforces
    # the complete-feed-only policy.
    merged, archived_count = merge_open_feed(
        previous_db.opportunities,
        fresh,
        complete_feed=complete_feed,
        now=started_iso,
    )
    merged.sort(key=lambda record: record.opportunity_id)
    # The feed count and the visible/active count are intentionally different:
    # active hides records with an explicit past close date.
    today = started.astimezone().date()
    open_count = sum(
        1 for record in merged if record.status in config.OPPORTUNITY_OPEN_STATUSES
    )
    active_open_count = sum(
        1 for record in merged if is_visible_open(record, today=today)
    )
    # Detail failures make the run partial even when the feed itself was complete.
    status = "success" if complete_feed and detail_failed == 0 else "partial"
    summary = RefreshSummary(
        started_at=started_iso,
        completed_at=now_fn().isoformat(),
        status=status,
        complete_feed=complete_feed,
        search_hit_count=hit_count,
        open_count=open_count,
        active_open_count=active_open_count,
        archived_count=archived_count,
        detail_fetched=detail_fetched,
        detail_cached=detail_cached,
        detail_failed=detail_failed,
        duration_ms=int((now_fn() - started).total_seconds() * 1000),
        errors=detail_errors,
    )
    database = OpportunityDatabase(
        schema_version=config.OPPORTUNITY_SCHEMA_VERSION,
        last_refresh=summary,
        opportunities=merged,
    )
    save_database(
        database,
        path=store_path,
        backup_path=store_path.with_name(f"{store_path.name}.bak"),
        lock_path=store_path.with_name(f".{store_path.name}.lock"),
    )
    return RefreshResult(database=database, summary=summary, written=True)
