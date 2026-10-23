"""Grants.gov client: fixture loading, search2 pagination, and detail fetch."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import config
from models import Opportunity
from src.grants.normalize import normalize_opportunity, normalize_search_hit

FETCH_URL = config.GRANTS_FETCH_URL
SEARCH_URL = config.GRANTS_SEARCH_URL
OPEN_STATUSES = "forecasted|posted"


class GrantsError(RuntimeError):
    """Raised when Grants.gov returns an error or an unusable payload."""


def load_opportunity_fixture(path: Path | None = None) -> Opportunity:
    path = path or (config.FIXTURES_DIR / "grants_fetch_synopsis.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "data" in data and isinstance(data["data"], dict):
        data = data["data"]
    return normalize_opportunity(data, raw_ref=str(path))


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(2**attempt + random.uniform(0, 0.25), 30.0)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                break
            sleep(_retry_delay(attempt, exc.headers.get("Retry-After")))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep(_retry_delay(attempt))
    raise GrantsError(f"Grants.gov request failed: {url}") from last_error


def search_page(
    *,
    rows: int = config.GRANTS_SEARCH_ROWS,
    start: int = 0,
    statuses: str = OPEN_STATUSES,
    timeout: int = config.GRANTS_HTTP_TIMEOUT_SECONDS,
    retries: int = config.GRANTS_MAX_RETRIES,
) -> dict[str, Any]:
    payload = {
        "keyword": "",
        "oppStatuses": statuses,
        "rows": rows,
        "startRecordNum": start,
    }
    body = _post_json(SEARCH_URL, payload, timeout=timeout, retries=retries)
    if body.get("errorcode") != 0 or not isinstance(body.get("data"), dict):
        raise GrantsError(
            "Grants.gov search2 error: "
            f"{body.get('errorcode')} {body.get('msg')}".strip()
        )
    return body["data"]


def fetch_opportunity_detail(
    opportunity_id: int | str,
    *,
    timeout: int = config.GRANTS_HTTP_TIMEOUT_SECONDS,
    retries: int = config.GRANTS_MAX_RETRIES,
) -> dict[str, Any]:
    payload = {"opportunityId": int(opportunity_id)}
    body = _post_json(FETCH_URL, payload, timeout=timeout, retries=retries)
    if body.get("errorcode") != 0 or not isinstance(body.get("data"), dict):
        raise GrantsError(
            "Grants.gov fetchOpportunity error: "
            f"{body.get('errorcode')} {body.get('msg')}".strip()
        )
    return body["data"]


def fetch_opportunity(
    opportunity_id: int | str,
    timeout: int = config.GRANTS_HTTP_TIMEOUT_SECONDS,
) -> Opportunity:
    data = fetch_opportunity_detail(opportunity_id, timeout=timeout)
    return normalize_opportunity(data, raw_ref=FETCH_URL)


def fetch_search_hits(
    *,
    rows: int = config.GRANTS_SEARCH_ROWS,
    max_pages: int | None = None,
    statuses: str = OPEN_STATUSES,
    timeout: int = config.GRANTS_HTTP_TIMEOUT_SECONDS,
    retries: int = config.GRANTS_MAX_RETRIES,
    page_fn: Callable[..., dict[str, Any]] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Return (hits, hit_count, pages_fetched, complete_feed)."""
    page_fn = page_fn or search_page
    hits: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    hit_count = 0
    start = 0
    pages = 0
    page_limit = max_pages or config.GRANTS_MAX_PAGES
    while pages < page_limit:
        data = page_fn(rows=rows, start=start, statuses=statuses, timeout=timeout, retries=retries)
        if pages == 0:
            hit_count = int(data.get("hitCount") or 0)
        page_hits = data.get("oppHits") or []
        pages += 1
        for hit in page_hits:
            identifier = str(hit.get("id") or "")
            if not identifier or identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            hits.append(hit)
        if progress:
            progress(len(hits), hit_count)
        if not page_hits:
            break
        if len(hits) >= hit_count:
            break
        start += len(page_hits)
    complete = len(hits) >= hit_count
    return hits, hit_count, pages, complete


def search_hits_to_opportunities(hits: list[dict[str, Any]]) -> list[Opportunity]:
    return [normalize_search_hit(hit) for hit in hits]
