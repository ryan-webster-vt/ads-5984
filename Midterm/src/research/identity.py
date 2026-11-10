"""Deterministic identity helpers for exact Scopus ID matching."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from models import Faculty


def parse_author_ids(raw: str | None) -> list[str]:
    # Scopus stores complete author IDs as semicolon-delimited strings. Preserve
    # them verbatim as strings; numeric conversion could drop leading zeros.
    if not raw:
        return []
    return [token.strip() for token in raw.split(";") if token.strip()]


def find_conflict_ids(scopus_ids: list[str]) -> set[str]:
    # A repeated ID in the roster means two people share one Scopus identity.
    counts = Counter(scopus_ids)
    return {scopus_id for scopus_id, count in counts.items() if count > 1}


def build_eligible_faculty_index(faculty: list[Faculty]) -> dict[str, Faculty]:
    # Only unambiguous, explicitly eligible roster members may enter retrieval or
    # team selection.
    return {
        member.scopus_id: member
        for member in faculty
        if member.eligible_for_selection and not member.identity_conflict
    }


def attribute_faculty_ids(
    author_ids: list[str], eligible_faculty: dict[str, Faculty]
) -> list[str]:
    """Intersect complete IDs; never substring-match or fuzzy-join names."""
    # Exact set membership is deliberate: names are not unique and partial IDs
    # would create false attribution.
    return sorted({author_id for author_id in author_ids if author_id in eligible_faculty})


def normalize_name(name: str) -> str:
    # Used only for explicit user-facing name resolution, never for automatic
    # publication attribution.
    return re.sub(r"[^a-z]+", " ", name.lower()).strip()


def conflict_groups(faculty: list[Faculty]) -> dict[str, list[Faculty]]:
    # Kept separate from ``find_conflict_ids`` so callers can inspect both people
    # behind a duplicate ID when reporting the data-quality issue.
    grouped: dict[str, list[Faculty]] = defaultdict(list)
    for member in faculty:
        grouped[member.scopus_id].append(member)
    return {
        scopus_id: members
        for scopus_id, members in grouped.items()
        if len(members) > 1
    }
