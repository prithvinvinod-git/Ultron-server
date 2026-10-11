"""`web.fetch` (T094, spec §31) — guarded HTTP fetch with text extraction.

The guard runs on the initial URL *and on every redirect hop*, because a public
host may 308 to a private one; redirects are followed manually for exactly that
reason. The body is read with a byte ceiling so a tool call can never pull an
unbounded response into memory, and the result says whether it truncated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urljoin

import httpx

from app.config.settings import WebSettings
from app.core.errors import DependencyUnavailableError, OperationTimeoutError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.arguments import as_bool, as_int, as_str
from app.tools.base import Reversibility, Tool
from app.tools.web.extract import extract_text
from app.tools.web.ssrf import assert_url_allowed

__all__ = ["WebFetchTool", "build_web_tools"]

_MAX_REDIRECTS = 5


class WebFetchTool(Tool):
    """Fetch a URL the SSRF guard allows, returning bounded text."""

    name = "web.fetch"
    description = "Fetch an http(s) URL and return its bounded body and readable text."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "minLength": 1},
            "extract": {"type": "boolean", "default": True},
            "max_bytes": {"type": "integer", "minimum": 1},
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, settings: WebSettings) -> None:
        self._settings = settings
        self._timeout = float(settings.timeout)

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        url = as_str(arguments.get("url"), "url")
        limit = as_int(
            arguments.get("max_bytes"),
            "max_bytes",
            default=self._settings.max_response_bytes,
            minimum=1,
        )
        limit = min(limit, self._settings.max_response_bytes)
        extract = as_bool(arguments.get("extract"), "extract", default=True)

        allow_private = self._settings.allow_private_networks
        headers = {"User-Agent": self._settings.user_agent, "Accept": "*/*"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, headers=headers, follow_redirects=False
            ) as client:
                return await _fetch(
                    client, url, limit=limit, extract=extract, allow_private=allow_private
                )
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(f"fetch of {url} timed out") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(url, reason="the request failed", cause=exc) from exc

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        status = result.get("status_code") if isinstance(result, Mapping) else None
        if isinstance(status, int) and 200 <= status < 400:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    limit: int,
    extract: bool,
    allow_private: bool,
) -> dict[str, Any]:
    current = await asyncio.to_thread(assert_url_allowed, url, allow_private_networks=allow_private)
    redirects = 0
    while True:
        async with client.stream("GET", current) as response:
            if response.is_redirect and redirects < _MAX_REDIRECTS:
                location = response.headers.get("location")
                if location:
                    redirects += 1
                    nxt = urljoin(str(response.url), location)
                    current = await asyncio.to_thread(
                        assert_url_allowed, nxt, allow_private_networks=allow_private
                    )
                    continue
            body, truncated = await _read_body(response, limit)
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            final_url = str(response.url)
            break

    text = body.decode("utf-8", errors="replace")
    if extract and "html" in content_type.lower():
        text = extract_text(text)
    return {
        "url": url,
        "final_url": final_url,
        "status_code": status,
        "content_type": content_type,
        "bytes": len(body),
        "truncated": truncated,
        "text": text,
    }


async def _read_body(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    chunks = bytearray()
    truncated = False
    async for chunk in response.aiter_bytes():
        remaining = limit - len(chunks)
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks += chunk[:remaining]
            truncated = True
            break
        chunks += chunk
    return bytes(chunks), truncated


def build_web_tools(settings: WebSettings) -> tuple[Tool, ...]:
    """Construct the web tools bound to ``settings``."""
    return (WebFetchTool(settings),)
