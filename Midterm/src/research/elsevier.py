"""Optional Elsevier/Scopus adapter.

CSV remains the default and reproducible source. Elsevier mode is key-gated: it
queries Scopus Search for each eligible roster author, retrieves abstracts through
Abstract Retrieval, normalizes every record into the same ``Publication`` model,
and caches abstract payloads so repeated builds do not spend quota twice.

Live Elsevier access is only exercised when ``ELSEVIER_API_KEY`` is configured;
recorded synthetic fixtures prove parsing and failure handling only.
"""

from __future__ import annotations

import hashlib
import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import config
from models import Faculty, Publication
from src.io_utils import write_json_atomic
from src.research.identity import (
    attribute_faculty_ids,
    build_eligible_faculty_index,
)

# Scopus exposes two endpoints used by this adapter: Search returns metadata for
# an author query, and Abstract Retrieval returns the evidence we index.
SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
ABSTRACT_URL = "https://api.elsevier.com/content/abstract/eid/{eid}"
DEFAULT_PAGE_SIZE = config.ELSEVIER_SEARCH_PAGE_SIZE


class ElsevierNotConfigured(RuntimeError):
    """Raised when the optional Elsevier key is absent."""


class ElsevierAPIError(RuntimeError):
    """Raised for non-successful Elsevier responses without exposing secrets."""


class ElsevierAuthError(ElsevierAPIError):
    """Raised for 401/403 responses; retrying cannot fix missing entitlement."""


class ElsevierQuotaError(ElsevierAPIError):
    """Raised when a 429 response persists after bounded retries."""


def _as_list(value: Any) -> list[Any]:
    # Scopus frequently returns a dict for one item and a list for many; callers
    # should not need to branch on that shape.
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any) -> str:
    # Elsevier wraps many scalar values as {"$": "text"} or {"value": ...}.
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("$", "value", "_"):
            if key in value:
                return _text(value[key])
        return ""
    return str(value).strip()


def _clean_text(value: Any) -> str:
    # Abstracts may contain escaped HTML; decode and flatten before indexing.
    text = _text(value)
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_int(value: Any) -> int | None:
    # Scopus numeric fields arrive as strings and sometimes as decimals.
    text = _text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _year(value: Any) -> int:
    # Cover dates can include a full timestamp; keep only the four-digit year.
    match = re.search(r"\b(\d{4})\b", _text(value))
    return int(match.group(1)) if match else 0


def _keyword_values(value: Any) -> list[str]:
    # Keywords appear under different keys in search versus abstract payloads.
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        for key in ("author-keyword", "mainterm", "keyword", "$"):
            if key in value:
                return _keyword_values(value[key])
        return []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_keyword_values(item))
        return values
    return [str(value).strip()]


def _unique_keywords(*values: Any) -> str | None:
    # Preserve source order while removing duplicates so prompts remain stable.
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        for keyword in _keyword_values(value):
            if keyword and keyword not in seen:
                seen.add(keyword)
                ordered.append(keyword)
    return "; ".join(ordered) or None


def _author_fields(value: Any) -> tuple[list[str], list[str]]:
    # Author IDs must remain strings; numeric conversion would break exact joins.
    author_ids: list[str] = []
    names: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            raw_id = (
                item.get("@auid")
                or item.get("auid")
                or _text(item.get("dc:identifier")).replace("AUTHOR_ID:", "")
            )
            preferred = item.get("preferred-name") or {}
            name = (
                item.get("ce:indexed-name")
                or item.get("authname")
                or _text(preferred.get("ce:indexed-name"))
                or _text(preferred.get("ce:surname"))
            )
            if raw_id:
                author_ids.append(str(raw_id).strip())
            if name:
                names.append(str(name).strip())
        else:
            names.append(_text(item))
    return author_ids, names


