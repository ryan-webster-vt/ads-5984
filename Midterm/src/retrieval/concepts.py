"""Deterministic concept extraction and matching for retrieval and scoring."""

from __future__ import annotations

import re

from models import ResearchQuery

# Keep matching deterministic and dependency-free; no stemming or fuzzy matching.
TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "via",
    "with",
}


def normalize_concept(value: str) -> str:
    # Normalize only for comparison/deduplication; callers display the original
    # concept text.
    return " ".join(TOKEN_RE.findall(value.lower()))


def tokens(value: str) -> set[str]:
    # Stopword removal keeps generic grant language from creating false matches.
    return {token for token in TOKEN_RE.findall(value.lower()) if token not in STOPWORDS}


def concept_list(research_query: ResearchQuery | None) -> list[str]:
    # Preserve first-seen order so coverage output is stable across runs.
    if research_query is None:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in (
        research_query.required_expertise
        + research_query.methods
        + research_query.disciplines
    ):
        normalized = normalize_concept(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value.strip())
    return result


def text_matches_concept(text: str, concept: str) -> bool:
    # Require every concept token to appear; partial keyword overlap is not enough
    # to claim coverage.
    concept_tokens = tokens(concept)
    if not concept_tokens:
        return False
    text_tokens = tokens(text)
    return concept_tokens.issubset(text_tokens)


def matched_concepts(text: str, concepts: list[str]) -> list[str]:
    return [concept for concept in concepts if text_matches_concept(text, concept)]


def coverage_ratio(matched: list[str], concepts: list[str]) -> float | None:
    # None means "no concepts to cover", not zero coverage; scoring treats those
    # cases differently.
    if not concepts:
        return None
    return len(set(matched) & set(concepts)) / len(concepts)
