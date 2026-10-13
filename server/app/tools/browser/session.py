"""Per-agent browser session management (T111, spec §11).

Spec §11's isolation rule — *browser sessions must be isolated by agent* — is
the whole reason this module exists. The shape is deliberately the one the spec
draws::

    BrowserAgent #12  →  BrowserSession #12  →  Playwright

One :class:`BrowserSession` per agent, each backed by its own Playwright
*browser context* (a fresh cookie jar, storage and page set), all sharing one
:class:`~app.tools.browser.runtime.BrowserRuntime` so a small server launches a
single Chromium rather than one per agent.

:class:`BrowserSessionManager` is the facade the tools (T112) and the agent
(T113) talk to. It is intentionally dependency-light: it knows the runtime
*interface*, never Playwright, so it is fully testable with an in-memory fake and
the real thing is a swap at construction.

Lifecycle:

- ``acquire(agent_id)`` returns that agent's session, creating the context on
  first use and refreshing its ``last_used_at`` on every call. Same agent, same
  session — that is the isolation guarantee: an agent can never see another's
  cookies.
- ``max_sessions`` is a hard resource cap. Reaching it evicts the
  **least-recently-used** session (closing its context) to make room, because
  the alternative — refusing the new agent — would let one idle agent starve the
  rest. The eviction is explicit in the returned session set, never silent.
- ``release``/``close_all``/``shutdown`` close contexts deterministically; the
  manager never leaks a Chromium context.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.settings import BrowserSettings
from app.core.errors import DependencyUnavailableError
from app.tools.browser.availability import playwright_unavailable_reason
from app.tools.browser.runtime import (
    BrowserContext,
    BrowserPage,
    BrowserRuntime,
    NavigationResult,
    PlaywrightRuntime,
)

__all__ = ["BrowserSession", "BrowserSessionManager"]


class BrowserSession:
    """One agent's isolated browser context and its current page.

    The session owns exactly one :class:`~app.tools.browser.runtime.BrowserContext`;
    pages are created lazily and the most recent one is remembered so a sequence
    of tool calls (open, click, type, extract) acts on the same tab without the
    caller passing a page handle around.
    """

    def __init__(self, *, agent_id: str, context: BrowserContext, created_at: datetime) -> None:
        self.agent_id = agent_id
        self._context = context
        self.created_at = created_at
        self.last_used_at = created_at
        self._page: BrowserPage | None = None

    @property
    def context_id(self) -> str:
        return self._context.context_id

    @property
    def is_closed(self) -> bool:
        return self._context.is_closed

    @property
    def page_count(self) -> int:
        return 1 if self._page is not None else 0

    @property
    def url(self) -> str:
        return self._page.url if self._page is not None else ""

    def touch(self) -> None:
        """Mark the session as used now (drives LRU eviction)."""
        self.last_used_at = datetime.now(UTC)

    async def page(self) -> BrowserPage:
        """The session's page, opening one on first use."""
        if self._page is None or self._context.is_closed:
            self._page = await self._context.new_page()
        return self._page

    async def goto(
        self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None
    ) -> NavigationResult:
        """Navigate the session's page to ``url``."""
        page = await self.page()
        return await page.goto(url, wait_until=wait_until, timeout_ms=timeout_ms)

    async def cookies(self) -> list[dict[str, object]]:
        """The session's isolated cookies."""
        return await self._context.cookies()

    async def add_cookies(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        """Add cookies to the session's isolated context (session handling)."""
        await self._context.add_cookies(cookies)

    async def clear_cookies(self) -> None:
        """Remove every cookie from the session's isolated context."""
        await self._context.clear_cookies()

    async def close(self) -> None:
        """Close the session's context (and every page in it)."""
        await self._context.close()
        self._page = None

    def __repr__(self) -> str:
        state = "closed" if self.is_closed else "open"
        return f"<BrowserSession agent={self.agent_id!r} {state} context={self.context_id}>"


class BrowserSessionManager:
    """Owns one :class:`BrowserSession` per agent, over a shared runtime."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        runtime: BrowserRuntime | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    # -- introspection ------------------------------------------------------ #
    @property
    def settings(self) -> BrowserSettings:
        return self._settings

    @property
    def runtime(self) -> BrowserRuntime:
        """The shared runtime, built on first access (Playwright by default)."""
        if self._runtime is None:
            self._runtime = PlaywrightRuntime(self._settings)
        return self._runtime

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def max_sessions(self) -> int:
        return self._settings.max_sessions

    @property
    def count(self) -> int:
        return len(self._sessions)

    def get(self, agent_id: str) -> BrowserSession | None:
        """The agent's live session, or ``None``."""
        session = self._sessions.get(agent_id)
        if session is not None and session.is_closed:
            del self._sessions[agent_id]
            return None
        return session

    def agents(self) -> list[str]:
        """The agent ids that currently hold a session."""
        return list(self._sessions)

    def stats(self) -> dict[str, object]:
        """A snapshot for /health and the agent's own reporting."""
        return {
            "enabled": self.enabled,
            "runtime": self.runtime.name,
            "started": self.runtime.is_started,
            "sessions": self.count,
            "max_sessions": self.max_sessions,
            "agents": self.agents(),
        }

    # -- lifecycle ---------------------------------------------------------- #
    async def acquire(self, agent_id: str) -> BrowserSession:
        """Return the agent's session, creating it (and a context) on first use.

        Raises ``DependencyUnavailableError`` when the browser subsystem is
        disabled or Playwright is absent, and ``BrowserError`` when a context
        cannot be created. Reaching ``max_sessions`` evicts the least-recently-used
        session to make room.
        """
        if not self.enabled:
            raise DependencyUnavailableError(
                "browser", reason="BROWSER_ENABLED is false", cause=None
            )
        existing = self.get(agent_id)
        if existing is not None:
            existing.touch()
            return existing

        async with self._lock:
            existing = self.get(agent_id)
            if existing is not None:
                existing.touch()
                return existing

            reason = playwright_unavailable_reason()
            if reason is not None:
                raise DependencyUnavailableError("playwright", reason=reason, cause=None)

            await self._make_room()
            context = await self.runtime.new_context(
                block_media=self._settings.block_media,
                accept_downloads=True,
                profile_dir=self._profile_dir(agent_id),
            )
            now = datetime.now(UTC)
            session = BrowserSession(agent_id=agent_id, context=context, created_at=now)
            self._sessions[agent_id] = session
            return session

    async def release(self, agent_id: str) -> bool:
        """Close and forget the agent's session. Returns whether one existed."""
        session = self._sessions.pop(agent_id, None)
        if session is None:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        """Close every session's context, keeping the manager reusable."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            with contextlib.suppress(Exception):
                await session.close()

    async def shutdown(self) -> None:
        """Close every session and stop the shared runtime."""
        await self.close_all()
        if self._runtime is not None:
            await self._runtime.stop()

    # -- internals ---------------------------------------------------------- #
    async def _make_room(self) -> None:
        """Evict the least-recently-used session while at capacity."""
        while len(self._sessions) >= self.max_sessions:
            victim_id = min(self._sessions, key=lambda aid: self._sessions[aid].last_used_at)
            victim = self._sessions.pop(victim_id)
            await victim.close()

    def _profile_dir(self, agent_id: str) -> str | None:
        """A persistent profile directory for the agent, or ``None``.

        Only used when ``BROWSER_PERSIST_PROFILE`` is on; derived from the
        download root so all browser state lives under one data directory.
        """
        if not self._settings.persist_profile:
            return None
        safe = _safe_agent_dir(agent_id)
        profiles = Path(self._settings.download_dir).parent / "browser_profiles" / safe
        profiles.mkdir(parents=True, exist_ok=True)
        return str(profiles)


def _safe_agent_dir(agent_id: str) -> str:
    """A filesystem-safe directory name for an agent id (§59.4: resolve before trust)."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in agent_id)
    cleaned = cleaned.strip("._") or "agent"
    return f"{cleaned}-{uuid.uuid5(uuid.NAMESPACE_URL, agent_id).hex[:8]}"
