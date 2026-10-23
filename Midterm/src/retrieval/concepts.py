"""Deterministic concept extraction and matching for retrieval and scoring."""

from __future__ import annotations

import re

from models import ResearchQuery

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
    return " ".join(TOKEN_RE.findall(value.lower()))


def tokens(value: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(value.lower()) if token not in STOPWORDS}


def concept_list(research_query: ResearchQuery | None) -> list[str]:
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
    concept_tokens = tokens(concept)
    if not concept_tokens:
        return False
    text_tokens = tokens(text)
    return concept_tokens.issubset(text_tokens)


def matched_concepts(text: str, concepts: list[str]) -> list[str]:
    return [concept for concept in concepts if text_matches_concept(text, concept)]


def coverage_ratio(matched: list[str], concepts: list[str]) -> float | None:
    if not concepts:
        return None
    return len(set(matched) & set(concepts)) / len(concepts)
