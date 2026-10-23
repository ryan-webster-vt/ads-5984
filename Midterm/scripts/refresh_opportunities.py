#!/usr/bin/env python3
"""Refresh the local Grants.gov opportunity store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make project modules importable when this script is launched from any cwd.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.grants.refresh import refresh_open_opportunities  # noqa: E402


def _progress(stage: str, done: int, total: int) -> None:
    # Progress is line-oriented so cron/terminal logs remain readable.
    if stage == "search" and done:
        print(f"search: {done}/{total} opportunities", flush=True)
    elif stage == "details" and done:
        print(f"details: {done}/{total}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=config.GRANTS_SEARCH_ROWS)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Explicit demo limit; an incomplete feed is marked partial and never archives.",
    )
    # Detail fetching can be disabled for a fast search-only preview.
    parser.add_argument("--workers", type=int, default=config.GRANTS_MAX_WORKERS)
    parser.add_argument("--no-details", action="store_true")
    parser.add_argument("--force-details", action="store_true")
    parser.add_argument(
        "--ttl-hours",
        type=float,
        default=config.GRANTS_DETAIL_CACHE_TTL_HOURS,
    )
    parser.add_argument("--store", type=Path, default=config.OPPORTUNITIES_PATH)
    parser.add_argument("--cache-dir", type=Path, default=config.DETAIL_CACHE_DIR)
    args = parser.parse_args()

    config.ensure_directories()
    # The refresh service owns safety policy; this CLI only parses options and
    # reports the returned summary.
    result = refresh_open_opportunities(
        store_path=args.store,
        cache_dir=args.cache_dir,
        rows=args.rows,
        max_pages=args.max_pages,
        fetch_details=not args.no_details,
        force_details=args.force_details,
        ttl_hours=args.ttl_hours,
        workers=args.workers,
        progress=_progress,
    )
    summary = result.summary
    print(
        f"status={summary.status} written={result.written} "
        f"hit_count={summary.search_hit_count} open={summary.open_count} "
        f"active_open={summary.active_open_count} "
        f"archived={summary.archived_count} details_fetched={summary.detail_fetched} "
        f"details_cached={summary.detail_cached} details_failed={summary.detail_failed} "
        f"duration_ms={summary.duration_ms}"
    )
    for error in summary.errors:
        print(f"warning: {error}")
    # Partial refreshes are still useful (the store was safely updated); only a
    # failed search/write should make automation fail.
    return 0 if summary.status in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
