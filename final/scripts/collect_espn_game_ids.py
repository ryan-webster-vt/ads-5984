"""Collect ESPN game ids for a full season.

``EspnHistoricalStatsProvider.list_game_ids`` deliberately refuses to enumerate
a season, so live ingestion requires explicit ``--game-id`` values. This helper
sweeps the public scoreboard endpoint day by day and writes the completed game
ids it finds, so a whole season can be ingested:

    python3 scripts/collect_espn_game_ids.py --season 2021-2022 \
        --output data/processed/espn_game_ids_2021-2022.txt

    python3 -m upset_profiler ingest --season 2021-2022 --provider espn \
        --context-provider fixture \
        $(sed 's/^/--game-id /' data/processed/espn_game_ids_2021-2022.txt)

Passing thousands of ``--game-id`` flags can exceed the shell argument limit;
prefer ``--game-id-file`` when a command supports it, or ingest in slices.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
GROUPS = "50"  # Division I
LIMIT = "1000"


def season_bounds(season: str) -> tuple[date, date]:
    """Return the conservative date range for a ``YYYY-YYYY`` season."""
    try:
        start_year = int(season.split("-", 1)[0])
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"invalid season {season!r}; expected e.g. 2021-2022") from exc
    return date(start_year, 11, 1), date(start_year + 1, 4, 10)


def fetch_day(day: date, *, timeout: float) -> list[dict[str, object]]:
    """Return the scoreboard events for one day."""
    url = (
        f"{SCOREBOARD_URL}?dates={day.strftime('%Y%m%d')}"
        f"&groups={GROUPS}&limit={LIMIT}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "upset-profiler/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    events = payload.get("events") or []
    return events if isinstance(events, list) else []


def _completed(event: dict[str, object]) -> bool:
    competitions = event.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        return False
    competition = competitions[0]
    if not isinstance(competition, dict):
        return False
    status = competition.get("status")
    if not isinstance(status, dict):
        return False
    status_type = status.get("type")
    return isinstance(status_type, dict) and bool(status_type.get("completed"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, help="Season, e.g. 2021-2022")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--delay", type=float, default=0.25, help="Seconds to sleep between requests"
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--completed-only",
        action="store_true",
        default=True,
        help="Keep only finished games (default)",
    )
    args = parser.parse_args(argv)

    default_start, default_end = season_bounds(args.season)
    day, end = args.start or default_start, args.end or default_end
    if day > end:
        raise SystemExit("--start must not be after --end")

    ids: list[str] = []
    seen: set[str] = set()
    days_with_games = 0
    failures: list[str] = []
    while day <= end:
        try:
            events = fetch_day(day, timeout=args.timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            failures.append(f"{day.isoformat()}: {exc}")
            day += timedelta(days=1)
            continue
        kept = 0
        for event in events:
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            if args.completed_only and not _completed(event):
                continue
            seen.add(event_id)
            ids.append(event_id)
            kept += 1
        if kept:
            days_with_games += 1
        print(f"{day.isoformat()}  events={len(events):>3}  kept={kept:>3}  total={len(ids)}",
              file=sys.stderr)
        day += timedelta(days=1)
        time.sleep(args.delay)

    if failures:
        print(f"warning: {len(failures)} day(s) failed", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)

    print(
        f"season {args.season}: {len(ids)} completed games over "
        f"{days_with_games} game days",
        file=sys.stderr,
    )
    text = "\n".join(ids)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + ("\n" if ids else ""), encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
