"""Deterministic identity helpers for exact Scopus ID matching."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from models import Faculty


def parse_author_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [token.strip() for token in raw.split(";") if token.strip()]


def find_conflict_ids(scopus_ids: list[str]) -> set[str]:
    counts = Counter(scopus_ids)
    return {scopus_id for scopus_id, count in counts.items() if count > 1}


def build_eligible_faculty_index(faculty: list[Faculty]) -> dict[str, Faculty]:
    return {
        member.scopus_id: member
        for member in faculty
        if member.eligible_for_selection and not member.identity_conflict
    }


def attribute_faculty_ids(
    author_ids: list[str], eligible_faculty: dict[str, Faculty]
) -> list[str]:
    """Intersect complete IDs; never substring-match or fuzzy-join names."""
    return sorted({author_id for author_id in author_ids if author_id in eligible_faculty})


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z]+", " ", name.lower()).strip()


def conflict_groups(faculty: list[Faculty]) -> dict[str, list[Faculty]]:
    grouped: dict[str, list[Faculty]] = defaultdict(list)
    for member in faculty:
        grouped[member.scopus_id].append(member)
    return {
        scopus_id: members
        for scopus_id, members in grouped.items()
        if len(members) > 1
    }
