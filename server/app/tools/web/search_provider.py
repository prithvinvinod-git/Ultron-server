"""Search provider abstraction (T110): pluggable search backends.

The LLM never makes HTTP calls directly. Instead it invokes a tool
(`web.search`) that delegates to a configured search provider. This keeps the
provider choice (DuckDuckGo, Google, Brave, SearXNG, etc.) out of the model's
prompt and under operator control.

The interface is deliberately small: one async method taking a query and
returning structured results. Each provider handles its own auth, rate limits,
and HTML/JSON parsing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["SearchProvider", "SearchResult", "SearchResults"]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search hit."""

    title: str
    url: str
    snippet: str
    score: float | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SearchResults:
    """A page of results from one provider."""

    query: str
    results: Sequence[SearchResult]
    provider: str
    total_estimated: int | None = None
    raw: dict[str, Any] | None = None


class SearchProvider(ABC):
    """Abstract search backend.

    Concrete providers implement ``search`` with their own auth, endpoint, and
    parsing. The tool pipeline calls this; the LLM sees only the returned
    ``SearchResults``.
    """

    name: str
    """Provider identifier (e.g. ``"duckduckgo"``, ``"google"``, ``"searxng"``)."""

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        safe_search: bool = True,
        language: str | None = None,
        region: str | None = None,
    ) -> SearchResults:
        """Return search results for ``query``.

        Args:
            query: The search string.
            max_results: Maximum hits to return (provider may return fewer).
            safe_search: Enable safe-search filtering where supported.
            language: Preferred result language (ISO 639-1 code).
            region: Geographic region bias (ISO 3166-1 alpha-2 code).

        Returns:
            A ``SearchResults`` page with titles, URLs, snippets.
        """
        ...

    async def close(self) -> None:  # noqa: B027 - intentional no-op hook for providers
        """Release any held resources (HTTP clients, etc.). Default no-op."""
        ...


def _normalize_results(
    provider: str,
    query: str,
    items: Sequence[dict[str, Any]],
    *,
    title_key: str = "title",
    url_key: str = "url",
    snippet_key: str = "snippet",
    score_key: str | None = "score",
    total_key: str | None = None,
) -> SearchResults:
    """Helper to convert a provider's raw JSON list into ``SearchResults``.

    Accepts flexible key names so providers can map their JSON without boilerplate.
    """
    results: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get(title_key)
        url = item.get(url_key)
        snippet = item.get(snippet_key)
        if not title or not url:
            continue
        score = item.get(score_key) if score_key else None
        results.append(
            SearchResult(
                title=str(title),
                url=str(url),
                snippet=str(snippet) if snippet else "",
                score=float(score) if score is not None else None,
                raw=item,
            )
        )
    total = None
    if total_key and items and isinstance(items[0], dict):
        total = items[0].get(total_key)
        if isinstance(total, (int, float)):
            total = int(total)
    return SearchResults(
        query=query,
        results=results,
        provider=provider,
        total_estimated=total,
    )
