"""Structured opportunity interpretation with a deterministic fallback."""

from __future__ import annotations

import re

import config
from models import LLMResearchQuery, Opportunity, ResearchQuery
from src.prompting import load_prompt, prompt_version

# Bounds keep optimization predictable and prevent a model from flooding retrieval.
MAX_SEARCH_QUERIES = 3
MIN_SEARCH_QUERIES = 1

KEYWORD_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "address",
    "award",
    "funding",
    "how",
    "important",
    "in",
    "into",
    "is",
    "it",
    "long",
    "of",
    "on",
    "or",
    "prioritise",
    "prioritize",
    "program",
    "project",
    "proposal",
    "proposals",
    "questions",
    "research",
    "support",
    "supports",
    "term",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "via",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
}


def _keywords(text: str, limit: int = 8) -> list[str]:
    # Simple deterministic keyword extraction is the fallback when the optimizer
    # is unavailable; it intentionally does not call an LLM.
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text.lower())
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in KEYWORD_STOPWORDS or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) >= limit:
            break
    return result


def _fallback_queries(opportunity: Opportunity, focus: str) -> list[str]:
    # The raw opportunity text is always a valid search query, even when no model
    # interpretation is available.
    base = " ".join(
        part
        for part in (
            opportunity.title,
            opportunity.agency,
            (opportunity.description or "")[:1200],
            focus,
        )
        if part and part.strip()
    )
    queries = [base.strip()]
    if focus.strip():
        second = f"{opportunity.title} {focus.strip()} research methods expertise"
        if second.strip() and second.strip() not in queries:
            queries.append(second.strip())
    return queries[:MAX_SEARCH_QUERIES]


def deterministic_research_query(
    opportunity: Opportunity,
    focus: str = "",
    *,
    error: str | None = None,
) -> ResearchQuery:
    # Split the fallback keywords into expertise/method buckets with fixed,
    # deterministic boundaries so repeated runs are identical.
    keyword_text = " ".join([focus.strip(), opportunity.title])
    keywords = _keywords(keyword_text)
    return ResearchQuery(
        problem_statement=(
            f"Address the research needs of {opportunity.title}"
            + (f" with focus on {focus.strip()}." if focus.strip() else ".")
        ),
        required_expertise=keywords[:5],
        methods=keywords[5:8],
        disciplines=_keywords(opportunity.title, limit=3),
        search_queries=_fallback_queries(opportunity, focus),
        exclusions=[],
        prompt_version=prompt_version("optimize_query"),
        fallback_used=True,
        optimization_error=error,
    )


def _clean_list(values: list[str], limit: int = 12) -> list[str]:
    # Collapse whitespace and de-duplicate case-insensitively while preserving the
    # model's original display casing.
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split()).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _validate_llm_query(output: LLMResearchQuery) -> list[str]:
    # Validate the model output deterministically before accepting it; malformed
    # output falls back to the raw opportunity.
    issues: list[str] = []
    if len(output.search_queries) < MIN_SEARCH_QUERIES:
        issues.append("no search queries returned")
    if len(output.search_queries) > MAX_SEARCH_QUERIES:
        issues.append("too many search queries")
    if not output.problem_statement.strip():
        issues.append("problem statement is empty")
    return issues


def optimize_query(
    opportunity: Opportunity,
    focus: str = "",
    *,
    llm_client: object | None = None,
) -> ResearchQuery:
    """Interpret the opportunity into research needs; fall back deterministically."""
    # Offline/test mode intentionally skips the model call entirely.
    if llm_client is None:
        return deterministic_research_query(opportunity, focus)

    system = load_prompt("optimize_query")
    # Opportunity text is untrusted data and is explicitly delimited for the model.
    user = (
        "<opportunity>\n"
        f"Title: {opportunity.title}\n"
        f"Agency: {opportunity.agency or 'Unknown'}\n"
        f"Description:\n{(opportunity.description or '')[:6000]}\n"
        "</opportunity>\n\n"
        f"<user_focus>{focus.strip() or 'None'}</user_focus>\n\n"
        "Return the structured research-needs object."
    )
    try:
        # Only structured output is accepted; the prompt tells the model that
        # evidence is data, never instructions.
        output = llm_client.complete_structured(
            system=system,
            user=user,
            schema=LLMResearchQuery,
            model=config.stage_model("optimize"),
        )
        issues = _validate_llm_query(output)
        if issues:
            raise ValueError("; ".join(issues))
        search_queries = _clean_list(output.search_queries, limit=MAX_SEARCH_QUERIES)
        if not search_queries:
            raise ValueError("no usable search queries")
        return ResearchQuery(
            problem_statement=" ".join(output.problem_statement.split()),
            required_expertise=_clean_list(output.required_expertise),
            methods=_clean_list(output.methods),
            disciplines=_clean_list(output.disciplines),
            search_queries=search_queries,
            exclusions=_clean_list(output.exclusions),
            prompt_version=prompt_version("optimize_query"),
            fallback_used=False,
        )
    except Exception as exc:
        # Preserve the failure class for run metadata without exposing raw errors.
        return deterministic_research_query(
            opportunity,
            focus,
            error=type(exc).__name__,
        )
