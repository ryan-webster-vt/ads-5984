"""Parse follow-up messages into deterministic constraint patches."""

from __future__ import annotations

import re

from models import ConstraintPatch, Faculty, FollowupResult, ResearchQuery, UserConstraints
from src.prompting import load_prompt
from src.team.constraints import apply_patch

SIZE_WORDS = {"three": 3, "four": 4, "five": 5, "3": 3, "4": 4, "5": 5}

DEPARTMENT_ALIASES = {
    "chemist": "Chemistry",
    "chemistry": "Chemistry",
    "chemical": "Chemistry",
    "biologist": "Biological Sciences",
    "biology": "Biological Sciences",
    "biological": "Biological Sciences",
    "physicist": "Physics",
    "physics": "Physics",
    "geologist": "Geosciences",
    "geoscience": "Geosciences",
    "geosciences": "Geosciences",
    "statistician": "Statistics",
    "statistics": "Statistics",
    "psychologist": "Psychology",
    "psychology": "Psychology",
    "mathematician": "Mathematics",
    "mathematics": "Mathematics",
    "economist": "Economics",
    "economics": "Economics",
    "neuroscientist": "School of Neuroscience",
    "neuroscience": "School of Neuroscience",
    "data scientist": "Academy of Data Science",
    "data science": "Academy of Data Science",
}

NAME_PATTERN = r"(?:dr\.?\s+|prof(?:essor)?\.?\s+)?([A-Za-z][A-Za-z'’.-]*(?:\s+[A-Za-z][A-Za-z'’.-]*){0,3}|\d{6,})"
EXPERTISE_MARKERS = ("expertise", "focus", "method", "background", "skill", "strength")


