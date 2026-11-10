"""Load and validate the read-only faculty and publication CSVs."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import config
from models import Faculty, Publication
from src.research.identity import (
    attribute_faculty_ids,
    find_conflict_ids,
    parse_author_ids,
)


def sha256_file(path: Path) -> str:
    # Streaming avoids loading the 49 MB publication CSV into memory just to hash
    # it for manifest compatibility checks.
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _to_int(value: str | None) -> int | None:
    # CSV fields are strings; malformed or blank metrics become null so scoring
    # can redistribute the missing weight.
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    # Preserve nulls for percentages instead of coercing them to zero.
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_faculty(path: Path | None = None) -> list[Faculty]:
    """Load the roster and flag duplicate Scopus IDs as identity conflicts."""
    path = path or (config.RAW_DATA_DIR / "authors.csv")
    # utf-8-sig strips the BOM present in the supplied CSV without altering data.
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No faculty rows found in {path}")

    # Resolve conflicts before building records so both people sharing an ID are
    # excluded from automatic selection.
    conflict_ids = find_conflict_ids([row["Scopus ID"].strip() for row in rows])
    faculty: list[Faculty] = []
    for row in rows:
        scopus_id = row["Scopus ID"].strip()
        flags: list[str] = []
        if scopus_id in conflict_ids:
            flags.append("identity_conflict")
        metric_fields = {
            "h_index": _to_int(row.get("H-Index")),
            "h5_index": _to_int(row.get("H5 Index")),
            "scholarly_output": _to_int(row.get("Scholarly Output")),
            "citation_count": _to_int(row.get("Citation Count")),
            "corporate_collaboration_pct": _to_float(
                row.get("Corporate Collaboration %")
            ),
            "international_collaboration_pct": _to_float(
                row.get("International Collaboration %")
            ),
            "top_journal_quartile_pct": _to_float(
                row.get("Top Journal Quartile %")
            ),
        }
        # Record missing metrics explicitly; downstream scoring must not impute.
        for name, value in metric_fields.items():
            if value is None:
                flags.append(f"missing_{name}")

        faculty.append(
            Faculty(
                scopus_id=scopus_id,
                author_name=row.get("Author", "").strip(),
                department=row.get("Department", "").strip(),
                college=row.get("College", "").strip(),
                rank=row.get("Rank", "").strip(),
                tenure=row.get("Tenure", "").strip(),
                appointment_type=row.get("Appointment Type", "").strip(),
                functional_title=row.get("Functional Title", "").strip(),
                hire_date=row.get("Hire Date", "").strip(),
                h_index=metric_fields["h_index"],
                h5_index=metric_fields["h5_index"],
                scholarly_output=metric_fields["scholarly_output"],
                citation_count=metric_fields["citation_count"],
                corporate_collaboration_pct=metric_fields[
                    "corporate_collaboration_pct"
                ],
                international_collaboration_pct=metric_fields[
                    "international_collaboration_pct"
                ],
                top_journal_quartile_pct=metric_fields[
                    "top_journal_quartile_pct"
                ],
                identity_conflict=scopus_id in conflict_ids,
                eligible_for_selection=scopus_id not in conflict_ids,
                data_quality_flags=flags,
            )
        )
    return faculty


def load_publications(
    eligible_faculty: dict[str, Faculty],
    path: Path | None = None,
) -> list[Publication]:
    """Load raw publication rows and attach exact roster attribution."""
    path = path or (config.RAW_DATA_DIR / "publications.csv")
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No publication rows found in {path}")

    publications: list[Publication] = []
    for row in rows:
        # Rows without an EID or title cannot be cited or deduplicated reliably.
        eid = (row.get("EID") or "").strip()
        title = (row.get("Title") or "").strip()
        if not eid or not title:
            continue
        author_ids = parse_author_ids(row.get("Author(s) ID"))
        faculty_ids = attribute_faculty_ids(author_ids, eligible_faculty)
        abstract = (row.get("Abstract") or "").strip() or None
        # Flags travel with the record into the index manifest and evidence UI.
        flags: list[str] = []
        if abstract is None:
            flags.append("no_abstract")
        if not faculty_ids:
            flags.append("unattributed")
        publications.append(
            Publication(
                eid=eid,
                doi=(row.get("DOI") or "").strip() or None,
                title=title,
                year=_to_int(row.get("Year")) or 0,
                source_title=(row.get("Source title") or "").strip(),
                document_type=(row.get("Document Type") or "").strip(),
                publication_stage=(row.get("Publication Stage") or "").strip(),
                cited_by=_to_int(row.get("Cited by")),
                open_access=_to_int(row.get("Open Access")),
                abstract=abstract,
                author_keywords=(row.get("Author Keywords") or "").strip() or None,
                index_keywords=(row.get("Index Keywords") or "").strip() or None,
                author_ids=author_ids,
                author_names=(row.get("Author full names") or "").strip(),
                faculty_ids=faculty_ids,
                is_attributed=bool(faculty_ids),
                provenance_url=(row.get("Link") or "").strip() or None,
                canonical_eid=eid,
                alias_eids=[eid],
                data_quality_flags=flags,
            )
        )
    return publications
