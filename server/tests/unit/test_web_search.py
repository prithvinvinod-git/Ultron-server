"""Tests for the web search provider abstraction and DuckDuckGo provider (T110)."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
import respx

from app.tools.web.duckduckgo import DuckDuckGoProvider
from app.tools.web.search_provider import SearchProvider, SearchResult, SearchResults
from app.tools.web.search_tool import WebSearchTool

DDG_URL = "https://html.duckduckgo.com/html/"

_ONE_RESULT = (
    '<html><article class="result">'
    '<h2 class="result__title">'
    '<a class="result__url" href="https://example.com">Example Title</a>'
    "</h2>"
    '<a class="result__snippet" href="https://example.com">This is a snippet.</a>'
    "</article></html>"
)

_TWO_RESULTS = (
    "<html>"
    '<article class="result">'
    '<h2 class="result__title"><a class="result__url" href="https://a.com">First</a></h2>'
    '<a class="result__snippet" href="https://a.com">Snippet A</a>'
    "</article>"
    '<article class="result">'
    '<h2 class="result__title"><a class="result__url" href="https://b.com">Second</a></h2>'
    '<a class="result__snippet" href="https://b.com">Snippet B</a>'
    "</article>"
    "</html>"
)

_REDIRECT_RESULT = (
    '<html><article class="result">'
    '<h2 class="result__title">'
    '<a class="result__url" '
    'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal.example.com%2Fpath">'
    "Real Site</a></h2>"
    '<a class="result__snippet" '
    'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal.example.com%2Fpath">Snippet</a>'
    "</article></html>"
)

_TOOL_RESULT = (
    '<html><article class="result">'
    '<h2 class="result__title">'
    '<a class="result__url" href="https://tool.example">Tool Result</a>'
    "</h2>"
    '<a class="result__snippet" href="https://tool.example">Tool snippet.</a>'
    "</article></html>"
)


class TestSearchProviderContract:
    """The SearchProvider ABC defines the contract all providers must satisfy."""

    def test_duckduckgo_is_a_provider(self) -> None:
        provider = DuckDuckGoProvider()
        assert isinstance(provider, SearchProvider)
        assert provider.name == "duckduckgo"

    def test_search_provider_abstract(self) -> None:
        with pytest.raises(TypeError):
            SearchProvider()  # type: ignore[abstract]


class TestDuckDuckGoProvider:
    """DuckDuckGo HTML scraping provider."""

    @respx.mock
    async def test_search_returns_results(self) -> None:
        provider = DuckDuckGoProvider()
        route = respx.post(DDG_URL).mock(return_value=httpx.Response(200, text=_ONE_RESULT))

        results = await provider.search("test query", max_results=5)

        assert route.called
        assert isinstance(results, SearchResults)
        assert results.provider == "duckduckgo"
        assert results.query == "test query"
        assert len(results.results) == 1
        assert results.results[0].title == "Example Title"
        assert results.results[0].url == "https://example.com"
        assert results.results[0].snippet == "This is a snippet."

    @respx.mock
    async def test_search_multiple_results(self) -> None:
        provider = DuckDuckGoProvider()
        route = respx.post(DDG_URL).mock(return_value=httpx.Response(200, text=_TWO_RESULTS))

        results = await provider.search("multi", max_results=10)

        assert route.called
        assert len(results.results) == 2
        assert results.results[0].title == "First"
        assert results.results[1].title == "Second"

    @respx.mock
    async def test_search_empty_results(self) -> None:
        provider = DuckDuckGoProvider()
        route = respx.post(DDG_URL).mock(
            return_value=httpx.Response(200, text="<html>no results</html>")
        )

        results = await provider.search("empty", max_results=10)

        assert route.called
        assert results.results == []

    @respx.mock
    async def test_search_redirect_url_decoding(self) -> None:
        provider = DuckDuckGoProvider()
        route = respx.post(DDG_URL).mock(return_value=httpx.Response(200, text=_REDIRECT_RESULT))

        results = await provider.search("redirect", max_results=5)

        assert route.called
        assert len(results.results) == 1
        assert results.results[0].url == "https://real.example.com/path"

    async def test_close_is_idempotent(self) -> None:
        provider = DuckDuckGoProvider()
        await provider.close()
        await provider.close()


class TestWebSearchTool:
    """The web.search tool delegates to its provider."""

    @respx.mock
    async def test_execute_returns_search_results(self) -> None:
        provider = DuckDuckGoProvider()
        respx.post(DDG_URL).mock(return_value=httpx.Response(200, text=_TOOL_RESULT))

        tool = WebSearchTool(provider)
        result = await tool.execute({"query": "tool test", "max_results": 5})

        assert isinstance(result, SearchResults)
        assert result.provider == "duckduckgo"
        assert len(result.results) == 1
        assert result.results[0].title == "Tool Result"

    async def test_verify_success_with_results(self) -> None:
        tool = WebSearchTool(DuckDuckGoProvider())
        results = SearchResults(
            query="test",
            results=[SearchResult(title="T", url="https://example.com", snippet="s")],
            provider="duckduckgo",
        )
        outcome = await tool.verify(results, {"query": "test"})
        assert outcome.name == "SUCCESS"

    async def test_verify_unverified_empty_results(self) -> None:
        tool = WebSearchTool(DuckDuckGoProvider())
        results = SearchResults(query="test", results=[], provider="duckduckgo")
        outcome = await tool.verify(results, {"query": "test"})
        assert outcome.name == "UNVERIFIED"

    async def test_verify_failed_invalid_result_type(self) -> None:
        tool = WebSearchTool(DuckDuckGoProvider())
        outcome = await tool.verify(cast("Any", "not a results object"), {"query": "test"})
        assert outcome.name == "FAILED"
