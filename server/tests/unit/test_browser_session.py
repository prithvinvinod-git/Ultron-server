"""T111 tests: per-agent browser session isolation and lifecycle (spec §11).

The session manager never imports Playwright, so the whole manager is exercised
here with an in-memory runtime. The handful of tests that need a real browser
live in the Phase 6 browser-tools module and are marked ``requires_playwright``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import BrowserSettings
from app.core.errors import DependencyUnavailableError
from app.tools.browser import session as session_module
from app.tools.browser.availability import playwright_available, playwright_unavailable_reason
from app.tools.browser.runtime import (
    BrowserContext,
    BrowserPage,
    BrowserRuntime,
    NavigationResult,
)
from app.tools.browser.session import BrowserSessionManager, _safe_agent_dir

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# In-memory fakes
# --------------------------------------------------------------------------- #
class FakePage(BrowserPage):
    def __init__(self, context_id: str) -> None:
        self._context_id = context_id
        self._url = ""
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def url(self) -> str:
        return self._url

    async def goto(
        self, url: str, *, wait_until: str = "load", timeout_ms: int | None = None
    ) -> NavigationResult:
        self._url = url
        self.calls.append(("goto", (url, wait_until, timeout_ms)))
        return NavigationResult(url=url, status=200, title="Fake Title")

    async def title(self) -> str:
        return "Fake Title"

    async def content(self) -> str:
        return "<html></html>"

    async def inner_text(self, selector: str, *, timeout_ms: int | None = None) -> str:
        self.calls.append(("inner_text", (selector,)))
        return "text"

    async def attribute(
        self, selector: str, name: str, *, timeout_ms: int | None = None
    ) -> str | None:
        return None

    async def click(
        self, selector: str, *, button: str = "left", timeout_ms: int | None = None
    ) -> None:
        self.calls.append(("click", (selector, button)))

    async def fill(self, selector: str, text: str, *, timeout_ms: int | None = None) -> None:
        self.calls.append(("fill", (selector, text)))

    async def press(self, selector: str, key: str, *, timeout_ms: int | None = None) -> None:
        self.calls.append(("press", (selector, key)))

    async def select_option(
        self, selector: str, value: str, *, timeout_ms: int | None = None
    ) -> list[str]:
        self.calls.append(("select_option", (selector, value)))
        return [value]

    async def wait_for_selector(
        self, selector: str, *, state: str = "visible", timeout_ms: int | None = None
    ) -> None:
        self.calls.append(("wait_for_selector", (selector, state)))

    async def screenshot(self, path: str | Path, *, full_page: bool = False) -> Path:
        return Path(path)

    async def download(
        self, selector: str, path: str | Path, *, timeout_ms: int | None = None
    ) -> Path:
        return Path(path)

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        self.calls.append(("evaluate", (expression, arg)))
        return None

    async def close(self) -> None:
        self.closed = True


class FakeContext(BrowserContext):
    def __init__(self, context_id: str) -> None:
        self._context_id = context_id
        self._closed = False
        self._pages: list[FakePage] = []
        self._cookies: list[dict[str, Any]] = []

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def new_page(self) -> FakePage:
        page = FakePage(self._context_id)
        self._pages.append(page)
        return page

    async def pages(self) -> list[FakePage]:
        return list(self._pages)

    async def cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)

    async def add_cookies(self, cookies: Any) -> None:
        self._cookies.extend(dict(c) for c in cookies)

    async def clear_cookies(self) -> None:
        self._cookies.clear()

    async def close(self) -> None:
        self._closed = True
        for page in self._pages:
            page.closed = True


class FakeRuntime(BrowserRuntime):
    name = "fake"

    def __init__(self, *, fail_start: bool = False) -> None:
        self._started = False
        self._counter = 0
        self.contexts: list[FakeContext] = []
        self.profile_dirs: list[str | None] = []
        self.stopped = False
        self.fail_start = fail_start

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self.fail_start:
            raise DependencyUnavailableError("playwright", reason="no browser", cause=None)
        self._started = True

    async def new_context(
        self,
        *,
        block_media: bool = False,
        accept_downloads: bool = True,
        profile_dir: str | None = None,
    ) -> FakeContext:
        if not self._started:
            await self.start()
        self._counter += 1
        context = FakeContext(f"ctx-{self._counter}")
        self.contexts.append(context)
        self.profile_dirs.append(profile_dir)
        return context

    async def stop(self) -> None:
        self._started = False
        self.stopped = True


@pytest.fixture
def playwright_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend Playwright is importable, so acquire exercises the real path."""
    monkeypatch.setattr(session_module, "playwright_unavailable_reason", lambda: None)


def _settings(**overrides: Any) -> BrowserSettings:
    base: dict[str, Any] = {"enabled": True}
    base.update(overrides)
    return BrowserSettings(**base)


def _manager(runtime: FakeRuntime, **overrides: Any) -> BrowserSessionManager:
    return BrowserSessionManager(_settings(**overrides), runtime=runtime)


