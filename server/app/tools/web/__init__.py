"""The web tool family (T094, T110, spec §31).

``web.fetch`` fetches an ``http(s)`` URL the SSRF guard permits, follows
redirects with a per-hop re-check, and returns a bounded body plus extracted
readable text.

``web.search`` delegates to a pluggable search provider (DuckDuckGo, SearXNG,
Google CSE, etc.) so the LLM never makes HTTP calls directly.
"""

from __future__ import annotations

from app.tools.web.duckduckgo import DuckDuckGoProvider
from app.tools.web.extract import extract_text
from app.tools.web.fetch import WebFetchTool, build_web_tools
from app.tools.web.search_provider import SearchProvider, SearchResult, SearchResults
from app.tools.web.search_tool import WebSearchTool
from app.tools.web.ssrf import assert_host_allowed, assert_url_allowed, parse_url

__all__ = [
    "DuckDuckGoProvider",
    "SearchProvider",
    "SearchResult",
    "SearchResults",
    "WebFetchTool",
    "WebSearchTool",
    "assert_host_allowed",
    "assert_url_allowed",
    "build_web_search_tool",
    "build_web_tools",
    "extract_text",
    "parse_url",
]


def build_web_search_tool(provider: SearchProvider | None = None) -> WebSearchTool:
    """Construct the search tool with a provider.

    If ``provider`` is omitted, a default ``DuckDuckGoProvider`` is used.
    """
    if provider is None:
        provider = DuckDuckGoProvider()
    return WebSearchTool(provider)