def _clean_reference(value: str) -> str:
    value = re.split(
        r"\s+(?:and|but|then|also|exclude|remove|include|add)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"^(?:dr\.?|prof(?:essor)?\.?)\s+", "", value.strip(), flags=re.I)
    return value.strip(" .,;:")


def _department_for(value: str) -> str | None:
    lowered = value.lower().strip()
    for alias, department in DEPARTMENT_ALIASES.items():
        if alias in lowered:
            return department
    return None


def _is_expertise_phrase(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in EXPERTISE_MARKERS) or bool(
        _department_for(value)
    )


def _parse_size(text: str, patch: ConstraintPatch) -> None:
    size_words = "|".join(SIZE_WORDS)
    patterns = (
        rf"\b(?:shrink|reduce|cut|make|keep|set|change|grow|expand|increase)\b"
        rf"[^.\n]{{0,35}}\b({size_words})\b",
        rf"\bteam(?:\s+size)?\b[^.\n]{{0,25}}\b({size_words})\b",
        rf"\b({size_words})\s*(?:members|people|faculty|person)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            patch.team_size = SIZE_WORDS[match.group(1).lower()]
            return


def _parse_replace(text: str, patch: ConstraintPatch) -> None:
    match = re.search(
        rf"\breplace\s+{NAME_PATTERN}\s+with\s+([^.;\n]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return
    first = _clean_reference(match.group(1))
    replacement = _clean_reference(match.group(2))
    if first:
        patch.add_excluded_members.append(first)
    if not replacement:
        return
    department = _department_for(replacement)
    if department:
        patch.department_preferences.append(department)
        patch.add_expertise.append(replacement)
    else:
        patch.add_required_members.append(replacement)


def _parse_named_members(text: str, patch: ConstraintPatch) -> None:
    for match in re.finditer(
        rf"\b(?:include|require|bring in|add)\s+{NAME_PATTERN}",
        text,
        re.IGNORECASE,
    ):
        reference = _clean_reference(match.group(1))
        if not reference or _is_expertise_phrase(reference):
            continue
        if reference.lower() not in {value.lower() for value in patch.add_required_members}:
            patch.add_required_members.append(reference)

    for match in re.finditer(
        rf"\b(?:exclude|remove|drop|swap out)\s+{NAME_PATTERN}",
        text,
        re.IGNORECASE,
    ):
        reference = _clean_reference(match.group(1))
        if not reference:
            continue
        if _is_expertise_phrase(reference) and "expertise" in reference.lower():
            patch.remove_expertise.append(reference)
        elif reference.lower() not in {value.lower() for value in patch.add_excluded_members}:
            patch.add_excluded_members.append(reference)


def _parse_expertise(text: str, patch: ConstraintPatch) -> None:
    patterns = (
        r"\b(?:add|include|increase|strengthen)\s+([^,.;\n]+?)\s+(?:expertise|focus|methods|background|skills?)\b",
        r"\b(?:expertise|focus|methods?)\s+(?:in|on)\s+([^,.;\n]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = " ".join(match.group(1).split()).strip(" .,;:")
            if not value:
                continue
            department = _department_for(value)
            if department:
                patch.department_preferences.append(department)
            if value.lower() not in {item.lower() for item in patch.add_expertise}:
                patch.add_expertise.append(value)


def _parse_department_preferences(text: str, patch: ConstraintPatch) -> None:
    for match in re.finditer(
        r"\b(?:prefer|prioritize|from|in|with)\s+(?:the\s+)?([A-Za-z -]+?)\s+department\b",
        text,
        re.IGNORECASE,
    ):
        department = _department_for(match.group(1))
        if department:
            patch.department_preferences.append(department)
    for alias, department in DEPARTMENT_ALIASES.items():
        if re.search(rf"\b(?:prefer|prioritize|add|include|need|want)\b[^.\n]{{0,40}}\b{re.escape(alias)}\b", text, re.IGNORECASE):
            patch.department_preferences.append(department)


def _parse_pi(text: str, patch: ConstraintPatch) -> None:
    match = re.search(
        rf"\b(?:pi|principal investigator)\b[^.\n]{{0,40}}"
        rf"\b(?:should be|must be|prefer|make)\s+{NAME_PATTERN}",
        text,
        re.IGNORECASE,
    )
    if match:
        patch.pi_preference = _clean_reference(match.group(1))


def _parse_tenure(text: str, patch: ConstraintPatch) -> None:
    if re.search(r"\b(?:non[- ]?tenure|not tenure[- ]?track)\b", text, re.IGNORECASE):
        return
    if re.search(
        r"\b(?:only|just)\s+(?:tenured|tenure[- ]?track)\b", text, re.IGNORECASE
    ) or (
        re.search(r"\btenure[- ]?track\b", text, re.IGNORECASE)
        and re.search(r"\b(?:only|restrict|limit|keep)\b", text, re.IGNORECASE)
    ):
        patch.tenure_track_only = True


def deterministic_parse_patch(message: str) -> ConstraintPatch:
    patch = ConstraintPatch()
    _parse_size(message, patch)
    _parse_replace(message, patch)
    _parse_named_members(message, patch)
    _parse_expertise(message, patch)
    _parse_department_preferences(message, patch)
    _parse_pi(message, patch)
    _parse_tenure(message, patch)
    if not patch.model_dump(exclude_none=True, exclude_defaults=True):
        patch.clarification_needed = (
            "I could not map that request to a size, member, expertise, or "
            "department constraint. Please name the member or expertise to change."
        )
    return patch


def _patch_is_empty(patch: ConstraintPatch) -> bool:
    return not patch.model_dump(exclude_none=True, exclude_defaults=True)


def parse_followup(
    message: str,
    current_state: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    *,
    llm_client: object | None = None,
) -> ConstraintPatch:
    """Parse one turn; LLM output is optional and validated downstream."""
    if llm_client is not None:
        system = load_prompt("parse_followup")
        user = (
            f"Current constraint state:\n{current_state.model_dump_json()}\n\n"
            f"<user_message>{message}</user_message>\n\n"
            "Return the structured constraint patch."
        )
        try:
            patch = llm_client.complete_structured(
                system=system,
                user=user,
                schema=ConstraintPatch,
            )
            if not _patch_is_empty(patch):
                return patch
        except Exception:
            pass
    return deterministic_parse_patch(message)


def apply_followup(
    message: str,
    current_state: UserConstraints,
    faculty_by_id: dict[str, Faculty],
    *,
    llm_client: object | None = None,
) -> FollowupResult:
    patch = parse_followup(
        message,
        current_state,
        faculty_by_id,
        llm_client=llm_client,
    )
    if patch.clarification_needed:
        return FollowupResult(
            patch=patch,
            state=current_state,
            clarification_needed=patch.clarification_needed,
            applied=False,
            redo_retrieval=False,
            assistant_message=patch.clarification_needed,
        )
    result = apply_patch(current_state, patch, faculty_by_id)
    if result.clarification_needed:
        result.assistant_message = result.clarification_needed
    return result


def augment_research_query(
    research_query: ResearchQuery,
    constraints: UserConstraints,
) -> ResearchQuery:
    """Add newly requested expertise as one bounded additional retrieval query."""
    additions = [
        value
        for value in constraints.add_expertise
        if value.lower()
        not in {existing.lower() for existing in research_query.required_expertise}
    ]
    if not additions:
        return research_query
    required = list(research_query.required_expertise)
    for value in additions:
        if value.lower() not in {existing.lower() for existing in required}:
            required.append(value)
    query = " ".join(additions) + " research publications methods"
    queries = list(research_query.search_queries)
    if query not in queries:
        queries.append(query)
    return research_query.model_copy(
        update={
            "required_expertise": required,
            "search_queries": queries[:3],
        }
    )