def _record_fields(record: dict[str, Any]) -> dict[str, Any]:
    # Normalize the union of Search and Abstract Retrieval field names into one
    # intermediate shape before constructing a Publication.
    eid = _text(record.get("eid")) or _text(record.get("dc:identifier"))
    author_ids, author_names = _author_fields(record.get("author"))
    return {
        "eid": eid.replace("SCOPUS_ID:", "").strip(),
        "doi": _text(record.get("prism:doi") or record.get("doi")) or None,
        "title": _clean_text(record.get("dc:title")),
        "year": _year(record.get("prism:coverDate") or record.get("prism:coverDisplayDate")),
        "source_title": _clean_text(record.get("prism:publicationName")),
        "document_type": _clean_text(record.get("subtypeDescription")),
        "publication_stage": _clean_text(record.get("publicationStatus")),
        "cited_by": _to_int(record.get("citedby-count")),
        "open_access": _to_int(record.get("openaccess")),
        "abstract": _clean_text(record.get("dc:description")),
        "author_keywords": _unique_keywords(record.get("authkeywords")),
        "index_keywords": _unique_keywords(
            record.get("idxterms"), record.get("indexkeywords")
        ),
        "author_ids": author_ids,
        "author_names": "; ".join(author_names),
        "provenance_url": _text(record.get("prism:url")) or None,
    }


def _make_publication(
    fields: dict[str, Any],
    eligible_faculty: dict[str, Faculty],
) -> Publication:
    author_ids = [str(value) for value in fields.get("author_ids", []) if value]
    # Attribution is deterministic and uses complete IDs only.
    faculty_ids = attribute_faculty_ids(author_ids, eligible_faculty)
    abstract = fields.get("abstract") or None
    flags: list[str] = []
    if abstract is None:
        flags.append("no_abstract")
    if not faculty_ids:
        flags.append("unattributed")
    # Keep the EID available even when only the SCOPUS_ID form was supplied.
    eid = str(fields.get("eid") or "")
    return Publication(
        eid=eid,
        doi=fields.get("doi") or None,
        title=str(fields.get("title") or "").strip(),
        year=int(fields.get("year") or 0),
        source_title=str(fields.get("source_title") or ""),
        document_type=str(fields.get("document_type") or ""),
        publication_stage=str(fields.get("publication_stage") or ""),
        cited_by=fields.get("cited_by"),
        open_access=fields.get("open_access"),
        abstract=abstract,
        author_keywords=fields.get("author_keywords"),
        index_keywords=fields.get("index_keywords"),
        author_ids=author_ids,
        author_names=str(fields.get("author_names") or ""),
        faculty_ids=faculty_ids,
        is_attributed=bool(faculty_ids),
        provenance_url=fields.get("provenance_url")
        or (
            f"https://www.scopus.com/record/display.uri?eid={eid}"
            if eid
            else None
        ),
        canonical_eid=eid,
        alias_eids=[eid] if eid else [],
        data_quality_flags=flags,
    )


def search_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Ignore error entries and entries with no usable identifier; they cannot be
    # cited or deduplicated.
    results = payload.get("search-results") or {}
    entries = results.get("entry") or payload.get("entry")
    return [
        entry
        for entry in _as_list(entries)
        if isinstance(entry, dict)
        and "error" not in entry
        and (entry.get("eid") or entry.get("dc:identifier"))
    ]


def publication_from_search_entry(
    entry: dict[str, Any],
    eligible_faculty: dict[str, Faculty],
) -> Publication:
    # Search metadata is enough for a preview but usually lacks an abstract.
    return _make_publication(_record_fields(entry), eligible_faculty)


def publications_from_search(
    payload: dict[str, Any],
    eligible_faculty: dict[str, Faculty],
) -> list[Publication]:
    return [
        publication_from_search_entry(entry, eligible_faculty)
        for entry in search_entries(payload)
    ]


def publication_from_abstract(
    payload: dict[str, Any],
    eligible_faculty: dict[str, Faculty],
) -> Publication:
    # Abstract Retrieval nests coredata, authors, and keywords differently from
    # Search; merge them into the same Publication contract.
    root = payload.get("abstracts-retrieval-response") or payload
    coredata = root.get("coredata") or payload.get("coredata") or {}
    fields = _record_fields(coredata)
    authors_value = root.get("authors")
    author_value = (
        authors_value.get("author")
        if isinstance(authors_value, dict)
        else authors_value
    )
    author_ids, names = _author_fields(author_value)
    if author_ids:
        fields["author_ids"] = author_ids
    if names:
        fields["author_names"] = "; ".join(names)
    fields["author_keywords"] = _unique_keywords(
        root.get("authkeywords"), coredata.get("authkeywords")
    )
    fields["index_keywords"] = _unique_keywords(
        root.get("idxterms"),
        root.get("indexkeywords"),
        coredata.get("idxterms"),
    )
    # Prefer the full abstract from coredata, then any top-level description, and
    # finally whatever the search-level record carried.
    fields["abstract"] = _clean_text(
        coredata.get("dc:description")
        or root.get("dc:description")
        or fields.get("abstract")
    )
    if not fields["eid"]:
        fields["eid"] = _text(root.get("eid"))
    return _make_publication(fields, eligible_faculty)


