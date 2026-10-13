"""The browser tool family (T111–T112, spec §11).

Playwright-backed DOM automation, isolated per agent. T111 provides the session
machinery (:class:`~app.tools.browser.session.BrowserSessionManager` and its
runtime handles); T112 adds the ``browser.*`` tool set
(:mod:`app.tools.browser.tools`) on top.
"""

from __future__ import annotations

from app.tools.browser.availability import playwright_available, playwright_unavailable_reason
from app.tools.browser.runtime import (
    BrowserContext,
    BrowserPage,
    BrowserRuntime,
    NavigationResult,
    PlaywrightRuntime,
)
from app.tools.browser.session import BrowserSession, BrowserSessionManager
from app.tools.browser.tools import (
    BrowserClickTool,
    BrowserCloseTool,
    BrowserCookiesTool,
    BrowserDownloadTool,
    BrowserExtractTool,
    BrowserInspectTool,
    BrowserOpenTool,
    BrowserScreenshotTool,
    BrowserSelectTool,
    BrowserSetCookiesTool,
    BrowserTypeTool,
    build_browser_tools,
)

__all__ = [
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserContext",
    "BrowserCookiesTool",
    "BrowserDownloadTool",
    "BrowserExtractTool",
    "BrowserInspectTool",
    "BrowserOpenTool",
    "BrowserPage",
    "BrowserRuntime",
    "BrowserScreenshotTool",
    "BrowserSelectTool",
    "BrowserSession",
    "BrowserSessionManager",
    "BrowserSetCookiesTool",
    "BrowserTypeTool",
    "NavigationResult",
    "PlaywrightRuntime",
    "build_browser_tools",
    "playwright_available",
    "playwright_unavailable_reason",
]
