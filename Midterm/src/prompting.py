"""Versioned prompt template loading shared by all LLM stages."""

from __future__ import annotations

import config


def prompt_version(name: str) -> str:
    return f"{name}-v{config.PROMPT_VERSION}"


def load_prompt(name: str) -> str:
    if name not in config.PROMPT_NAMES:
        raise ValueError(f"Unknown prompt template: {name}")
    path = config.PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise RuntimeError(f"Missing prompt template: {path.name}")
    return path.read_text(encoding="utf-8").strip()


def prompt_versions(*names: str) -> dict[str, str]:
    return {name: prompt_version(name) for name in names}
