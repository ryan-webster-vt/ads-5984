"""Optional Elsevier/Scopus adapter.

Phase 2 implements the response parsers and a key-gated client interface. The
parsers are exercised by synthetic fixtures only; live Elsevier access is not
claimed until a real key and institutional entitlement are available.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import config
from models import Faculty, Publication
from src.research.identity import attribute_faculty_ids

SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
ABSTRACT_URL = "https://api.elsevier.com/content/abstract/eid/{eid}"
DEFAULT_PAGE_SIZE = 25


class ElsevierNotConfigured(RuntimeError):
    """Raised when the optional Elsevier key is absent."""


class ElsevierAPIError(RuntimeError):
    """Raised for non-successful Elsevier responses without exposing secrets."""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("$", "value", "_"):
            if key in value:
                return _text(value[key])
        return ""
    return str(value).strip()


def _clean_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_int(value: Any) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _year(value: Any) -> int:
    match = re.search(r"\b(\d{4})\b", _text(value))
    return int(match.group(1)) if match else 0


def _keyword_values(value: Any) -> list[str]:
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
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        for keyword in _keyword_values(value):
            if keyword and keyword not in seen:
                seen.add(keyword)
                ordered.append(keyword)
    return "; ".join(ordered) or None


def _author_fields(value: Any) -> tuple[list[str], list[str]]:
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
    faculty_ids = attribute_faculty_ids(author_ids, eligible_faculty)
    abstract = fields.get("abstract") or None
    flags: list[str] = []
    if abstract is None:
        flags.append("no_abstract")
    if not faculty_ids:
        flags.append("unattributed")
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
    results = payload.get("search-results") or {}
    entries = results.get("entry") or payload.get("entry")
    return [entry for entry in _as_list(entries) if isinstance(entry, dict)]


def publication_from_search_entry(
    entry: dict[str, Any],
    eligible_faculty: dict[str, Faculty],
) -> Publication:
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
    fields["abstract"] = _clean_text(
        coredata.get("dc:description")
        or root.get("dc:description")
        or fields.get("abstract")
    )
    if not fields["eid"]:
        fields["eid"] = _text(root.get("eid"))
    return _make_publication(fields, eligible_faculty)


class ElsevierClient:
    """Key-gated Scopus Search and Abstract Retrieval client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        inst_token: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key or config.optional_env("ELSEVIER_API_KEY")
        if not self.api_key:
            raise ElsevierNotConfigured(
                "ELSEVIER_API_KEY is not configured; use CSV source or add the key."
            )
        self.inst_token = inst_token or config.optional_env("ELSEVIER_INSTTOKEN")
        self.timeout = timeout

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
        request = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ElsevierAPIError(
                f"Elsevier request failed with HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ElsevierAPIError("Elsevier request could not connect") from exc
        except json.JSONDecodeError as exc:
            raise ElsevierAPIError("Elsevier returned invalid JSON") from exc

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

    def iter_author_publications(
        self,
        scopus_id: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_results: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        start = 0
        yielded = 0
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
            if fetch_abstracts and _text(entry.get("eid")):
                payload = self.fetch_abstract(_text(entry.get("eid")))
                publications.append(
                    publication_from_abstract(payload, eligible_faculty)
                )
            else:
                publications.append(
                    publication_from_search_entry(entry, eligible_faculty)
                )
        return publications