class ElsevierAbstractCache:
    """Revision-free payload cache keyed by EID with a configurable TTL."""

    # EIDs are hashed for filenames so unusual characters cannot escape the cache
    # directory or collide on case-insensitive filesystems.
    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl_hours: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path or config.ELSEVIER_CACHE_DIR
        ttl = config.ELSEVIER_CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
        self.ttl_seconds = max(0.0, float(ttl) * 3600)
        self.clock = clock

    def _entry_path(self, eid: str) -> Path:
        digest = hashlib.sha256(eid.encode("utf-8")).hexdigest()
        return self.path / f"{digest}.json"

    def get(self, eid: str) -> dict[str, Any] | None:
        # Corrupt or foreign entries are a cache miss, never a hard error.
        path = self._entry_path(eid)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(entry, dict) or entry.get("eid") != eid:
            return None
        fetched_at = entry.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        # A TTL of zero disables expiry (used by tests that assert reuse).
        if self.ttl_seconds and self.clock() - float(fetched_at) > self.ttl_seconds:
            return None
        payload = entry.get("payload")
        return payload if isinstance(payload, dict) else None

    def put(self, eid: str, payload: dict[str, Any]) -> None:
        # Store the original payload so parsing changes can be replayed offline.
        write_json_atomic(
            self._entry_path(eid),
            {
                "eid": eid,
                "fetched_at": self.clock(),
                "payload": payload,
            },
        )


