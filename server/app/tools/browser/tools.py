"""The ``browser.*`` tool set (T112, spec §11).

Spec §11 fixes the capability set — navigate, click, type, select, inspect,
extract, screenshot, download, cookies/session handling, close — and the
isolation rule the session manager already enforces (T111,
:mod:`app.tools.browser.session`): **one browser session per agent**. These
tools are the model-facing surface over that manager. They hold no Playwright
reference and no session of their own: each call resolves the *ambient* agent id
(bound by :class:`~app.tools.executor.ToolExecutor` into the correlation context,
§16) and asks the manager for that agent's session, so one agent's tool calls can
never reach another agent's cookies or page.

Two rules shape every tool here:

- **The path is generated, never supplied.** A screenshot or download writes to
  the browser's own data directory under a sanitized, timestamped name; a caller
  cannot name an arbitrary filesystem path (§59.4: resolve before you trust).
- **Navigation is guarded.** ``browser.open`` runs the same SSRF check the web
  tool uses (§31) before handing the URL to Chromium: "let the model choose a URL
  the server then loads from inside its own network" is exactly the hole that
  guard exists to close.

The tools follow §66.7 — they *declare* a permission level, timeout and
reversibility; the pipeline decides whether a call is allowed. Reads are
``READ_ONLY``, interactions ``SAFE_ACTIONS``, and an external file persisted to
disk (``browser.download``) is ``MODIFY_PROJECT``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from app.core.errors import BrowserError, InvalidInputError
from app.database.models import PermissionLevel, VerificationOutcome
from app.observability.logging import get_agent_id
from app.tools.arguments import as_bool, as_int, as_optional_int, as_str
from app.tools.base import Reversibility, Tool
from app.tools.browser.runtime import BrowserPage
from app.tools.browser.session import BrowserSession, BrowserSessionManager
from app.tools.web.ssrf import assert_url_allowed

__all__ = [
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserCookiesTool",
    "BrowserDownloadTool",
    "BrowserExtractTool",
    "BrowserInspectTool",
    "BrowserOpenTool",
    "BrowserScreenshotTool",
    "BrowserSelectTool",
    "BrowserSetCookiesTool",
    "BrowserTypeTool",
    "build_browser_tools",
]

#: Element summary returned by ``browser.inspect``: tag, id, class list, a
#: bounded slice of visible text, and any ``href``. Parameters arrive as a
#: single serializable argument (never interpolated into the source) so a
#: selector containing quotes cannot escape the expression.
_INSPECT_JS = """
(args) => {
  const selector = args.selector;
  const limit = args.limit;
  const nodes = selector ? Array.from(document.querySelectorAll(selector)) : [];
  return nodes.slice(0, limit).map((el) => ({
    tag: el.tagName ? el.tagName.toLowerCase() : null,
    id: el.id || null,
    classes: (typeof el.className === 'string' && el.className)
      ? el.className.split(/\\s+/).filter(Boolean)
      : [],
    text: (el.innerText || el.textContent || '').slice(0, 200),
    href: el.getAttribute ? el.getAttribute('href') : null,
  }));
}
"""

_PAGE_TEXT_JS = "() => (document.body ? document.body.innerText : '')"

_OUTER_HTML_JS = (
    "(selector) => {"
    " const el = document.querySelector(selector);"
    " return el ? el.outerHTML : null; }"
)

#: Default/maximum size of an ``browser.extract`` body, in characters. Bounded
#: so a pathological page cannot flood the model's context (§59.6).
_DEFAULT_MAX_CHARS = 20_000
_MAX_MAX_CHARS = 200_000

#: Default/maximum number of elements ``browser.inspect`` reports.
_DEFAULT_INSPECT_LIMIT = 20
_MAX_INSPECT_LIMIT = 200


class _BrowserTool(Tool):
    """Family base: the shared declarations and the agent→session resolution.

    ``timeout`` and ``reversibility`` are uniform across the family and declared
    here once; ``permission_level`` and the schema differ per tool, so each
    concrete tool declares its own. The class stays abstract (no ``execute``), so
    :meth:`Tool.__init_subclass__` lets the family base declare on the
    subclasses' behalf.
    """

    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(
        self,
        manager: BrowserSessionManager,
        *,
        allow_private_networks: bool = False,
    ) -> None:
        self._manager = manager
        self._allow_private_networks = allow_private_networks

    def _agent_id(self) -> str:
        """The ambient agent id, or a clear refusal when none is bound."""
        agent_id = get_agent_id()
        if not agent_id:
            raise BrowserError(
                f"{self.name} requires an agent context but no agent id is bound",
                details={"tool": self.name},
            )
        return agent_id

    async def _session(self) -> BrowserSession:
        """The calling agent's isolated session (created on first use)."""
        return await self._manager.acquire(self._agent_id())

    async def _page(self) -> BrowserPage:
        """The session's current page, opening one on first use."""
        session = await self._session()
        return await session.page()

    async def _prepare_dir(self, raw: str) -> Path:
        """Ensure an artifact directory exists and return it (non-blocking)."""
        directory = Path(raw)
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        return directory


