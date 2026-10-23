from __future__ import annotations

import pytest

import config
from models import IndexManifest, Publication
from src.research.index import (
    IndexMismatchError,
    assert_index_compatible,
    index_compatibility_issues,
    select_index_publications,
)


def _manifest(**overrides) -> IndexManifest:
    data = {
        "index_version": config.INDEX_VERSION,
        "collection_name": config.COLLECTION_NAME,
        "schema_version": config.SCHEMA_VERSION,
        "chunker_version": config.CHUNKER_VERSION,
        "embedding_model": "qwen3-embedding:8b",
        "corpus_sha256": "corpus",
        "authors_sha256": "authors",
        "publications_sha256": "publications",
        "publication_rows": 2,
        "canonical_publications": 1,
        "attributed_publications": 1,
        "selected_publications": 1,
        "faculty_rows": 1,
        "unique_faculty": 1,
        "conflict_ids": 0,
        "publication_chunks": 1,
        "faculty_chunks": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    data.update(overrides)
    return IndexManifest(**data)


def test_compatible_manifest_has_no_issues():
    issues = index_compatibility_issues(
        _manifest(),
        expected_model="qwen3-embedding:8b",
        expected_corpus_hash="corpus",
    )
    assert issues == []


def test_every_incompatibility_is_reported():
    issues = index_compatibility_issues(
        _manifest(
            index_version="old",
            collection_name="old_collection",
            schema_version="0",
            chunker_version="0",
            embedding_model="different-model",
            corpus_sha256="stale",
        ),
        expected_model="qwen3-embedding:8b",
        expected_corpus_hash="fresh",
    )
    assert len(issues) == 6
    assert any("index version" in issue for issue in issues)
    assert any("schema" in issue for issue in issues)
    assert any("chunker" in issue for issue in issues)
    assert any("embedding model" in issue for issue in issues)
    assert any("corpus data changed" in issue for issue in issues)


def test_mismatch_raises_clear_rebuild_instruction():
    with pytest.raises(IndexMismatchError) as excinfo:
        assert_index_compatible(
            _manifest(schema_version="0"),
            expected_model="qwen3-embedding:8b",
        )
    assert "scripts/build_index.py --rebuild" in str(excinfo.value)


def _publication(eid: str, year: int, cited_by: int) -> Publication:
    return Publication(
        eid=eid,
        title=f"Title {eid}",
        year=year,
        cited_by=cited_by,
        faculty_ids=["111"],
        is_attributed=True,
    )


def test_selection_is_deterministic_and_limit_is_newest_first():
    publications = [
        _publication("e1", 2020, 100),
        _publication("e2", 2025, 1),
        _publication("e3", 2025, 50),
    ]
    assert [item.eid for item in select_index_publications(publications)] == [
        "e1",
        "e2",
        "e3",
    ]
    assert [
        item.eid for item in select_index_publications(publications, limit=2)
    ] == ["e2", "e3"]
    assert select_index_publications(publications, limit=0) == []
