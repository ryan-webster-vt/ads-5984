"""Deterministic publication deduplication and canonical-record selection."""

from __future__ import annotations

import re
from collections import defaultdict

from models import Publication


def normalize_title(title: str) -> str:
    # Remove punctuation, spacing, and case so cosmetic differences do not create
    # duplicate works.
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def dedupe_key(publication: Publication) -> str:
    # DOI+title is preferred because the same DOI can be reused with different
    # titles; title+year is the fallback when no DOI exists.
    doi = (publication.doi or "").strip().lower()
    if doi:
        return f"doi:{doi}|{normalize_title(publication.title)}"
    return f"title:{normalize_title(publication.title)}|{publication.year}"


def _canonical_sort_key(publication: Publication) -> tuple[int, str]:
    # Prefer the most-cited record, then the lexicographically smallest EID as a
    # stable deterministic tie-break.
    return (-(publication.cited_by or 0), publication.eid)


def dedupe_publications(publications: list[Publication]) -> list[Publication]:
    """Collapse same-DOI/same-title or same-title/year records.

    Same-DOI records with different titles are intentionally kept separate and
    flagged, because they may represent distinct works or data errors.
    """
    # Detect same-DOI records whose titles disagree; these are flagged but not
    # silently collapsed into one work.
    doi_titles: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        if publication.doi:
            doi_titles[publication.doi.lower()].add(
                normalize_title(publication.title)
            )
    conflicting_dois = {
        doi for doi, titles in doi_titles.items() if len(titles) > 1
    }

    grouped: dict[str, list[Publication]] = defaultdict(list)
    for publication in publications:
        publication.dedupe_key = dedupe_key(publication)
        if (
            publication.doi
            and publication.doi.lower() in conflicting_dois
            and "doi_conflict" not in publication.data_quality_flags
        ):
            publication.data_quality_flags.append("doi_conflict")
        grouped[publication.dedupe_key].append(publication)

    canonical: list[Publication] = []
    for group in grouped.values():
        # Merge faculty attribution across aliases so no co-author is lost when a
        # duplicate record is collapsed.
        chosen = sorted(group, key=_canonical_sort_key)[0].model_copy(deep=True)
        alias_eids = sorted({member.eid for member in group})
        faculty_ids = sorted(
            {faculty_id for member in group for faculty_id in member.faculty_ids}
        )
        chosen.faculty_ids = faculty_ids
        chosen.is_attributed = bool(faculty_ids)
        chosen.canonical_eid = chosen.eid
        chosen.alias_eids = alias_eids
        if len(alias_eids) > 1:
            chosen.data_quality_flags.append("deduped_alias")
        canonical.append(chosen)

    return sorted(canonical, key=lambda publication: publication.eid)
