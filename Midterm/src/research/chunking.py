"""Document-aware chunking for publications and faculty roster profiles."""

from __future__ import annotations

import hashlib
import re

import config
from models import EvidenceChunk, Faculty, Publication


def _content_hash(text: str) -> str:
    # A short prefix is enough to detect edits while keeping metadata compact.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _split_sentences(text: str) -> list[str]:
    # The lookbehind keeps terminal punctuation attached to the sentence.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def split_text_sentence_aware(
    text: str,
    max_chars: int = config.CHUNK_MAX_CHARS,
    overlap_chars: int = config.CHUNK_OVERLAP_CHARS,
    max_chunks: int = config.MAX_ABSTRACT_CHUNKS,
) -> list[str]:
    """Pack sentences into bounded windows without mixing documents."""
    text = " ".join(text.split())
    # Normalize whitespace before measuring so chunk sizes are comparable.
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    buffer = ""
    for sentence in sentences:
        # A single overlong sentence is hard-split; chunks never cross documents.
        while len(sentence) > max_chars:
            if buffer:
                chunks.append(buffer.strip())
                buffer = ""
            chunks.append(sentence[:max_chars].strip())
            sentence = sentence[max_chars:].strip()
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer.strip())
            # A small tail overlap preserves context across chunk boundaries.
            tail = buffer[-overlap_chars:] if overlap_chars else ""
            buffer = f"{tail} {sentence}".strip()
        else:
            buffer = sentence
        if len(chunks) >= max_chunks:
            break
    if buffer and len(chunks) < max_chunks:
        chunks.append(buffer.strip())
    return [chunk for chunk in chunks if chunk][:max_chunks]


def _publication_header(
    publication: Publication,
    faculty_names: dict[str, str],
) -> str:
    faculty = ", ".join(
        faculty_names.get(faculty_id, faculty_id)
        for faculty_id in publication.faculty_ids
    )
    return "\n".join(
        [
            "Publication evidence",
            f"Title: {publication.title}",
            f"Year: {publication.year}",
            f"Source: {publication.source_title or 'Unknown'}",
            f"Document type: {publication.document_type or 'Unknown'}",
            f"Faculty: {faculty or 'Unattributed'}",
            f"EID: {publication.eid}",
            f"DOI: {publication.doi or 'None'}",
        ]
    )


def chunk_publication(
    publication: Publication,
    faculty_names: dict[str, str],
    index_version: str = config.INDEX_VERSION,
) -> list[EvidenceChunk]:
    # The header repeats identity metadata in every chunk so each chunk remains
    # independently citable after retrieval.
    header = _publication_header(publication, faculty_names)
    body_parts = []
    if publication.abstract:
        body_parts.append(f"Abstract: {publication.abstract}")
    if publication.author_keywords:
        body_parts.append(f"Author keywords: {publication.author_keywords}")
    if publication.index_keywords:
        body_parts.append(f"Index keywords: {publication.index_keywords}")
    if not body_parts:
        # Missing evidence must stay explicit; never fabricate an abstract.
        body_parts.append(
            "No abstract or keywords are available for this publication; "
            "use the title and source metadata only."
        )

    body_chunks = split_text_sentence_aware(" ".join(body_parts))
    chunks: list[EvidenceChunk] = []
    for index, body in enumerate(body_chunks):
        text = f"{header}\n\n{body}"
        # Stable IDs make reruns idempotent and let citations resolve exactly.
        chunks.append(
            EvidenceChunk(
                evidence_id=f"pub::{publication.eid}::c{index:02d}",
                chunk_type="publication",
                parent_id=publication.eid,
                chunk_index=index,
                chunk_count=len(body_chunks),
                text=text,
                faculty_ids=publication.faculty_ids,
                title=publication.title,
                year=publication.year,
                source_title=publication.source_title,
                eid=publication.eid,
                doi=publication.doi,
                has_abstract=bool(publication.abstract),
                index_version=index_version,
                content_hash=_content_hash(text),
                provenance_url=publication.provenance_url,
            )
        )
    return chunks


def _metric(value: int | float | None) -> str:
    # Missing metrics are labeled unknown rather than imputed or shown as zero.
    return "unknown" if value is None else str(value)


def chunk_faculty_profile(
    faculty: Faculty,
    index_version: str = config.INDEX_VERSION,
) -> EvidenceChunk:
    """Profile chunks contain roster facts only, never invented biographies."""
    # Profile chunks contain roster facts and metric caveats only. They aid recall
    # but are not a substitute for publication-level evidence.
    text = "\n".join(
        [
            "Faculty roster facts",
            f"Name: {faculty.author_name}",
            f"Scopus ID: {faculty.scopus_id}",
            f"Department: {faculty.department}",
            f"College: {faculty.college}",
            f"Rank: {faculty.rank or 'unknown'}",
            f"Tenure: {faculty.tenure or 'unknown'}",
            f"Appointment type: {faculty.appointment_type or 'unknown'}",
            f"Functional title: {faculty.functional_title or 'unknown'}",
            f"H-index: {_metric(faculty.h_index)}",
            f"H5 index: {_metric(faculty.h5_index)}",
            f"Scholarly output: {_metric(faculty.scholarly_output)}",
            f"Citation count: {_metric(faculty.citation_count)}",
            "Top Journal Quartile %: "
            f"{_metric(faculty.top_journal_quartile_pct)} "
            "(author-level metric only)",
            "Corporate Collaboration %: "
            f"{_metric(faculty.corporate_collaboration_pct)} "
            "(author-level aggregate only)",
            "International Collaboration %: "
            f"{_metric(faculty.international_collaboration_pct)} "
            "(author-level aggregate only)",
            "Note: availability, leadership experience, and sponsor eligibility "
            "are not present in the roster.",
        ]
    )
    return EvidenceChunk(
        evidence_id=f"faculty::{faculty.scopus_id}::profile::v{config.PROFILE_VERSION}",
        chunk_type="faculty_profile",
        parent_id=faculty.scopus_id,
        chunk_index=0,
        chunk_count=1,
        text=text,
        faculty_ids=[faculty.scopus_id],
        title=faculty.author_name,
        year=None,
        source_title="VT College of Science roster",
        has_abstract=False,
        index_version=index_version,
        content_hash=_content_hash(text),
    )
