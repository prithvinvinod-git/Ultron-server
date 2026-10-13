"""The browser runtime adapter and its handle abstractions (T111, spec §11).

Spec §11 fixes the capability set (navigate, click, type, select, inspect,
extract, screenshot, download, cookies, close) and the isolation boundary:
**browser sessions must be isolated by agent**. Playwright's own isolation
primitive is the *browser context* — each context has its own cookies, storage,
cache and pages — so a session is one context, and the session manager (see
:mod:`app.tools.browser.session`) maps one agent to one context.

This module keeps the Playwright dependency behind three abstract handles —
:class:`BrowserRuntime`, :class:`BrowserContext`, :class:`BrowserPage` — so:

- the session manager never imports Playwright, and
- tests can drive the full manager with an in-memory fake, while the handful of
  tests that need a real browser are marked ``requires_playwright`` and skipped
  when it is absent (T116).

The concrete :class:`PlaywrightRuntime` imports ``playwright.async_api`` lazily
*inside* its methods: importing it at module import would make the whole browser
package explode on a machine without the optional dependency, which is exactly
what the availability probe exists to avoid.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import BrowserSettings
from app.core.errors import BrowserError, DependencyUnavailableError

__all__ = [
    "BrowserContext",
    "BrowserPage",
    "BrowserRuntime",
    "NavigationResult",
    "PlaywrightRuntime",
]

#: Resource kinds the media-blocking context aborts before they are fetched
#: (``BROWSER_BLOCK_MEDIA``). Fonts and media as well as images: a text-extraction
#: agent almost never needs them, and on a 2.6 GiB server the saved memory and
#: bandwidth are not incidental.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

#: Playwright's ``wait_until`` values, none of which are ULTRON's invention.
_VALID_WAIT_UNTIL = frozenset({"commit", "domcontentloaded", "load", "networkidle"})


@dataclass(frozen=True, slots=True)
class NavigationResult:
    """What a navigation produced: the final URL, HTTP status, and page title."""

    url: str
    status: int | None
    title: str


class BrowserPage(ABC):
    """One page (tab) inside a context: §11's DOM-automation surface.

    Every method maps to a Playwright page operation; the abstraction exists so
    tools (T112) depend on the contract, not on Playwright, and so a fake can
    stand in for tests. Selectors are Playwright selectors (CSS by default).
    """

    @property
    @abstractmethod
    def url(self) -> str:
        """The page's current URL (empty string before the first navigation)."""

    @abstractmethod
    async def goto(
        self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None
    ) -> NavigationResult:
        """Navigate to ``url`` and report where the page landed."""

    @abstractmethod
    async def title(self) -> str:
        """The document title."""

    @abstractmethod
    async def content(self) -> str:
        """The full serialized HTML of the page."""

    @abstractmethod
    async def inner_text(self, selector: str, *, timeout_ms: int | None = None) -> str:
        """The rendered text content of the first element matching ``selector``."""

    @abstractmethod
    async def attribute(
        self, selector: str, name: str, *, timeout_ms: int | None = None
    ) -> str | None:
        """The value of attribute ``name`` on the first match, or ``None``."""

    @abstractmethod
    async def click(
        self, selector: str, *, button: str = "left", timeout_ms: int | None = None
    ) -> None:
        """Click the first element matching ``selector``."""

    @abstractmethod
    async def fill(self, selector: str, text: str, *, timeout_ms: int | None = None) -> None:
        """Replace the value of the input matching ``selector`` with ``text``."""

    @abstractmethod
    async def press(self, selector: str, key: str, *, timeout_ms: int | None = None) -> None:
        """Focus ``selector`` and press ``key`` (e.g. ``Enter``)."""

    @abstractmethod
    async def select_option(
        self, selector: str, value: str, *, timeout_ms: int | None = None
    ) -> list[str]:
        """Select ``value`` in a ``<select>`` and return the selected values."""

    @abstractmethod
    async def wait_for_selector(
        self, selector: str, *, state: str = "visible", timeout_ms: int | None = None
    ) -> None:
        """Wait until ``selector`` reaches ``state`` (visible/attached/…)."""

    @abstractmethod
    async def screenshot(self, path: str | Path, *, full_page: bool = False) -> Path:
        """Write a PNG screenshot to ``path`` and return that path."""

    @abstractmethod
    async def download(
        self, selector: str, path: str | Path, *, timeout_ms: int | None = None
    ) -> Path:
        """Click ``selector``, wait for the download, and save it to ``path``."""

    @abstractmethod
    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        """Run ``expression`` in the page, passing ``arg``, and return its value.

        ``arg`` is Playwright's single serializable argument, so a caller never
        has to interpolate data (a selector, a limit) into JavaScript source —
        the injection-safe way to parameterise an evaluation.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the page."""


