"""Tests for timestamp-based leakage prevention."""

from __future__ import annotations

from datetime import UTC, datetime

from upset_profiler.models.research import SourceDocument
from upset_profiler.security.leakage import LeakageFilter


def _document(
    document_id: str,
    published_at: datetime | None,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        game_id="game-1",
        url="https://www.espn.com/story",
        source_domain="espn.com",
        published_at=published_at,
        retrieved_at=datetime(2025, 2, 1, tzinfo=UTC),
        content="fixture content",
    )


def test_strict_filter_excludes_undated_and_post_tipoff_documents() -> None:
    filter_ = LeakageFilter(strict=True)
    tipoff = datetime(2025, 2, 1, 19, 0, tzinfo=UTC)
    assert filter_.is_visible(datetime(2025, 2, 1, 18, 59, tzinfo=UTC), tipoff)
    assert not filter_.is_visible(tipoff, tipoff)
    assert not filter_.is_visible(datetime(2025, 2, 1, 19, 1, tzinfo=UTC), tipoff)
    assert not filter_.is_visible(None, tipoff)


def test_non_strict_filter_admits_undated_but_not_post_tipoff() -> None:
    filter_ = LeakageFilter(strict=False)
    tipoff = datetime(2025, 2, 1, 19, 0, tzinfo=UTC)
    assert filter_.is_visible(None, tipoff)
    assert not filter_.is_visible(datetime(2025, 2, 1, 20, 0, tzinfo=UTC), tipoff)


def test_partition_documents_preserves_order() -> None:
    tipoff = datetime(2025, 2, 1, 19, 0, tzinfo=UTC)
    documents = [
        _document("before", datetime(2025, 2, 1, 12, 0, tzinfo=UTC)),
        _document("after", datetime(2025, 2, 2, 12, 0, tzinfo=UTC)),
        _document("undated", None),
    ]
    visible, excluded = LeakageFilter(strict=True).partition_documents(documents, tipoff)
    assert [document.document_id for document in visible] == ["before"]
    assert [document.document_id for document in excluded] == ["after", "undated"]
