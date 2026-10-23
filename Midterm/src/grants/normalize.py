"""Normalize Grants.gov opportunity payloads into the local contract."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, date, datetime
from typing import Any

from models import Opportunity

# Statuses that represent a currently open solicitation. Closed/archived values
# are retained in the store but hidden by the UI.
OPEN_STATUSES = {"posted", "forecasted"}
# Grants.gov mixes ISO, US, and human-readable timestamps across feed versions.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%b %d, %Y %I:%M:%S %p %Z",
    "%b %d, %Y %I:%M:%S %p",
    "%b %d, %Y",
    "%B %d, %Y",
)


def strip_html(value: str | None) -> str:
    """Convert API HTML into plain text while preserving paragraph boundaries."""
    if not value:
        return ""
    # Decode entities before tag stripping so encoded markup is handled too.
    text = html.unescape(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_amount(value: Any) -> float | None:
    """Parse amounts defensively; blanks and "none" become null, not zero."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "").replace("$", "")
    if not text or text in {"none", "n/a", "na", "null", "unknown"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_bool(value: Any) -> bool | None:
    """Treat unknown boolean spellings as null instead of guessing a value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def parse_date(value: Any) -> str | None:
    """Return an ISO date string, or None for blank/unparseable values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"none", "n/a", "na", "null"}:
        return None
    # Try known formats first; the regex fallbacks handle stray time zones or
    # labels that the API occasionally appends around a valid date.
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", text)
    if match:
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                candidate = f"{match.group(1)} {match.group(2)}, {match.group(3)}"
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        try:
            return datetime(
                int(match.group(3)), int(match.group(1)), int(match.group(2))
            ).date().isoformat()
        except ValueError:
            return None
    return None


def source_hash(payload: dict[str, Any]) -> str:
    """Hash a raw payload deterministically for change detection and provenance."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    """Return a timezone-aware timestamp used for cache and refresh metadata."""
    return datetime.now(UTC).isoformat()


def _status(data: dict[str, Any], detail: dict[str, Any] | None = None) -> str:
    # Detail payloads sometimes carry a more accurate status than search hits.
    detail = detail or {}
    ost = str(data.get("ost") or detail.get("ost") or "").strip().lower()
    if ost in {"posted", "forecasted", "closed", "archived"}:
        return ost
    doc_type = str(data.get("docType") or "").strip().lower()
    if doc_type == "forecast":
        return "forecasted"
    if doc_type == "synopsis":
        return "posted"
    return "unknown"


def _cfda_numbers(data: dict[str, Any]) -> list[str]:
    # CFDA/assistance-listing entries appear in more than one shape across APIs.
    values = data.get("cfdaList") or data.get("cfdas") or []
    numbers: list[str] = []
    for item in values:
        value = item.get("cfdaNumber") if isinstance(item, dict) else item
        text = str(value or "").strip()
        if text and text not in numbers:
            numbers.append(text)
    return numbers


def normalize_search_hit(data: dict[str, Any]) -> Opportunity:
    """Normalize one search2 hit, which lacks synopsis amounts and eligibility."""
    # Search hits are usable for browsing immediately; detail fields are filled
    # later by the refresh service and explicitly marked ``search_only``.
    return Opportunity(
        opportunity_id=str(data.get("id") or ""),
        opportunity_number=str(data.get("number") or data.get("opportunityNumber") or ""),
        title=str(data.get("title") or data.get("opportunityTitle") or "").strip(),
        agency=str(data.get("agency") or "").strip(),
        agency_code=str(data.get("agencyCode") or "").strip(),
        status=_status(data),
        doc_type=str(data.get("docType") or "").strip(),
        open_date=parse_date(data.get("openDate") or data.get("postingDate")),
        close_date=parse_date(
            data.get("closeDate")
            or data.get("responseDate")
            or data.get("estApplicationResponseDate")
        ),
        cfda_numbers=_cfda_numbers(data),
        detail_status="search_only",
        source_hash=source_hash(data),
        raw_ref="search2",
    )


def normalize_opportunity(data: dict[str, Any], raw_ref: str | None = None) -> Opportunity:
    """Normalize a fetchOpportunity response into the shared record contract."""
    # A record is either a synopsis (posted) or a forecast; the two payloads use
    # different field names but expose the same core details.
    synopsis = data.get("synopsis") or {}
    forecast = data.get("forecast") or {}
    detail = synopsis if synopsis else forecast
    agency_details = data.get("agencyDetails") or detail.get("agencyDetails") or {}
    top_agency = data.get("topAgencyDetails") or {}
    raw_description = detail.get("synopsisDesc") or detail.get("forecastDesc") or ""
    # Forecasts often provide an estimated response date instead of a firm one.
    close_value = (
        detail.get("responseDate")
        or detail.get("estApplicationResponseDate")
        or detail.get("responseDateDesc")
    )
    close_date = parse_date(close_value)
    # Revision drives detail-cache invalidation when Grants.gov edits a record.
    revision = data.get("revision")
    if revision is None:
        revision = detail.get("version")
    return Opportunity(
        opportunity_id=str(data.get("id") or data.get("opportunityId") or ""),
        opportunity_number=str(data.get("opportunityNumber") or ""),
        title=str(data.get("opportunityTitle") or "").strip(),
        agency=str(
            agency_details.get("agencyName")
            or detail.get("agencyName")
            or top_agency.get("agencyName")
            or ""
        ).strip(),
        agency_code=str(
            agency_details.get("agencyCode")
            or agency_details.get("seed")
            or data.get("owningAgencyCode")
            or ""
        ).strip(),
        status=_status(data, detail),
        doc_type=str(data.get("docType") or "").strip(),
        open_date=parse_date(detail.get("postingDate") or detail.get("estSynopsisPostingDate")),
        close_date=close_date,
        revision=int(revision) if revision is not None else None,
        description=strip_html(raw_description),
        # Keep the raw HTML only as provenance; the UI renders the plain text.
        description_html=raw_description or None,
        eligibility=strip_html(detail.get("applicantEligibilityDesc")),
        award_ceiling=parse_amount(detail.get("awardCeiling")),
        award_floor=parse_amount(detail.get("awardFloor")),
        estimated_funding=parse_amount(detail.get("estimatedFunding")),
        cost_sharing=parse_bool(detail.get("costSharing")),
        cfda_numbers=_cfda_numbers(data),
        synopsis_url=detail.get("fundingDescLinkUrl") or None,
        raw_ref=raw_ref,
        detail_status="success",
        detail_fetched_at=utc_now_iso(),
        source_hash=source_hash(data),
    )
