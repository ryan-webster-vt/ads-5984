"""Timestamp-based leakage prevention for historical research.

Requirement R24 forbids information known after tip-off from entering research
or monitoring. The check is intentionally deterministic: publication timestamps
are compared against the game's tip-off time in UTC, with no LLM judgment.
"""

from __future__ import annotations

from dataclasses import dataclass

from upset_profiler.models.common import UtcDatetime
from upset_profiler.models.research import SourceDocument


@dataclass(frozen=True)
class LeakageFilter:
    """Decide whether information was knowable before a game started."""

    strict: bool = True

    def is_visible(self, published_at: UtcDatetime | None, tipoff_at: UtcDatetime) -> bool:
        """Return whether a publication time precedes tip-off.

        In strict mode a missing timestamp is treated as unknown and excluded.
        Non-strict mode admits undated material but still rejects explicit
        post-tip-off timestamps.
        """
        if published_at is None:
            return not self.strict
        return published_at < tipoff_at

    def is_chunk_visible(
        self,
        published_at: UtcDatetime | None,
        tipoff_at: UtcDatetime,
    ) -> bool:
        """Return visibility for a chunk, mirroring :meth:`is_visible`."""
        return self.is_visible(published_at, tipoff_at)

    def partition_documents(
        self,
        documents: list[SourceDocument],
        tipoff_at: UtcDatetime,
    ) -> tuple[list[SourceDocument], list[SourceDocument]]:
        """Split documents into visible and excluded lists, preserving order."""
        visible: list[SourceDocument] = []
        excluded: list[SourceDocument] = []
        for document in documents:
            if self.is_visible(document.published_at, tipoff_at):
                visible.append(document)
            else:
                excluded.append(document)
        return visible, excluded
