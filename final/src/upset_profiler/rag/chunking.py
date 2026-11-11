"""Deterministic recursive chunking for cited research documents.

Chunking happens within one document at a time so text never bleeds across
documents or games. Paragraph and sentence boundaries are preferred, with word
windows as the final fallback. Chunk identifiers are deterministic so repeated
ingestion upserts the same records instead of creating duplicates.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from upset_profiler.models.common import SeasonStr
from upset_profiler.models.evidence import DocumentChunk
from upset_profiler.models.research import SourceDocument

_WORD_PATTERN = re.compile(r"\S+")
_PARAGRAPH_PATTERN = re.compile(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


def count_tokens(text: str) -> int:
    """Return a deterministic whitespace-token count for chunk sizing."""
    return len(_WORD_PATTERN.findall(text))


@dataclass(frozen=True)
class ChunkingConfig:
    """Chunk-size and overlap controls measured in whitespace tokens."""

    chunk_size_tokens: int = 800
    overlap_tokens: int = 120

    def __post_init__(self) -> None:
        if self.chunk_size_tokens < 1:
            raise ValueError("chunk_size_tokens must be at least 1")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must be non-negative")
        if self.overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("overlap_tokens must be smaller than chunk_size_tokens")


class RecursiveChunker:
    """Split source documents into deterministic, structure-aware chunks."""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    @property
    def config(self) -> ChunkingConfig:
        """Return the active chunking configuration."""
        return self._config

    def chunk_document(
        self,
        document: SourceDocument,
        *,
        season: SeasonStr | None = None,
    ) -> list[DocumentChunk]:
        """Return chunks for one document, or an empty list when it has no text."""
        content = document.content or ""
        if not content.strip():
            return []

        document_hash = document.content_hash or hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
        spans = self._pack_spans(content, self._units(content))
        chunks: list[DocumentChunk] = []
        for start, end in spans:
            text = self._trimmed(content, start, end)
            if text is None:
                continue
            chunk_text, chunk_start, chunk_end = text
            chunk_index = len(chunks)
            digest = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:12]
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{document.document_id}:{chunk_index}:{digest}",
                    document_id=document.document_id,
                    game_id=document.game_id,
                    season=season,
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=count_tokens(chunk_text),
                    start_char=chunk_start,
                    end_char=chunk_end,
                    source_url=document.url,
                    source_domain=document.source_domain,
                    published_at=document.published_at,
                    whitelisted=True,
                    document_hash=document_hash,
                    metadata={
                        **document.metadata,
                        "retrieved_at": document.retrieved_at.isoformat(),
                    },
                )
            )
        return chunks

    def _units(self, content: str) -> list[tuple[int, int]]:
        units: list[tuple[int, int]] = []
        for paragraph in _PARAGRAPH_PATTERN.finditer(content):
            paragraph_text = paragraph.group(0)
            if not paragraph_text.strip():
                continue
            cursor = paragraph.start()
            for sentence in _SENTENCE_PATTERN.split(paragraph_text):
                if not sentence.strip():
                    cursor += len(sentence)
                    continue
                start = content.find(sentence, cursor, paragraph.end())
                if start < 0:
                    start = cursor
                end = start + len(sentence)
                cursor = end
                units.extend(
                    self._split_oversized(content, start, end)
                )
        return units

    def _split_oversized(
        self,
        content: str,
        start: int,
        end: int,
    ) -> list[tuple[int, int]]:
        text = content[start:end]
        if count_tokens(text) <= self._config.chunk_size_tokens:
            return [(start, end)]

        words = list(_WORD_PATTERN.finditer(text))
        windows: list[tuple[int, int]] = []
        step = self._config.chunk_size_tokens - self._config.overlap_tokens
        index = 0
        while index < len(words):
            window = words[index : index + self._config.chunk_size_tokens]
            windows.append(
                (start + window[0].start(), start + window[-1].end())
            )
            if index + self._config.chunk_size_tokens >= len(words):
                break
            index += step
        return windows

    def _pack_spans(
        self,
        content: str,
        units: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        current: list[tuple[int, int]] = []
        current_tokens = 0
        index = 0
        while index < len(units):
            unit = units[index]
            unit_tokens = count_tokens(content[unit[0] : unit[1]])
            if current and current_tokens + unit_tokens > self._config.chunk_size_tokens:
                spans.append((current[0][0], current[-1][1]))
                current, current_tokens = self._overlap(content, current)
                if current and current_tokens + unit_tokens > self._config.chunk_size_tokens:
                    current, current_tokens = [], 0
                continue
            current.append(unit)
            current_tokens += unit_tokens
            index += 1
        if current:
            spans.append((current[0][0], current[-1][1]))
        return self._dedupe(spans)

    def _overlap(
        self,
        content: str,
        current: list[tuple[int, int]],
    ) -> tuple[list[tuple[int, int]], int]:
        if self._config.overlap_tokens == 0:
            return [], 0
        overlap: list[tuple[int, int]] = []
        overlap_tokens = 0
        for unit in reversed(current):
            unit_tokens = count_tokens(content[unit[0] : unit[1]])
            if overlap_tokens + unit_tokens > self._config.overlap_tokens:
                break
            overlap.insert(0, unit)
            overlap_tokens += unit_tokens
        if overlap and overlap_tokens >= sum(
            count_tokens(content[unit[0] : unit[1]]) for unit in current
        ):
            overlap = overlap[1:]
            overlap_tokens = sum(
                count_tokens(content[unit[0] : unit[1]]) for unit in overlap
            )
        return overlap, overlap_tokens

    def _dedupe(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        deduped: list[tuple[int, int]] = []
        for span in spans:
            if not deduped or deduped[-1] != span:
                deduped.append(span)
        return deduped

    def _trimmed(
        self,
        content: str,
        start: int,
        end: int,
    ) -> tuple[str, int, int] | None:
        raw = content[start:end]
        stripped = raw.strip()
        if not stripped:
            return None
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        return stripped, start + leading, end - trailing