class BrowserContext(ABC):
    """An isolated browser context: one agent's cookie jar, storage and pages."""

    @property
    @abstractmethod
    def context_id(self) -> str:
        """A stable id for this context, for logs and the session record."""

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        """Whether the context has been closed."""

    @abstractmethod
    async def new_page(self) -> BrowserPage:
        """Open a new page in this context."""

    @abstractmethod
    async def pages(self) -> Sequence[BrowserPage]:
        """The pages currently open in this context."""

    @abstractmethod
    async def cookies(self) -> list[dict[str, Any]]:
        """The context's cookies (all URLs)."""

    @abstractmethod
    async def add_cookies(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        """Add cookies to the context."""

    @abstractmethod
    async def clear_cookies(self) -> None:
        """Remove every cookie from the context."""

    @abstractmethod
    async def close(self) -> None:
        """Close the context and every page it owns."""


class BrowserRuntime(ABC):
    """Launches the browser and hands out isolated contexts.

    One runtime is shared by every session; isolation is at the context level,
    not the runtime level, because launching a browser per agent would exhaust a
    2.6 GiB server in seconds.
    """

    name: str
    """Runtime identifier (``"playwright"``)."""

    @property
    @abstractmethod
    def is_started(self) -> bool:
        """Whether the underlying browser process is up."""

    @abstractmethod
    async def start(self) -> None:
        """Launch the browser. Idempotent; raises ``DependencyUnavailableError``
        when Playwright or its browsers are missing."""

    @abstractmethod
    async def new_context(
        self,
        *,
        block_media: bool = False,
        accept_downloads: bool = True,
        profile_dir: str | None = None,
    ) -> BrowserContext:
        """Create a fresh isolated context (starting the browser if needed).

        ``profile_dir`` selects a *persistent* context rooted at that directory
        (cookies survive the session); ``None`` yields an ephemeral context.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Close the browser and release the Playwright driver. Idempotent."""


class PlaywrightRuntime(BrowserRuntime):
    """The concrete Playwright/Chromium runtime (spec §11).

    Chromium is chosen deliberately: it is the only engine Playwright bundles on
    every platform, and §11's capability set — DOM automation — does not need a
    specific engine's quirks.
    """

    name = "playwright"

    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._playwright: Any = None
        self._browser: Any = None
        self._browser_type: Any = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise DependencyUnavailableError(
                    "playwright",
                    reason="the 'playwright' package is not installed",
                    cause=exc,
                ) from exc
            self._playwright = await async_playwright().start()
            self._browser_type = self._playwright.chromium
            launch_kwargs: dict[str, Any] = {"headless": self._settings.headless}
            if self._settings.executable_path:
                launch_kwargs["executable_path"] = self._settings.executable_path
            try:
                self._browser = await self._browser_type.launch(**launch_kwargs)
            except Exception as exc:
                await self._stop_driver()
                raise DependencyUnavailableError(
                    "playwright",
                    reason=(
                        "the Chromium browser could not be launched; run "
                        "'playwright install chromium'"
                    ),
                    cause=exc,
                ) from exc
            self._started = True

    async def new_context(
        self,
        *,
        block_media: bool = False,
        accept_downloads: bool = True,
        profile_dir: str | None = None,
    ) -> BrowserContext:
        if not self._started:
            await self.start()
        timeout_ms = self._settings.timeout * 1000
        context_kwargs: dict[str, Any] = {"accept_downloads": accept_downloads}
        try:
            if profile_dir:
                raw_context = await self._browser_type.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=self._settings.headless,
                    **context_kwargs,
                )
            else:
                raw_context = await self._browser.new_context(**context_kwargs)
        except Exception as exc:
            raise BrowserError(
                "could not create a browser context",
                details={"reason": str(exc)[:300]},
                cause=exc,
            ) from exc
        handle = _PlaywrightContext(raw_context, block_media=block_media, timeout_ms=timeout_ms)
        await handle.initialize()
        return handle

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is not None:
                with contextlib.suppress(Exception):
                    await self._browser.close()
                self._browser = None
            await self._stop_driver()
            self._started = False

    async def _stop_driver(self) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
        self._browser_type = None


async def _block_resource(route: Any) -> None:
    """Abort image/media/font requests; continue everything else."""
    try:
        if getattr(route.request, "resource_type", None) in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        await route.continue_()