# --------------------------------------------------------------------------- #
# Availability probe
# --------------------------------------------------------------------------- #
class TestAvailability:
    def test_probe_returns_bool(self) -> None:
        assert isinstance(playwright_available(), bool)

    def test_reason_matches_availability(self) -> None:
        reason = playwright_unavailable_reason()
        if playwright_available():
            assert reason is None
        else:
            assert reason is not None
            assert "playwright" in reason

    def test_fake_is_a_runtime(self) -> None:
        assert isinstance(FakeRuntime(), BrowserRuntime)


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
class TestIsolation:
    async def test_each_agent_gets_its_own_context(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)

        a = await manager.acquire("agent-a")
        b = await manager.acquire("agent-b")

        assert a.context_id != b.context_id
        assert len(runtime.contexts) == 2
        assert manager.count == 2
        assert set(manager.agents()) == {"agent-a", "agent-b"}

    async def test_same_agent_reuses_its_session(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)

        first = await manager.acquire("agent-a")
        second = await manager.acquire("agent-a")

        assert first is second
        assert len(runtime.contexts) == 1

    async def test_cookies_do_not_leak_between_agents(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)

        a = await manager.acquire("agent-a")
        b = await manager.acquire("agent-b")
        await runtime.contexts[0].add_cookies([{"name": "session", "value": "a-secret"}])

        assert await a.cookies() == [{"name": "session", "value": "a-secret"}]
        assert await b.cookies() == []

    async def test_last_used_refreshes_on_reacquire(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)

        session = await manager.acquire("agent-a")
        before = session.last_used_at
        await manager.acquire("agent-a")
        assert session.last_used_at >= before


# --------------------------------------------------------------------------- #
# Capacity / eviction
# --------------------------------------------------------------------------- #
class TestCapacity:
    async def test_lru_eviction_at_capacity(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime, max_sessions=2)

        await manager.acquire("oldest")
        await asyncio.sleep(0.005)
        await manager.acquire("middle")
        await asyncio.sleep(0.005)
        # re-touch "oldest" so "middle" becomes the least-recently-used
        await manager.acquire("oldest")
        await asyncio.sleep(0.005)
        await manager.acquire("newest")

        assert manager.count == 2
        assert "middle" not in manager.agents()
        assert {*manager.agents()} == {"oldest", "newest"}
        # the evicted agent's context was actually closed
        closed = [c for c in runtime.contexts if c.is_closed]
        assert len(closed) == 1

    async def test_evicted_agent_can_be_reacquired(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime, max_sessions=1)

        await manager.acquire("agent-a")
        await manager.acquire("agent-b")

        again = await manager.acquire("agent-a")
        assert manager.count == 1
        assert again.agent_id == "agent-a"


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
class TestLifecycle:
    async def test_disabled_manager_refuses(self) -> None:
        manager = _manager(FakeRuntime(), enabled=False)
        with pytest.raises(DependencyUnavailableError) as info:
            await manager.acquire("agent-a")
        assert info.value.details["dependency"] == "browser"

    async def test_playwright_absent_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            session_module, "playwright_unavailable_reason", lambda: "not installed"
        )
        manager = _manager(FakeRuntime())
        with pytest.raises(DependencyUnavailableError) as info:
            await manager.acquire("agent-a")
        assert info.value.details["dependency"] == "playwright"

    async def test_release_closes_and_forgets(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        session = await manager.acquire("agent-a")

        assert await manager.release("agent-a") is True
        assert session.is_closed
        assert manager.get("agent-a") is None
        assert await manager.release("agent-a") is False

    async def test_close_all_keeps_manager_reusable(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        await manager.acquire("agent-a")
        await manager.acquire("agent-b")

        await manager.close_all()

        assert manager.count == 0
        assert all(c.is_closed for c in runtime.contexts)
        reacquired = await manager.acquire("agent-a")
        assert reacquired.agent_id == "agent-a"

    async def test_shutdown_stops_runtime(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        await manager.acquire("agent-a")

        await manager.shutdown()

        assert manager.count == 0
        assert runtime.stopped
        assert not runtime.is_started

    async def test_get_drops_a_closed_session(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        session = await manager.acquire("agent-a")
        await session.close()
        assert manager.get("agent-a") is None


# --------------------------------------------------------------------------- #
# Session behaviour
# --------------------------------------------------------------------------- #
class TestSession:
    async def test_goto_uses_page(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        session = await manager.acquire("agent-a")

        result = await session.goto("https://example.com")

        assert result.url == "https://example.com"
        assert result.status == 200
        assert session.url == "https://example.com"
        page = (await runtime.contexts[0].pages())[0]
        assert ("goto", ("https://example.com", "load", None)) in page.calls

    async def test_page_created_lazily(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        session = await manager.acquire("agent-a")

        assert session.page_count == 0
        await session.page()
        assert session.page_count == 1
        await session.page()
        assert session.page_count == 1

    async def test_stats_shape(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        await manager.acquire("agent-a")

        stats = manager.stats()
        assert stats["enabled"] is True
        assert stats["runtime"] == "fake"
        assert stats["sessions"] == 1
        assert stats["max_sessions"] == 4
        assert stats["agents"] == ["agent-a"]


# --------------------------------------------------------------------------- #
# Persistent profiles + path safety
# --------------------------------------------------------------------------- #
class TestProfiles:
    async def test_no_profile_dir_by_default(self, playwright_ready: None) -> None:
        runtime = FakeRuntime()
        manager = _manager(runtime)
        await manager.acquire("agent-a")
        assert runtime.profile_dirs == [None]

    async def test_persistent_profile_dir_created(
        self, playwright_ready: None, tmp_path: Path
    ) -> None:
        runtime = FakeRuntime()
        manager = _manager(
            runtime,
            persist_profile=True,
            download_dir=str(tmp_path / "downloads"),
        )
        await manager.acquire("agent-a")

        profile_dir = runtime.profile_dirs[0]
        assert profile_dir is not None
        assert Path(profile_dir).is_dir()  # noqa: ASYNC240 - test assertion, not hot path

    def test_safe_agent_dir_sanitizes(self) -> None:
        safe = _safe_agent_dir("../../evil agent")
        assert "/" not in safe
        assert "\\" not in safe
        assert ".." not in safe
        assert safe.startswith("evil_agent")
