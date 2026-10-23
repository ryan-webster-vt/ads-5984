"""Versioned prompt template loading shared by all LLM stages."""

from __future__ import annotations

import config


def prompt_version(name: str) -> str:
    # The version is recorded in run metadata so a result can be traced to the
    # exact prompt revision that produced it.
    return f"{name}-v{config.PROMPT_VERSION}"


def load_prompt(name: str) -> str:
    # Reject unknown names early so a typo cannot silently send an empty prompt.
    if name not in config.PROMPT_NAMES:
        raise ValueError(f"Unknown prompt template: {name}")
    path = config.PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise RuntimeError(f"Missing prompt template: {path.name}")
    # Prompts are plain UTF-8 Markdown so they can be reviewed and versioned.
    return path.read_text(encoding="utf-8").strip()


def prompt_versions(*names: str) -> dict[str, str]:
    # Convenience helper for persisting several prompt revisions at once.
    return {name: prompt_version(name) for name in names}