class ElsevierClient:
    """Key-gated Scopus Search and Abstract Retrieval client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        inst_token: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
        cache: ElsevierAbstractCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # The key is read here and sent only in request headers; it is never part
        # of a URL, log message, or persisted artifact.
        self.api_key = api_key or config.optional_env("ELSEVIER_API_KEY")
        if not self.api_key:
            raise ElsevierNotConfigured(
                "ELSEVIER_API_KEY is not configured; use CSV source or add the key."
            )
        self.inst_token = inst_token or config.optional_env("ELSEVIER_INSTTOKEN")
        self.timeout = int(
            config.ELSEVIER_HTTP_TIMEOUT_SECONDS if timeout is None else timeout
        )
        self.max_retries = int(
            config.ELSEVIER_MAX_RETRIES if max_retries is None else max_retries
        )
        self.retry_base_seconds = (
            config.ELSEVIER_RETRY_BASE_SECONDS
            if retry_base_seconds is None
            else retry_base_seconds
        )
        self.retry_max_seconds = (
            config.ELSEVIER_RETRY_MAX_SECONDS
            if retry_max_seconds is None
            else retry_max_seconds
        )
        self.cache = cache if cache is not None else ElsevierAbstractCache()
        self.sleep = sleep

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        # Retry-After is authoritative; otherwise use capped exponential backoff.
        if retry_after:
            try:
                return min(float(retry_after), self.retry_max_seconds)
            except ValueError:
                pass
        base = self.retry_base_seconds * (2**attempt)
        return min(base + random.uniform(0, 0.25), self.retry_max_seconds)

    def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or {})
        full_url = f"{url}?{query}" if query else url
        headers = {
            "X-ELS-APIKey": self.api_key,
            "Accept": "application/json",
        }
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token

        last_error: Exception | None = None
        # ``max_retries`` counts retries in addition to the initial attempt.
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(full_url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                # Missing entitlement/key will not fix itself; fail before any
                # quota is wasted on repeated authorization attempts.
                if exc.code in {401, 403}:
                    raise ElsevierAuthError(
                        "Elsevier request was not authorized (HTTP "
                        f"{exc.code}); check the API key and institutional token."
                    ) from exc
                # 429 means quota/rate pressure. Honor Retry-After, then give up
                # with a distinct error the CLI can report clearly.
                if exc.code == 429:
                    if attempt >= self.max_retries:
                        raise ElsevierQuotaError(
                            "Elsevier quota exhausted (HTTP 429) after bounded retries."
                        ) from exc
                    self.sleep(self._delay(attempt, exc.headers.get("Retry-After")))
                    continue
                # Gateway/transient server errors are safe to retry within the cap.
                if exc.code in {500, 502, 503, 504} and attempt < self.max_retries:
                    self.sleep(self._delay(attempt, exc.headers.get("Retry-After")))
                    continue
                raise ElsevierAPIError(
                    f"Elsevier request failed with HTTP {exc.code}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleep(self._delay(attempt, None))
            except json.JSONDecodeError as exc:
                raise ElsevierAPIError("Elsevier returned invalid JSON") from exc
        raise ElsevierAPIError("Elsevier request could not connect") from last_error

    def search(
        self,
        query: str,
        *,
        start: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return self._get_json(
            SEARCH_URL,
            {
                "query": query,
                "start": start,
                "count": page_size,
                "httpAccept": "application/json",
            },
        )

    def fetch_abstract(self, eid: str) -> dict[str, Any]:
        return self._get_json(
            ABSTRACT_URL.format(eid=urllib.parse.quote(eid, safe="")),
            {"httpAccept": "application/json"},
        )

    def abstract_for_eid(self, eid: str) -> tuple[dict[str, Any], bool]:
        # Returns (payload, cache_hit) so callers can report quota savings.
        cached = self.cache.get(eid)
        if cached is not None:
            return cached, True
        payload = self.fetch_abstract(eid)
        self.cache.put(eid, payload)
        return payload, False

    def iter_author_publications(
        self,
        scopus_id: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_results: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        start = 0
        yielded = 0
        # Stop on a short page, an empty page, or the explicit result cap.
        while True:
            payload = self.search(
                f"AU-ID({scopus_id})",
                start=start,
                page_size=page_size,
            )
            entries = search_entries(payload)
            if not entries:
                return
            for entry in entries:
                yield entry
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            if len(entries) < page_size:
                return
            start += page_size

    def publications_for_faculty(
        self,
        faculty: Faculty,
        eligible_faculty: dict[str, Faculty],
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_results: int | None = None,
        fetch_abstracts: bool = True,
    ) -> list[Publication]:
        publications: list[Publication] = []
        for entry in self.iter_author_publications(
            faculty.scopus_id,
            page_size=page_size,
            max_results=max_results,
        ):
            # Fall back to dc:identifier when the search entry uses the Scopus
            # prefix form instead of a bare EID.
            eid = _text(entry.get("eid")) or _text(
                entry.get("dc:identifier")
            ).replace("SCOPUS_ID:", "").strip()
            if fetch_abstracts and eid:
                try:
                    payload, _ = self.abstract_for_eid(eid)
                except (ElsevierAuthError, ElsevierQuotaError):
                    # Credential/quota problems affect every record; stop rather
                    # than silently producing an index missing abstracts.
                    raise
                except ElsevierAPIError:
                    publication = publication_from_search_entry(
                        entry, eligible_faculty
                    )
                    publication.data_quality_flags.append("abstract_fetch_failed")
                else:
                    publication = publication_from_abstract(
                        payload, eligible_faculty
                    )
            else:
                publication = publication_from_search_entry(entry, eligible_faculty)
            publications.append(publication)
        return publications


def load_elsevier_publications(
    faculty: list[Faculty],
    *,
    client: ElsevierClient | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_results_per_faculty: int | None = None,
    fetch_abstracts: bool = True,
    progress: Callable[[int, int, str, int], None] | None = None,
) -> list[Publication]:
    """Collect and deduplicate publications for every eligible roster author."""
    eligible_faculty = build_eligible_faculty_index(faculty)
    client = client or ElsevierClient()
    by_eid: dict[str, Publication] = {}
    # Sorting makes fetch order and progress output deterministic.
    scopus_ids = sorted(eligible_faculty)
    for index, scopus_id in enumerate(scopus_ids, start=1):
        publications = client.publications_for_faculty(
            eligible_faculty[scopus_id],
            eligible_faculty,
            page_size=page_size,
            max_results=max_results_per_faculty,
            fetch_abstracts=fetch_abstracts,
        )
        for publication in publications:
            if not publication.eid:
                continue
            # The same work can be returned for multiple co-authors. Prefer the
            # version that actually has an abstract.
            existing = by_eid.get(publication.eid)
            if existing is None or (
                existing.abstract is None and publication.abstract is not None
            ):
                by_eid[publication.eid] = publication
        if progress is not None:
            progress(index, len(scopus_ids), scopus_id, len(by_eid))
    return [by_eid[eid] for eid in sorted(by_eid)]
