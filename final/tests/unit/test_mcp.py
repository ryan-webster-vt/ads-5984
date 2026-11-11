"""Tests for MCP contextual research adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from upset_profiler.errors import ConfigurationError
from upset_profiler.integrations.fakes import FixtureContextResearchProvider
from upset_profiler.integrations.mcp import (
    McpContextResearchProvider,
    ResearchHit,
    SdkMcpServerClient,
    parse_search_results,
    split_stdio_command,
    stdio_environment,
)
from upset_profiler.security.source_whitelist import SourcePolicy, SourceWhitelist


class _Search:
    def __init__(self, hits: list[ResearchHit]) -> None:
        self._hits = hits

    def search(self, query: str, *, limit: int) -> list[ResearchHit]:
        return self._hits[:limit]


class _Fetch:
    def fetch(self, url: str) -> str:
        return f"Pregame report from {url}. The underdog is on short rest."


class _Deep:
    def __init__(self) -> None:
        self.calls = 0

    def research(self, query: str) -> str:
        self.calls += 1
        return "Synthesized context from multiple reports."


def _whitelist() -> SourceWhitelist:
    return SourceWhitelist(SourcePolicy(allowed_domains=["espn.com"]))


def test_mcp_provider_filters_fetches_and_enriches_documents() -> None:
    deep = _Deep()
    provider = McpContextResearchProvider(
        search=_Search(
            [
                ResearchHit(
                    "https://www.espn.com/story#fragment",
                    title="Preview",
                    published_at=datetime(2024, 12, 9, tzinfo=UTC),
                ),
                ResearchHit("https://example.com/untrusted"),
                ResearchHit(
                    "https://www.espn.com/story",
                    title="Duplicate",
                    published_at=datetime(2024, 12, 9, tzinfo=UTC),
                ),
            ]
        ),
        fetch=_Fetch(),
        deep_research=deep,
        whitelist=_whitelist(),
    )

    documents = provider.get_documents("401000001", "2024-2025")

    assert len(documents) == 1
    assert str(documents[0].url) == "https://www.espn.com/story"
    assert documents[0].source_domain == "www.espn.com"
    assert documents[0].metadata["deep_research"] is True
    assert "Deep research notes" in (documents[0].content or "")
    assert deep.calls == 1


def test_mcp_provider_without_deep_research_keeps_document() -> None:
    provider = McpContextResearchProvider(
        search=_Search([ResearchHit("https://espn.com/story")]),
        fetch=_Fetch(),
        whitelist=_whitelist(),
    )
    documents = provider.get_documents("game-1", "2024-2025")
    assert len(documents) == 1
    assert "deep_research" not in documents[0].metadata


def test_fixture_context_research_provider_validates_documents() -> None:
    documents = FixtureContextResearchProvider().get_documents("401000001", "2024-2025")
    assert [document.document_id for document in documents] == [
        "doc-401000001-rest",
        "doc-401000001-form",
    ]


def test_parse_search_results_handles_json_and_plain_text() -> None:
    json_hits = parse_search_results(
        ['{"results": [{"url": "https://espn.com/a", "title": "A"}]}'],
        limit=5,
    )
    assert [hit.url for hit in json_hits] == ["https://espn.com/a"]

    text_hits = parse_search_results(
        ["Read more at https://espn.com/b and https://sports-reference.com/cbb."],
        limit=5,
    )
    assert [hit.url for hit in text_hits] == [
        "https://espn.com/b",
        "https://sports-reference.com/cbb",
    ]


def test_mcp_client_requires_transport_configuration() -> None:
    with pytest.raises(ConfigurationError, match="command"):
        SdkMcpServerClient(transport="stdio")
    with pytest.raises(ConfigurationError, match="URL"):
        SdkMcpServerClient(transport="http")
    with pytest.raises(ConfigurationError, match="transport"):
        SdkMcpServerClient(transport="invalid")  # type: ignore[arg-type]


def test_split_stdio_command_parses_executable_and_arguments() -> None:
    assert split_stdio_command("npx -y @modelcontextprotocol/server-brave-search") == (
        "npx",
        ["-y", "@modelcontextprotocol/server-brave-search"],
    )
    assert split_stdio_command("uvx mcp-server-fetch") == ("uvx", ["mcp-server-fetch"])
    assert split_stdio_command("/usr/bin/python3") == ("/usr/bin/python3", [])
    assert split_stdio_command("  npx   -y   pkg  ") == ("npx", ["-y", "pkg"])
    with pytest.raises(ConfigurationError, match="command"):
        split_stdio_command("   ")


def test_stdio_environment_inherits_parent_and_injects_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("UPSET_PROFILER_TEST_MARKER", "present")

    env = stdio_environment("brave-secret")
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["UPSET_PROFILER_TEST_MARKER"] == "present"
    assert env["BRAVE_API_KEY"] == "brave-secret"

    assert "BRAVE_API_KEY" not in stdio_environment(None)