class _PlaywrightContext(BrowserContext):
    """Wraps a Playwright ``BrowserContext`` as a :class:`BrowserContext`."""

    def __init__(self, raw_context: Any, *, block_media: bool, timeout_ms: int) -> None:
        self._context = raw_context
        self._block_media = block_media
        self._timeout_ms = timeout_ms
        self._context_id = uuid.uuid4().hex
        self._closed = False

    async def initialize(self) -> None:
        """Apply default timeout and (optionally) install the media blocker."""
        self._context.set_default_timeout(self._timeout_ms)
        if self._block_media:
            await self._context.route("**/*", _block_resource)

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def new_page(self) -> BrowserPage:
        raw_page = await self._context.new_page()
        return _PlaywrightPage(raw_page, default_timeout_ms=self._timeout_ms)

    async def pages(self) -> list[BrowserPage]:
        return [
            _PlaywrightPage(page, default_timeout_ms=self._timeout_ms)
            for page in self._context.pages
        ]

    async def cookies(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._context.cookies()
        return result

    async def add_cookies(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        await self._context.add_cookies([dict(cookie) for cookie in cookies])

    async def clear_cookies(self) -> None:
        await self._context.clear_cookies()

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self._context.close()
        finally:
            self._closed = True


class _PlaywrightPage(BrowserPage):
    """Wraps a Playwright ``Page`` as a :class:`BrowserPage`."""

    def __init__(self, page: Any, *, default_timeout_ms: int) -> None:
        self._page = page
        self._timeout_ms = default_timeout_ms

    def _timeout(self, timeout_ms: int | None) -> int:
        return timeout_ms if timeout_ms is not None else self._timeout_ms

    @property
    def url(self) -> str:
        return str(self._page.url)

    async def goto(
        self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None
    ) -> NavigationResult:
        if wait_until not in _VALID_WAIT_UNTIL:
            raise BrowserError(
                f"invalid wait_until '{wait_until}'",
                details={"allowed": sorted(_VALID_WAIT_UNTIL)},
            )
        response = await self._page.goto(
            url, wait_until=wait_until, timeout=self._timeout(timeout_ms)
        )
        status = response.status if response is not None else None
        try:
            title = await self._page.title()
        except Exception:
            title = ""
        return NavigationResult(url=self.url, status=status, title=title)

    async def title(self) -> str:
        return str(await self._page.title())

    async def content(self) -> str:
        return str(await self._page.content())

    async def inner_text(self, selector: str, *, timeout_ms: int | None = None) -> str:
        return str(await self._page.inner_text(selector, timeout=self._timeout(timeout_ms)))

    async def attribute(
        self, selector: str, name: str, *, timeout_ms: int | None = None
    ) -> str | None:
        value = await self._page.get_attribute(selector, name, timeout=self._timeout(timeout_ms))
        return None if value is None else str(value)

    async def click(
        self, selector: str, *, button: str = "left", timeout_ms: int | None = None
    ) -> None:
        await self._page.click(selector, button=button, timeout=self._timeout(timeout_ms))

    async def fill(self, selector: str, text: str, *, timeout_ms: int | None = None) -> None:
        await self._page.fill(selector, text, timeout=self._timeout(timeout_ms))

    async def press(self, selector: str, key: str, *, timeout_ms: int | None = None) -> None:
        await self._page.press(selector, key, timeout=self._timeout(timeout_ms))

    async def select_option(
        self, selector: str, value: str, *, timeout_ms: int | None = None
    ) -> list[str]:
        selected = await self._page.select_option(
            selector, value, timeout=self._timeout(timeout_ms)
        )
        return [str(item) for item in (selected or [])]

    async def wait_for_selector(
        self, selector: str, *, state: str = "visible", timeout_ms: int | None = None
    ) -> None:
        await self._page.wait_for_selector(selector, state=state, timeout=self._timeout(timeout_ms))

    async def screenshot(self, path: str | Path, *, full_page: bool = False) -> Path:
        target = Path(path)
        await self._page.screenshot(path=str(target), full_page=full_page)
        return target

    async def download(
        self, selector: str, path: str | Path, *, timeout_ms: int | None = None
    ) -> Path:
        target = Path(path)
        async with self._page.expect_download(timeout=self._timeout(timeout_ms)) as info:
            await self._page.click(selector, timeout=self._timeout(timeout_ms))
        download = await info.value
        await download.save_as(str(target))
        return target

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        return await self._page.evaluate(expression, arg)

    async def close(self) -> None:
        await self._page.close()
