# Offline test module: Elsevier parsing, retries, quota handling, caching, and source switching.
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import config
from models import Faculty, Publication
from src.research.elsevier import (
    ElsevierAbstractCache,
    ElsevierAuthError,
    ElsevierClient,
    ElsevierNotConfigured,
    ElsevierQuotaError,
    load_elsevier_publications,
    publication_from_abstract,
    publications_from_search,
)
from src.research.identity import build_eligible_faculty_index
from src.research.loaders import load_faculty


@pytest.fixture(scope="module")
def eligible_faculty():
    return build_eligible_faculty_index(load_faculty())


def _fixture(name: str) -> dict:
    return json.loads((config.FIXTURES_DIR / name).read_text(encoding="utf-8"))


# Verifies: search fixture produces publication records.
def test_search_fixture_produces_publication_records(eligible_faculty):
    publications = publications_from_search(
        _fixture("elsevier_search.json"), eligible_faculty
    )
    assert len(publications) == 2
    first = publications[0]
    assert isinstance(first, Publication)
    assert first.eid == "2-s2.0-85190000001"
    assert first.doi == "10.1000/elsevier.fixture.2024.001"
    assert first.title == "Long-term ecological synthesis of lake carbon dynamics"
    assert first.year == 2024
    assert first.cited_by == 42
    assert first.open_access == 1
    assert first.faculty_ids == ["24553952400", "7403498714"]
    assert first.is_attributed is True
    assert "no_abstract" in first.data_quality_flags

    second = publications[1]
    assert second.faculty_ids == []
    assert second.is_attributed is False
    assert "unattributed" in second.data_quality_flags


# Verifies: abstract fixture matches publication contract.
def test_abstract_fixture_matches_publication_contract(eligible_faculty):
    publication = publication_from_abstract(
        _fixture("elsevier_abstract.json"), eligible_faculty
    )
    assert isinstance(publication, Publication)
    assert set(publication.model_dump()) == set(Publication.model_fields)
    assert publication.eid == "2-s2.0-85190000001"
    assert publication.year == 2024
    assert publication.faculty_ids == ["24553952400", "7403498714"]
    assert publication.abstract is not None
    assert "<p>" not in publication.abstract
    assert "two decades of lake carbon observations" in publication.abstract
    assert "ecological synthesis" in (publication.author_keywords or "")
    assert "Bayesian state-space model" in (publication.index_keywords or "")
    assert publication.provenance_url and "scopus.com" in publication.provenance_url


# Verifies: client requires an API key.
def test_client_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    with pytest.raises(ElsevierNotConfigured):
        ElsevierClient(api_key=None)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        "https://api.elsevier.com", code, "error", headers, None
    )


def _client(tmp_path, **kwargs) -> ElsevierClient:
    cache = ElsevierAbstractCache(tmp_path / "cache", ttl_hours=1)
    return ElsevierClient(api_key="test-key", cache=cache, **kwargs)


