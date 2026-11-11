"""MCP-backed contextual research adapters.

Context-Research uses Brave Search, Fetch, and Deep Research through MCP
servers. The provider interface is intentionally small so the live SDK adapter
can be swapped for deterministic fixtures in tests and offline demos. Every
document is returned as a validated :class:`SourceDocument`; whitelisting and
leakage filtering remain the orchestrator's responsibility.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from upset_profiler.config import Settings
from upset_profiler.errors import ConfigurationError, IntegrationError
from upset_profiler.models.research import SourceDocument
from upset_profiler.security.source_whitelist import (
    SourceWhitelist,
    canonicalize_url,
    domain_from_url,
)

_URL_PATTERN = re.compile(r"https?://[^\s<>)\"']+")


def split_stdio_command(command: str) -> tuple[str, list[str]]:
    """Split a stdio server command line into an executable and arguments.

    MCP stdio servers are launched from a command line such as
    ``npx -y @modelcontextprotocol/server-brave-search``. The SDK expects the
    executable and its arguments separately.
    """
    parts = shlex.split(command)
    if not parts:
        raise ConfigurationError("MCP stdio transport requires a command")
    return parts[0], parts[1:]


def stdio_environment(api_key: str | None = None) -> dict[str, str]:
    """Return the child-process environment for a stdio MCP server.

    When ``env`` is ``None`` the MCP SDK forwards only a small allowlist
    (``HOME``, ``LOGNAME``, ``PATH``, ``SHELL``, ``TERM``, ``USER``), which hides
    credentials such as ``BRAVE_API_KEY`` from the server process. Passing the
    parent environment explicitly keeps the server usable.
    """
    env = dict(os.environ)
    if api_key:
        env["BRAVE_API_KEY"] = api_key
    return env


@dataclass(frozen=True)
class ResearchHit:
    """One search result returned by the Brave Search MCP server."""

    url: str
    title: str | None = None
    snippet: str | None = None
    published_at: datetime | None = None


class ResearchSearchClient(Protocol):
    """Search tool used by Context-Research."""

    def search(self, query: str, *, limit: int) -> list[ResearchHit]:
        """Return search hits for a query."""
        ...


class ResearchFetchClient(Protocol):
    """Document fetch tool used by Context-Research."""

    def fetch(self, url: str) -> str:
        """Return the text content of a URL."""
        ...


class ResearchDeepClient(Protocol):
    """Deep Research tool used to enrich a game's context."""

    def research(self, query: str) -> str:
        """Return synthesized research text for a query."""
        ...


class McpContextResearchProvider:
    """Collect cited pregame documents through MCP research tools."""

    source = "mcp"

    def __init__(
        self,
        *,
        search: ResearchSearchClient,
        fetch: ResearchFetchClient,
        deep_research: ResearchDeepClient | None = None,
        whitelist: SourceWhitelist | None = None,
        max_documents: int = 25,
    ) -> None:
        if max_documents < 0:
            raise ConfigurationError("max_documents must be non-negative")
        self._search = search
        self._fetch = fetch
        self._deep = deep_research
        self._whitelist = whitelist
        self._max_documents = max_documents

    def get_documents(self, game_id: str, season: str) -> list[SourceDocument]:
        """Return cited documents for one game from MCP search and fetch tools."""
        query = f"{game_id} {season} college basketball pregame preview"
        hits = self._search.search(query, limit=self._max_documents)
        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for hit in hits:
            if len(documents) >= self._max_documents:
                break
            try:
                canonical = canonicalize_url(hit.url)
            except ValueError:
                continue
            if canonical in seen:
                continue
            if self._whitelist is not None and not self._whitelist.is_allowed_url(
                canonical
            ):
                continue
            seen.add(canonical)
            content = self._fetch.fetch(canonical)
            if not content.strip():
                continue
            documents.append(
                self._document(
                    game_id=game_id,
                    url=canonical,
                    title=hit.title,
                    content=content,
                    published_at=hit.published_at,
                    kind="mcp-search",
                )
            )

        if self._deep is not None and documents:
            research = self._deep.research(query)
            if research.strip():
                documents[0] = self._with_deep_research(documents[0], research)
        return documents

    def _document(
        self,
        *,
        game_id: str,
        url: str,
        title: str | None,
        content: str,
        published_at: datetime | None,
        kind: str,
    ) -> SourceDocument:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return SourceDocument(
            document_id=f"mcp-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}",
            game_id=game_id,
            url=url,
            title=title,
            source_domain=domain_from_url(url),
            published_at=published_at,
            content=content,
            content_hash=digest,
            metadata={"kind": kind},
        )

    def _with_deep_research(
        self,
        document: SourceDocument,
        research: str,
    ) -> SourceDocument:
        content = f"{document.content or ''}\n\nDeep research notes:\n{research}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata = {**document.metadata, "deep_research": True}
        return document.model_copy(
            update={
                "content": content,
                "content_hash": digest,
                "metadata": metadata,
            }
        )


