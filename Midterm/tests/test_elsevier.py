from __future__ import annotations

import json

import pytest

import config
from models import Publication
from src.research.elsevier import (
    ElsevierClient,
    ElsevierNotConfigured,
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


def test_client_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    with pytest.raises(ElsevierNotConfigured):
        ElsevierClient(api_key=None)