class BrowserOpenTool(_BrowserTool):
    """Navigate the agent's browser to a guarded http(s) URL."""

    name = "browser.open"
    description = (
        "Navigate the agent's browser to an http(s) URL and report the final "
        "URL, HTTP status and page title."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "Absolute http(s) URL to open.",
            },
            "wait_until": {
                "type": "string",
                "enum": ["commit", "domcontentloaded", "load", "networkidle"],
                "default": "load",
                "description": "Navigation readiness condition.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        url = as_str(arguments.get("url"), "url")
        wait_until = as_str(arguments.get("wait_until"), "wait_until", default="load")
        # §31/§59.4: resolve (DNS) and vet the host off the event loop.
        await asyncio.to_thread(
            assert_url_allowed, url, allow_private_networks=self._allow_private_networks
        )
        session = await self._session()
        result = await session.goto(url, wait_until=wait_until)
        return {"url": result.url, "status": result.status, "title": result.title}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        status = result.get("status") if isinstance(result, Mapping) else None
        if status is None:
            return VerificationOutcome.UNVERIFIED
        if isinstance(status, int) and 200 <= status < 400:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class BrowserClickTool(_BrowserTool):
    """Click an element in the agent's browser."""

    name = "browser.click"
    description = "Click the first element matching a CSS selector in the agent's browser."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "default": "left",
            },
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
        "required": ["selector"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector")
        button = as_str(arguments.get("button"), "button", default="left")
        timeout_ms = as_optional_int(arguments.get("timeout_ms"), "timeout_ms", minimum=1)
        page = await self._page()
        await page.click(selector, button=button, timeout_ms=timeout_ms)
        return {"clicked": selector, "button": button}


class BrowserTypeTool(_BrowserTool):
    """Type text into an input in the agent's browser."""

    name = "browser.type"
    description = (
        "Replace the value of the input matching a CSS selector with text, "
        "optionally pressing Enter to submit."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "default": False},
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
        "required": ["selector", "text"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector")
        text = as_str(arguments.get("text"), "text", allow_empty=True)
        submit = as_bool(arguments.get("submit"), "submit", default=False)
        timeout_ms = as_optional_int(arguments.get("timeout_ms"), "timeout_ms", minimum=1)
        page = await self._page()
        await page.fill(selector, text, timeout_ms=timeout_ms)
        if submit:
            await page.press(selector, "Enter", timeout_ms=timeout_ms)
        return {"filled": selector, "length": len(text), "submitted": submit}


class BrowserSelectTool(_BrowserTool):
    """Select an option in a ``<select>`` in the agent's browser."""

    name = "browser.select"
    description = "Select an option by value in the ``<select>`` matching a CSS selector."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "value": {"type": "string"},
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
        "required": ["selector", "value"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector")
        value = as_str(arguments.get("value"), "value", allow_empty=True)
        timeout_ms = as_optional_int(arguments.get("timeout_ms"), "timeout_ms", minimum=1)
        page = await self._page()
        selected = await page.select_option(selector, value, timeout_ms=timeout_ms)
        return {"selector": selector, "selected": selected}


class BrowserInspectTool(_BrowserTool):
    """Inspect the agent's page: URL, title, and matched elements."""

    name = "browser.inspect"
    description = (
        "Inspect the agent's current page: its URL and title, plus a summary "
        "(tag, id, classes, text, href) of elements matching an optional selector."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_INSPECT_LIMIT,
                "default": _DEFAULT_INSPECT_LIMIT,
            },
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector", default="")
        limit = as_int(
            arguments.get("limit"),
            "limit",
            default=_DEFAULT_INSPECT_LIMIT,
            minimum=1,
        )
        limit = min(limit, _MAX_INSPECT_LIMIT)
        page = await self._page()
        elements: list[Any] = []
        if selector:
            raw = await page.evaluate(_INSPECT_JS, {"selector": selector, "limit": limit})
            if isinstance(raw, list):
                elements = raw
        return {
            "url": page.url,
            "title": await page.title(),
            "selector": selector or None,
            "count": len(elements),
            "elements": elements,
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if not isinstance(result, Mapping):
            return VerificationOutcome.FAILED
        if result.get("elements") or result.get("title") or result.get("url"):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.UNVERIFIED


class BrowserExtractTool(_BrowserTool):
    """Extract text or HTML from the agent's page."""

    name = "browser.extract"
    description = (
        "Extract the rendered text (or HTML) of the agent's page or of the "
        "first element matching a CSS selector."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "format": {
                "type": "string",
                "enum": ["text", "html"],
                "default": "text",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_MAX_CHARS,
                "default": _DEFAULT_MAX_CHARS,
            },
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector", default="")
        fmt = as_str(arguments.get("format"), "format", default="text")
        max_chars = as_int(
            arguments.get("max_chars"),
            "max_chars",
            default=_DEFAULT_MAX_CHARS,
            minimum=1,
        )
        max_chars = min(max_chars, _MAX_MAX_CHARS)
        page = await self._page()
        content = await self._read(page, selector, fmt)
        truncated = len(content) > max_chars
        return {
            "url": page.url,
            "selector": selector or None,
            "format": fmt,
            "truncated": truncated,
            "text": content[:max_chars],
        }

    async def _read(self, page: BrowserPage, selector: str, fmt: str) -> str:
        if fmt == "html":
            if selector:
                raw = await page.evaluate(_OUTER_HTML_JS, selector)
            else:
                raw = await page.content()
        elif selector:
            raw = await page.inner_text(selector)
        else:
            raw = await page.evaluate(_PAGE_TEXT_JS)
        return "" if raw is None else str(raw)

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        text = result.get("text") if isinstance(result, Mapping) else None
        if isinstance(text, str) and text:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.UNVERIFIED


class BrowserScreenshotTool(_BrowserTool):
    """Capture a PNG screenshot of the agent's page."""

    name = "browser.screenshot"
    description = "Save a PNG screenshot of the agent's current page and return its path."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
            "full_page": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        name = as_str(arguments.get("name"), "name", default="screenshot")
        full_page = as_bool(arguments.get("full_page"), "full_page", default=False)
        directory = await self._prepare_dir(self._manager.settings.screenshot_dir)
        target = directory / _artifact_name(name, default="screenshot", suffix=".png")
        page = await self._page()
        written = await page.screenshot(target, full_page=full_page)
        return {"path": str(written), "full_page": full_page}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        path = result.get("path") if isinstance(result, Mapping) else None
        if isinstance(path, str) and path:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class BrowserDownloadTool(_BrowserTool):
    """Trigger and save a download from the agent's page."""

    name = "browser.download"
    description = (
        "Click the element matching a CSS selector, wait for the download it "
        "triggers, save it under the browser's download directory and return the path."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1, "maxLength": 120},
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
        "required": ["selector"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        selector = as_str(arguments.get("selector"), "selector")
        name = as_str(arguments.get("name"), "name", default="download")
        timeout_ms = as_optional_int(arguments.get("timeout_ms"), "timeout_ms", minimum=1)
        directory = await self._prepare_dir(self._manager.settings.download_dir)
        target = directory / _artifact_name(name, default="download")
        page = await self._page()
        written = await page.download(selector, target, timeout_ms=timeout_ms)
        return {"path": str(written), "selector": selector}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        path = result.get("path") if isinstance(result, Mapping) else None
        if isinstance(path, str) and path:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class BrowserCookiesTool(_BrowserTool):
    """Read the agent's isolated cookies."""

    name = "browser.cookies"
    description = "Return the cookies held by the agent's isolated browser session."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        session = await self._session()
        cookies = await session.cookies()
        return {"count": len(cookies), "cookies": cookies}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        cookies = result.get("cookies") if isinstance(result, Mapping) else None
        if isinstance(cookies, list):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class BrowserSetCookiesTool(_BrowserTool):
    """Add or clear cookies in the agent's isolated session."""

    name = "browser.set_cookies"
    description = (
        "Add cookies to, or clear the cookies of, the agent's isolated browser "
        "session (session handling)."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "cookies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "value": {"type": "string"},
                        "url": {"type": "string"},
                        "domain": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["name", "value"],
                    "additionalProperties": False,
                },
            },
            "clear": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        clear = as_bool(arguments.get("clear"), "clear", default=False)
        raw = arguments.get("cookies")
        cookies: list[Mapping[str, Any]] = []
        if raw is not None:
            if not isinstance(raw, list):
                raise InvalidInputError(
                    "cookies must be an array", details={"cookies": repr(raw)[:100]}
                )
            cookies = raw
        session = await self._session()
        if clear:
            await session.clear_cookies()
        if cookies:
            await session.add_cookies(cookies)
        remaining = await session.cookies()
        return {"added": len(cookies), "cleared": clear, "count": len(remaining)}


class BrowserCloseTool(_BrowserTool):
    """Close the agent's browser session."""

    name = "browser.close"
    description = "Close the calling agent's browser session and release its context."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        agent_id = self._agent_id()
        released = await self._manager.release(agent_id)
        return {"closed": released, "agent_id": agent_id}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        closed = result.get("closed") if isinstance(result, Mapping) else None
        if closed is True:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.UNVERIFIED


def build_browser_tools(
    manager: BrowserSessionManager, *, allow_private_networks: bool = False
) -> tuple[Tool, ...]:
    """Construct the ``browser.*`` tools bound to ``manager``.

    ``allow_private_networks`` is forwarded to ``browser.open``'s SSRF guard so
    the operator's ``WEB_ALLOW_PRIVATE_NETWORKS`` choice governs navigation the
    same way it governs ``web.fetch``.
    """
    return (
        BrowserOpenTool(manager, allow_private_networks=allow_private_networks),
        BrowserClickTool(manager, allow_private_networks=allow_private_networks),
        BrowserTypeTool(manager, allow_private_networks=allow_private_networks),
        BrowserSelectTool(manager, allow_private_networks=allow_private_networks),
        BrowserInspectTool(manager, allow_private_networks=allow_private_networks),
        BrowserExtractTool(manager, allow_private_networks=allow_private_networks),
        BrowserScreenshotTool(manager, allow_private_networks=allow_private_networks),
        BrowserDownloadTool(manager, allow_private_networks=allow_private_networks),
        BrowserCookiesTool(manager, allow_private_networks=allow_private_networks),
        BrowserSetCookiesTool(manager, allow_private_networks=allow_private_networks),
        BrowserCloseTool(manager, allow_private_networks=allow_private_networks),
    )


def _artifact_name(name: str, *, default: str, suffix: str = "") -> str:
    """A sanitized, collision-free filename for a screenshot or download (§59.4)."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name).strip("._")
    if not cleaned:
        cleaned = default
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{cleaned}-{stamp}-{uuid.uuid4().hex[:8]}{suffix}"