class SdkMcpServerClient:
    """Thin synchronous wrapper around the optional official MCP SDK.

    The SDK is imported lazily so the package stays installable without the
    ``mcp`` extra. Any missing dependency or transport failure is reported as a
    configuration/integration error rather than crashing the pipeline.
    """

    def __init__(
        self,
        *,
        transport: str = "stdio",
        command: str | None = None,
        url: str | None = None,
        timeout_seconds: float = 120.0,
        api_key: str | None = None,
    ) -> None:
        if transport not in {"stdio", "http", "sse"}:
            raise ConfigurationError(
                "unsupported MCP transport",
                context={"transport": transport},
            )
        if transport == "stdio" and not command:
            raise ConfigurationError("MCP stdio transport requires a command")
        if transport in {"http", "sse"} and not url:
            raise ConfigurationError("MCP HTTP/SSE transport requires a URL")
        if timeout_seconds <= 0:
            raise ConfigurationError("MCP timeout must be positive")
        self._transport = transport
        self._command = command
        self._url = url
        self._timeout = timeout_seconds
        self._api_key = api_key

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Call one MCP tool and return its text content blocks."""
        if not name.strip():
            raise ConfigurationError("MCP tool name must be non-empty")
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._call_tool(name, dict(arguments or {})),
                    timeout=self._timeout,
                )
            )
        except TimeoutError as exc:
            raise IntegrationError(
                "MCP tool call timed out",
                context={"tool": name},
                retryable=True,
            ) from exc

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[str]:
        try:
            module = importlib.import_module("mcp")
        except ImportError as exc:
            raise ConfigurationError("the 'mcp' extra is not installed") from exc
        session_class = module.ClientSession
        if self._transport == "stdio":
            return await self._call_stdio(name, arguments, session_class)
        return await self._call_http(name, arguments, session_class)

    async def _call_stdio(
        self,
        name: str,
        arguments: dict[str, Any],
        session_class: Any,
    ) -> list[str]:
        try:
            stdio_module = importlib.import_module("mcp.client.stdio")
        except ImportError as exc:  # pragma: no cover - depends on SDK layout
            raise ConfigurationError("the MCP stdio client is unavailable") from exc
        if self._command is None:  # pragma: no cover - guarded in __init__
            raise ConfigurationError("MCP stdio transport requires a command")
        command, args = split_stdio_command(self._command)
        params = importlib.import_module("mcp").StdioServerParameters(
            command=command,
            args=args,
            env=stdio_environment(self._api_key),
        )
        async with (
            stdio_module.stdio_client(params) as (read, write),
            session_class(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        return _content_texts(result)

    async def _call_http(
        self,
        name: str,
        arguments: dict[str, Any],
        session_class: Any,
    ) -> list[str]:
        headers = (
            {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        )
        try:
            if self._transport == "http":
                module = importlib.import_module("mcp.client.streamable_http")
                async with (
                    module.streamablehttp_client(
                        self._url,
                        headers=headers,
                    ) as (read, write, _),
                    session_class(read, write) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
            else:  # pragma: no cover - depends on server availability
                module = importlib.import_module("mcp.client.sse")
                async with (
                    module.sse_client(self._url, headers=headers) as (read, write),
                    session_class(read, write) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except ImportError as exc:  # pragma: no cover - depends on SDK layout
            raise ConfigurationError("the MCP HTTP client is unavailable") from exc
        return _content_texts(result)


def _content_texts(result: Any) -> list[str]:
    blocks = getattr(result, "content", None)
    if blocks is None and isinstance(result, dict):
        blocks = result.get("content")
    texts: list[str] = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    if not texts:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            texts.append(json.dumps(structured, sort_keys=True))
    return texts


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hit_from_mapping(item: Mapping[str, Any]) -> ResearchHit | None:
    url = item.get("url")
    if not isinstance(url, str) or not url:
        return None
    title = item.get("title") or item.get("name")
    snippet = item.get("description") or item.get("snippet")
    published = item.get("published") or item.get("date") or item.get("age")
    return ResearchHit(
        url=url,
        title=title if isinstance(title, str) else None,
        snippet=snippet if isinstance(snippet, str) else None,
        published_at=_parse_datetime(published),
    )


def parse_search_results(texts: list[str], *, limit: int) -> list[ResearchHit]:
    """Parse Brave-style MCP text blocks into normalized search hits."""
    hits: list[ResearchHit] = []
    for text in texts:
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError:
            for url in _URL_PATTERN.findall(text):
                hits.append(ResearchHit(url=url.rstrip(".,;:!?")))
            continue
        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ("results", "web", "items", "data"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    items = candidate
                    break
        for raw in items:
            if isinstance(raw, dict):
                hit = _hit_from_mapping(raw)
                if hit is not None:
                    hits.append(hit)
        if len(hits) >= limit:
            break
    unique: dict[str, ResearchHit] = {}
    for hit in hits:
        unique.setdefault(hit.url, hit)
    return list(unique.values())[:limit]


class McpSearchClient:
    """Brave Search adapter over an :class:`SdkMcpServerClient`."""

    def __init__(self, client: SdkMcpServerClient, *, tool_name: str = "brave_web_search") -> None:
        self._client = client
        self._tool_name = tool_name

    def search(self, query: str, *, limit: int) -> list[ResearchHit]:
        """Call Brave Search and normalize its results."""
        texts = self._client.call_tool(
            self._tool_name,
            {"query": query, "count": limit},
        )
        return parse_search_results(texts, limit=limit)


class McpFetchClient:
    """Fetch adapter over an :class:`SdkMcpServerClient`."""

    def __init__(self, client: SdkMcpServerClient, *, tool_name: str = "fetch") -> None:
        self._client = client
        self._tool_name = tool_name

    def fetch(self, url: str) -> str:
        """Fetch and join the text content for a URL."""
        return "\n".join(self._client.call_tool(self._tool_name, {"url": url}))


class McpDeepResearchClient:
    """Deep Research adapter over an :class:`SdkMcpServerClient`."""

    def __init__(
        self,
        client: SdkMcpServerClient,
        *,
        tool_name: str = "deep_research",
    ) -> None:
        self._client = client
        self._tool_name = tool_name

    def research(self, query: str) -> str:
        """Run Deep Research and join the returned text."""
        return "\n".join(self._client.call_tool(self._tool_name, {"query": query}))


def build_mcp_context_provider(
    settings: Settings,
    *,
    whitelist: SourceWhitelist,
) -> McpContextResearchProvider:
    """Build the live MCP provider from typed settings.

    Each tool may be configured with its own transport, command, or URL. Missing
    deep-research configuration simply omits that enrichment step.
    """
    mcp = settings.mcp
    brave_key = (
        mcp.brave_api_key.get_secret_value() if mcp.brave_api_key is not None else None
    )
    search_client = SdkMcpServerClient(
        transport=mcp.mcp_brave_transport,
        command=mcp.mcp_brave_command,
        url=mcp.mcp_brave_url,
        timeout_seconds=mcp.mcp_timeout_seconds,
        api_key=brave_key,
    )
    fetch_client = SdkMcpServerClient(
        transport="stdio" if mcp.mcp_fetch_command else "http",
        command=mcp.mcp_fetch_command,
        url=mcp.mcp_fetch_url,
        timeout_seconds=mcp.mcp_timeout_seconds,
    )
    deep_client: McpDeepResearchClient | None = None
    if mcp.mcp_deep_research_command or mcp.mcp_deep_research_url:
        deep_client = McpDeepResearchClient(
            SdkMcpServerClient(
                transport="stdio" if mcp.mcp_deep_research_command else "http",
                command=mcp.mcp_deep_research_command,
                url=mcp.mcp_deep_research_url,
                timeout_seconds=mcp.mcp_timeout_seconds,
            )
        )
    return McpContextResearchProvider(
        search=McpSearchClient(search_client),
        fetch=McpFetchClient(fetch_client),
        deep_research=deep_client,
        whitelist=whitelist,
        max_documents=whitelist.max_documents_per_game,
    )