# Verifies: 401 fails fast without retry.
def test_401_fails_fast_without_retry(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        raise _http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(tmp_path, max_retries=3, sleep=lambda _: None)
    with pytest.raises(ElsevierAuthError):
        client.search("AU-ID(123)")
    assert len(calls) == 1


# Verifies: 429 respects retry after then succeeds.
def test_429_respects_retry_after_then_succeeds(monkeypatch, tmp_path):
    payload = _fixture("elsevier_search.json")
    responses = [_http_error(429, "2"), _FakeResponse(payload)]
    calls = []
    sleeps: list[float] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(tmp_path, max_retries=2, sleep=sleeps.append)
    assert client.search("AU-ID(123)") == payload
    assert len(calls) == 2
    assert sleeps == [2.0]


# Verifies: 429 exhaustion raises quota error.
def test_429_exhaustion_raises_quota_error(monkeypatch, tmp_path):
    calls = []
    sleeps: list[float] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        raise _http_error(429, "0")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(tmp_path, max_retries=2, sleep=sleeps.append)
    with pytest.raises(ElsevierQuotaError):
        client.search("AU-ID(123)")
    assert len(calls) == 3
    assert len(sleeps) == 2


# Verifies: abstract cache avoids duplicate quota use.
def test_abstract_cache_avoids_duplicate_quota_use(monkeypatch, tmp_path):
    payload = _fixture("elsevier_abstract.json")
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(tmp_path)

    first, first_cached = client.abstract_for_eid("2-s2.0-85190000001")
    second, second_cached = client.abstract_for_eid("2-s2.0-85190000001")

    assert first == payload
    assert second == payload
    assert first_cached is False
    assert second_cached is True
    assert len(calls) == 1


# Verifies: abstract cache expires.
def test_abstract_cache_expires(tmp_path):
    now = [1000.0]
    cache = ElsevierAbstractCache(
        tmp_path / "cache", ttl_hours=1, clock=lambda: now[0]
    )
    cache.put("eid-1", {"ok": True})
    assert cache.get("eid-1") == {"ok": True}
    now[0] += 3601
    assert cache.get("eid-1") is None


# Verifies: load elsevier publications deduplicates across faculty.
def test_load_elsevier_publications_deduplicates_across_faculty():
    faculty = [
        Faculty(scopus_id="111", author_name="A", department="Biology"),
        Faculty(scopus_id="222", author_name="B", department="Biology"),
    ]
    shared = Publication(
        eid="e1",
        title="Shared work",
        year=2024,
        faculty_ids=["111", "222"],
        is_attributed=True,
    )
    unique = Publication(
        eid="e2",
        title="Unique work",
        year=2023,
        faculty_ids=["222"],
        is_attributed=True,
    )

    class FakeClient:
        def publications_for_faculty(self, member, eligible, **kwargs):
            return [shared] if member.scopus_id == "111" else [shared, unique]

    progress = []
    publications = load_elsevier_publications(
        faculty,
        client=FakeClient(),
        progress=lambda *args: progress.append(args),
    )
    assert [publication.eid for publication in publications] == ["e1", "e2"]
    assert len(progress) == 2


# Verifies: publication corpus hash is order independent.
def test_publication_corpus_hash_is_order_independent():
    from src.research.index import publication_corpus_hash

    first = Publication(eid="e1", title="A", year=2024)
    second = Publication(eid="e2", title="B", year=2023)
    assert publication_corpus_hash([first, second]) == publication_corpus_hash(
        [second, first]
    )


# Verifies: publication source defaults to CSV and validates.
def test_publication_source_defaults_to_csv_and_validates(monkeypatch):
    monkeypatch.delenv("PUBLICATION_SOURCE", raising=False)
    assert config.configured_publication_source() == "csv"
    monkeypatch.setenv("PUBLICATION_SOURCE", "elsevier")
    assert config.configured_publication_source() == "elsevier"
    monkeypatch.setenv("PUBLICATION_SOURCE", "neo4j")
    with pytest.raises(RuntimeError):
        config.configured_publication_source()


# Verifies: index loader uses elsevier source.
def test_index_loader_uses_elsevier_source(monkeypatch):
    from src.research import index as index_module

    roster = [
        Faculty(scopus_id="111", author_name="A", department="Biology"),
        Faculty(scopus_id="222", author_name="B", department="Biology"),
    ]
    publication = Publication(
        eid="e1",
        title="Shared work",
        year=2024,
        faculty_ids=["111"],
        is_attributed=True,
    )

    class FakeClient:
        def publications_for_faculty(self, member, eligible, **kwargs):
            return [publication]

    monkeypatch.setattr(index_module, "load_faculty", lambda: roster)
    monkeypatch.setattr(index_module, "sha256_file", lambda path: "authors-hash")

    data = index_module._load_research_data(
        "elsevier", None, elsevier_client=FakeClient()
    )
    assert [item.eid for item in data.raw_publications] == ["e1"]
    assert data.authors_hash == "authors-hash"
    assert data.corpus_hash == f"authors-hash:{data.publications_hash}"
    assert data.selected[0].eid == "e1"
