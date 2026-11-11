"""Deterministic source-domain allowlisting and URL canonicalization.

Context-Research workers read untrusted web material. The whitelist is the
first line of defense against prompt injection: only configured, reputable
domains are eligible, and the check is deterministic Python rather than an LLM
judgment. The policy is loaded from ``config/sources.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from upset_profiler.errors import ConfigurationError

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class SourcePolicy(BaseModel):
    """Validated representation of ``config/sources.yaml``."""

    model_config = ConfigDict(extra="ignore")

    version: int = Field(default=1, ge=1)
    allow_subdomains: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_domains", "blocked_domains")
    @classmethod
    def _normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().strip(".")
            if not domain:
                raise ValueError("source policy domains must be non-empty")
            normalized.append(domain)
        return normalized


def canonicalize_url(url: str) -> str:
    """Return a canonical http(s) URL or raise :class:`ValueError`.

    Fragments and userinfo are removed, the hostname is lowercased, default
    ports are dropped, and an empty path becomes ``/``.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsupported URL scheme {parts.scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("URL must include a hostname")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parts.path or "/"
    query = parts.query
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_from_url(url: str) -> str:
    """Return the canonical hostname for a URL."""
    return (urlsplit(canonicalize_url(url)).hostname or "").lower()


class SourceWhitelist:
    """Deterministic domain policy used before documents are stored."""

    def __init__(self, policy: SourcePolicy) -> None:
        self._policy = policy
        self._allowed = tuple(policy.allowed_domains)
        self._blocked = tuple(policy.blocked_domains)

    @classmethod
    def from_path(cls, path: str | Path) -> SourceWhitelist:
        """Load a whitelist from a YAML policy file."""
        policy_path = Path(path)
        if not policy_path.is_file():
            raise ConfigurationError(
                "source whitelist file does not exist",
                context={"path": str(policy_path)},
            )
        try:
            payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(
                "failed to load source whitelist",
                context={"path": str(policy_path)},
            ) from exc
        if not isinstance(payload, dict):
            raise ConfigurationError(
                "source whitelist must be a mapping",
                context={"path": str(policy_path)},
            )
        return cls(SourcePolicy.model_validate(payload))

    @property
    def policy(self) -> SourcePolicy:
        """Return the underlying policy."""
        return self._policy

    @property
    def max_documents_per_game(self) -> int:
        """Return the configured per-game document cap."""
        value = self._policy.rules.get("max_documents_per_game", 25)
        return value if isinstance(value, int) and value >= 0 else 25

    @property
    def require_published_at(self) -> bool:
        """Return whether strict mode policy requires publication times."""
        return bool(self._policy.rules.get("require_published_at", True))

    def is_allowed_domain(self, domain: str) -> bool:
        """Return whether a canonical hostname passes the policy."""
        host = domain.strip().lower().rstrip(".")
        if not host:
            return False
        if any(self._matches(host, blocked) for blocked in self._blocked):
            return False
        return any(self._matches(host, allowed) for allowed in self._allowed)

    def is_allowed_url(self, url: str) -> bool:
        """Return whether a URL's canonical hostname passes the policy."""
        try:
            domain = domain_from_url(url)
        except ValueError:
            return False
        return self.is_allowed_domain(domain)

    def _matches(self, host: str, domain: str) -> bool:
        if host == domain:
            return True
        return self._policy.allow_subdomains and host.endswith(f".{domain}")
