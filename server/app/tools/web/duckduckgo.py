"""DuckDuckGo HTML search provider (T110).

No API key required; scrapes the public HTML endpoint. Suitable for low-volume
use and testing. For production volumes use a provider with a proper API
(SearXNG, Brave, Google CSE, etc.).
"""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

import httpx

from app.tools.web.search_provider import SearchProvider, SearchResults, _normalize_results

__all__ = ["DuckDuckGoProvider"]


class DuckDuckGoProvider(SearchProvider):
    """Search via DuckDuckGo's HTML frontend."""

    name = "duckduckgo"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        html_selector: str = "article.result",
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = httpx.Timeout(timeout)
        self._selector = html_selector

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "ULTRON/0.1 (+https://ultron.local)"},
            )
            self._owns_client = True
        return self._client

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        safe_search: bool = True,
        language: str | None = None,
        region: str | None = None,
    ) -> SearchResults:
        params = {"q": query, "kl": language or "us-en"}
        if safe_search:
            params["safe"] = "1"
        if region:
            params["kl"] = f"{region.lower()}-{language or 'en'}"

        url = "https://html.duckduckgo.com/html/"
        client = await self._http()
        resp = await client.post(url, data=params)
        resp.raise_for_status()

        # Parse the HTML results. The structure uses <article class="result">
        # with title in <a class="result__snippet"> or <h2><a>.
        items = self._parse_html(resp.text, max_results)
        return _normalize_results(
            provider=self.name,
            query=query,
            items=items,
            title_key="title",
            url_key="url",
            snippet_key="snippet",
        )

    def _parse_html(self, html_text: str, max_results: int) -> list[dict[str, Any]]:
        """Extract results from DuckDuckGo HTML.

        DuckDuckGo's HTML structure (as of 2024):
        <article class="result">
          <h2 class="result__title"><a class="result__url" href="...">Title</a></h2>
          <a class="result__snippet" href="...">Snippet...</a>
        </article>
        """
        items: list[dict[str, Any]] = []

        # Find all <article class="result"> blocks
        article_pattern = re.compile(
            r'<article\s+class="result"[^>]*>.*?</article>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in article_pattern.finditer(html_text):
            if len(items) >= max_results:
                break
            block = match.group(0)

            # Title + URL from <h2 class="result__title"><a ...>
            title_match = re.search(
                r'<h2\s+class="result__title"[^>]*>\s*<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            # Snippet from <a class="result__snippet" href="...">...
            snippet_match = re.search(
                r'<a\s+class="result__snippet"[^>]*href="[^"]*"[^>]*>(.*?)</a>',
                block,
                re.DOTALL | re.IGNORECASE,
            )

            if not title_match:
                continue

            url = html.unescape(title_match.group(1))
            title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(2))).strip()
            snippet = ""
            if snippet_match:
                snippet = html.unescape(re.sub(r"<[^>]+>", "", snippet_match.group(1))).strip()

            # DuckDuckGo sometimes uses relative URLs with redirect through duckduckgo.com
            if url.startswith("//duckduckgo.com/l/?uddg="):
                # Extract the real URL from the redirect parameter
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs:
                    url = qs["uddg"][0]

            items.append({"title": title, "url": url, "snippet": snippet})

        return items

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
