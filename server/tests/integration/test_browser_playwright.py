"""Phase 6 browser tests against a real Chromium (T116, spec §11).

The rest of the browser suite drives the manager and the tools through an
in-memory runtime (:mod:`tests.unit.test_browser_session`), which is what makes
the isolation and tool-contract assertions fast and deterministic. But an
in-memory fake cannot prove that Playwright's *actual* isolation primitive does
what §11 requires — that two agent contexts do not share cookies, that a
navigation really reports the landing URL and title, and that extraction reads
the rendered DOM rather than the raw HTML string.

So this module is the small, honest set of tests that need the real thing. They
are marked ``requires_playwright`` and **skipped cleanly when the optional
``playwright`` dependency is absent** — the availability probe (T111) supplies
the reason, so a machine without the browser extra gets a skip with an
explanation, never an error or a false green.

Nothing here touches the network: pages are ``data:`` URLs, so the tests prove
DOM behaviour without depending on a remote host or the SSRF guard (which is
exercised where it lives, in the tool tests).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import pytest

from app.config.settings import BrowserSettings
from app.tools.browser.availability import playwright_unavailable_reason
from app.tools.browser.session import BrowserSessionManager

_REASON = playwright_unavailable_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_playwright,
    pytest.mark.skipif(_REASON is not None, reason=_REASON or ""),
]


def _data_url(html: str) -> str:
    """A self-contained page URL, so no test reaches the network."""
    return "data:text/html," + quote(html)


_PAGE = (
    "<html><head><title>Isolation Demo</title></head>"
    "<body><h1 id='heading'>Hello world</h1>"
    "<p class='note'>Second paragraph.</p></body></html>"
)


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[BrowserSessionManager]:
    """A real Playwright-backed manager, shut down (Chromium closed) after the test."""
    settings = BrowserSettings(
        enabled=True,
        headless=True,
        screenshot_dir=str(tmp_path / "shots"),
        download_dir=str(tmp_path / "downloads"),
    )
    manager = BrowserSessionManager(settings)
    try:
        yield manager
    finally:
        await manager.shutdown()


# --------------------------------------------------------------------------- #
# Isolation (§11's load-bearing rule, proven on the real runtime)
# --------------------------------------------------------------------------- #
async def test_two_agents_are_isolated_by_context(manager: BrowserSessionManager) -> None:
    first = await manager.acquire("agent-1")
    second = await manager.acquire("agent-2")

    # Distinct Playwright contexts — the isolation unit.
    assert first.context_id != second.context_id

    # A cookie set on one agent's context is invisible to the other.
    await first.add_cookies(
        [{"name": "session", "value": "one", "url": "https://example.test/"}]
    )
    assert {cookie["name"] for cookie in await first.cookies()} == {"session"}
    assert await second.cookies() == []

    # And their pages are independent: one navigates, the other is still blank.
    await first.goto(_data_url(_PAGE))
    assert first.url.startswith("data:text/html")
    assert second.url == ""


async def test_the_same_agent_reuses_one_context(manager: BrowserSessionManager) -> None:
    first = await manager.acquire("agent-1")
    again = await manager.acquire("agent-1")

    assert first is again
    assert first.context_id == again.context_id


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
async def test_navigation_reports_the_landing_url_and_title(
    manager: BrowserSessionManager,
) -> None:
    session = await manager.acquire("agent-1")

    result = await session.goto(_data_url(_PAGE))

    assert result.url.startswith("data:text/html")
    assert result.title == "Isolation Demo"
    assert session.url == result.url


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
async def test_extraction_reads_the_rendered_dom(manager: BrowserSessionManager) -> None:
    session = await manager.acquire("agent-1")
    await session.goto(_data_url(_PAGE))
    page = await session.page()

    heading = await page.inner_text("#heading")
    notes = await page.inner_text(".note")
    html = await page.content()

    assert heading == "Hello world"
    assert notes == "Second paragraph."
    assert "<h1 id='heading'>Hello world</h1>" in html or "Hello world" in html


async def test_cookies_can_be_cleared_within_a_session(
    manager: BrowserSessionManager,
) -> None:
    session = await manager.acquire("agent-1")
    await session.add_cookies(
        [{"name": "token", "value": "abc", "url": "https://example.test/"}]
    )
    assert await session.cookies()

    await session.clear_cookies()

    assert await session.cookies() == []
