"""Web search tool (T110): LLM-facing capability that delegates to a provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.base import Reversibility, Tool
from app.tools.web.search_provider import SearchProvider, SearchResults

__all__ = ["WebSearchTool"]


class WebSearchTool(Tool):
    """Search the web via a configured provider.

    The provider is injected at construction so the tool itself contains no
    provider logic. The operator chooses the provider via configuration.
    """

    name = "web.search"
    description = (
        "Search the web for information and return a list of results "
        "with titles, URLs, and snippets."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "The search query string.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
                "description": "Maximum number of results to return.",
            },
            "safe_search": {
                "type": "boolean",
                "default": True,
                "description": "Enable safe search filtering.",
            },
            "language": {
                "type": "string",
                "pattern": "^[a-z]{2}$",
                "description": "Preferred result language (ISO 639-1).",
            },
            "region": {
                "type": "string",
                "pattern": "^[a-z]{2}$",
                "description": "Geographic region bias (ISO 3166-1 alpha-2).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE
    audit = False

    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    async def execute(self, arguments: Mapping[str, Any]) -> SearchResults:
        query = str(arguments["query"])
        max_results = int(arguments.get("max_results", 10))
        safe_search = bool(arguments.get("safe_search", True))
        language = arguments.get("language")
        region = arguments.get("region")

        return await self._provider.search(
            query=query,
            max_results=max_results,
            safe_search=safe_search,
            language=language,
            region=region,
        )

    async def verify(
        self, result: SearchResults, arguments: Mapping[str, Any]
    ) -> VerificationOutcome:
        if not isinstance(result, SearchResults):
            return VerificationOutcome.FAILED
        if not result.results:
            return VerificationOutcome.UNVERIFIED
        # At least one result with a valid URL means the search worked
        for r in result.results:
            if r.url.startswith("http"):
                return VerificationOutcome.SUCCESS
        return VerificationOutcome.UNVERIFIED
