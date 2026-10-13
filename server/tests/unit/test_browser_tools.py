"""T112 tests: the ``browser.*`` tool set (spec §11).

The tools are exercised against the in-memory runtime fakes from T111
(:mod:`tests.unit.test_browser_session`), so no real browser is needed. They are
driven both directly (``execute``/``verify``) and — for the agent-context wiring —
through :class:`~app.tools.executor.ToolExecutor`, which is the component that
binds the ambient agent id the tools resolve their session from. The handful of
tests that need a real Playwright browser live in the Phase 6 suite and are marked
``requires_playwright`` (T116).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.config.settings import BrowserSettings, WorkspaceSettings
from app.core.errors import (
    BrowserError,
    DependencyUnavailableError,
    InvalidInputError,
    PermissionDeniedError,
    SsrfBlockedError,
)
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.observability.logging import agent_context
from app.security.audit import AuditLogger
from app.tools import browser as browser_pkg
from app.tools.bootstrap import build_tool_registry
from app.tools.browser import session as session_module, tools as tools_module
from app.tools.browser.session import BrowserSessionManager
from app.tools.browser.tools import build_browser_tools
from app.tools.executor import ToolExecutor
from app.tools.filesystem import FilesystemSandbox
from app.tools.registry import ToolRegistry
from app.workspaces.manager import SubprocessGitRunner
from tests.unit import test_browser_session as tbs

pytestmark = pytest.mark.unit

_BROWSER_TOOLS = frozenset(
    {
        "browser.open",
        "browser.click",
        "browser.type",
        "browser.select",
        "browser.inspect",
        "browser.extract",
        "browser.screenshot",
        "browser.download",
        "browser.cookies",
        "browser.set_cookies",
        "browser.close",
    }
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def playwright_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the availability probe report success so ``acquire`` runs the path."""
    monkeypatch.setattr(session_module, "playwright_unavailable_reason", lambda: None)


@pytest.fixture
def evaluate_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the fake page a deterministic ``evaluate`` for inspect/extract."""

    async def fake_evaluate(self: Any, expression: str, arg: Any = None) -> Any:
        if "querySelectorAll" in expression:
            return [
                {
                    "tag": "a",
                    "id": "link",
                    "classes": ["nav"],
                    "text": "Home",
                    "href": "https://example.com/home",
                }
            ]
        if "outerHTML" in expression:
            return "<a id='link'>Home</a>"
        if "innerText" in expression:
            return "page text"
        return None

    monkeypatch.setattr(tbs.FakePage, "evaluate", fake_evaluate)


def _settings(tmp_path: Path, **overrides: Any) -> BrowserSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "screenshot_dir": str(tmp_path / "shots"),
        "download_dir": str(tmp_path / "downloads"),
    }
    base.update(overrides)
    return BrowserSettings(**base)


def _manager(tmp_path: Path, runtime: Any, **overrides: Any) -> BrowserSessionManager:
    return BrowserSessionManager(_settings(tmp_path, **overrides), runtime=runtime)


def _registry(tmp_path: Path, runtime: Any, **overrides: Any) -> tuple[ToolRegistry, Any]:
    manager = _manager(tmp_path, runtime, **overrides)
    registry = ToolRegistry()
    for tool in build_browser_tools(manager):
        registry.register(tool)
    return registry, manager


def _executor(registry: ToolRegistry, level: PermissionLevel, audit_sink: Any) -> ToolExecutor:
    return ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink), default_level=level
        ),
        events=EventBus(),
    )


# --------------------------------------------------------------------------- #
# Inventory + contract (§11/§14)
# --------------------------------------------------------------------------- #
class TestInventory:
    def test_family_exposes_every_capability(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        assert {tool.name for tool in registry.list()} == _BROWSER_TOOLS

    def test_every_tool_declares_the_contract(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        for tool in registry.list():
            assert tool.input_schema.get("type") == "object"
            assert isinstance(tool.permission_level, PermissionLevel)
            assert tool.timeout > 0
            assert tool.description

    def test_permission_levels(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        expected = {
            "browser.open": PermissionLevel.READ_ONLY,
            "browser.inspect": PermissionLevel.READ_ONLY,
            "browser.extract": PermissionLevel.READ_ONLY,
            "browser.cookies": PermissionLevel.READ_ONLY,
            "browser.click": PermissionLevel.SAFE_ACTIONS,
            "browser.type": PermissionLevel.SAFE_ACTIONS,
            "browser.select": PermissionLevel.SAFE_ACTIONS,
            "browser.screenshot": PermissionLevel.SAFE_ACTIONS,
            "browser.set_cookies": PermissionLevel.SAFE_ACTIONS,
            "browser.close": PermissionLevel.SAFE_ACTIONS,
            "browser.download": PermissionLevel.MODIFY_PROJECT,
        }
        for name, level in expected.items():
            assert registry.lookup(name).permission_level is level

    def test_package_reexports_the_family(self) -> None:
        assert browser_pkg.build_browser_tools is tools_module.build_browser_tools
        for cls_name in ("BrowserOpenTool", "BrowserClickTool", "BrowserCloseTool"):
            assert getattr(browser_pkg, cls_name) is getattr(tools_module, cls_name)


# --------------------------------------------------------------------------- #
# Registration: bootstrap includes the family only when enabled (§59.6)
# --------------------------------------------------------------------------- #
class TestRegistration:
    def _settings(self, tmp_path: Path, *, enabled: bool) -> Settings:
        return Settings(
            workspaces=WorkspaceSettings(root=str(tmp_path / "ws")),
            browser_enabled=enabled,
        )

    def _build(self, settings: Settings) -> ToolRegistry:
        manager = BrowserSessionManager(settings.browser, runtime=tbs.FakeRuntime())
        return build_tool_registry(
            settings,
            sandbox=FilesystemSandbox(settings.workspaces),
            git_runner=SubprocessGitRunner(),
            browser=manager,
        )

    def test_enabled_settings_register_the_browser_family(self, tmp_path: Path) -> None:
        registry = self._build(self._settings(tmp_path, enabled=True))
        names = {tool.name for tool in registry.list()}
        assert names >= _BROWSER_TOOLS

    def test_disabled_settings_omit_the_browser_family(self, tmp_path: Path) -> None:
        registry = self._build(self._settings(tmp_path, enabled=False))
        names = {tool.name for tool in registry.list()}
        assert not any(name.startswith("browser.") for name in names)

    def test_container_wires_the_browser_manager(self, tmp_path: Path) -> None:
        from app.container import Container

        settings = self._settings(tmp_path, enabled=True)
        container = Container(settings)
        assert container.browser_sessions.enabled is True
        names = {tool.name for tool in container.tools.list()}
        assert names >= _BROWSER_TOOLS


# --------------------------------------------------------------------------- #
# Agent context
# --------------------------------------------------------------------------- #
class TestAgentContext:
    async def test_tool_refuses_without_agent_context(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.cookies")
        with pytest.raises(BrowserError) as info:
            await tool.execute({})
        assert info.value.details["tool"] == "browser.cookies"

    async def test_executor_binds_the_agent_id(self, tmp_path: Path, audit_sink: Any) -> None:
        # No manual ``agent_context``: the executor is what must bind it (§16).
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        result = await _executor(registry, PermissionLevel.READ_ONLY, audit_sink).run(
            "browser.cookies", {}, principal="user:test", agent_id="agent-x"
        )
        assert result.output["count"] == 0

    async def test_each_agent_gets_an_isolated_session(self, tmp_path: Path) -> None:
        registry, manager = _registry(tmp_path, tbs.FakeRuntime())
        cookies = registry.lookup("browser.cookies")
        set_cookies = registry.lookup("browser.set_cookies")

        with agent_context("agent-a"):
            await set_cookies.execute({"cookies": [{"name": "sid", "value": "a-secret"}]})
            a = await cookies.execute({})
        with agent_context("agent-b"):
            b = await cookies.execute({})

        assert a["count"] == 1
        assert b["count"] == 0
        assert manager.count == 2


# --------------------------------------------------------------------------- #
# Navigation (§11/§31)
# --------------------------------------------------------------------------- #
class TestOpen:
    async def test_open_navigates_and_reports(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.open")
        with agent_context("agent-a"):
            result = await tool.execute({"url": "https://example.com"})
        assert result == {"url": "https://example.com", "status": 200, "title": "Fake Title"}
        assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS

    async def test_open_passes_wait_until(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        registry, _ = _registry(tmp_path, runtime)
        tool = registry.lookup("browser.open")
        with agent_context("agent-a"):
            await tool.execute({"url": "https://example.com", "wait_until": "networkidle"})
        page = (await runtime.contexts[0].pages())[0]
        assert ("goto", ("https://example.com", "networkidle", None)) in page.calls

    async def test_open_blocks_private_host(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())  # allow_private_networks=False
        tool = registry.lookup("browser.open")
        with agent_context("agent-a"), pytest.raises(SsrfBlockedError):
            await tool.execute({"url": "http://127.0.0.1/"})

    async def test_open_can_allow_private_when_configured(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        manager = _manager(tmp_path, runtime)
        registry = ToolRegistry()
        for tool in build_browser_tools(manager, allow_private_networks=True):
            registry.register(tool)
        tool = registry.lookup("browser.open")
        with agent_context("agent-a"):
            result = await tool.execute({"url": "http://127.0.0.1:9999/"})
        assert result["status"] == 200

    async def test_verify_failed_on_server_error(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.open")
        assert await tool.verify({"status": 500}, {}) is VerificationOutcome.FAILED

    async def test_verify_unverified_when_no_status(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.open")
        assert await tool.verify({"status": None}, {}) is VerificationOutcome.UNVERIFIED


# --------------------------------------------------------------------------- #
# Interaction
# --------------------------------------------------------------------------- #
class TestInteraction:
    async def test_click_records_call(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        registry, _ = _registry(tmp_path, runtime)
        tool = registry.lookup("browser.click")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#submit", "button": "left"})
        assert result == {"clicked": "#submit", "button": "left"}
        page = (await runtime.contexts[0].pages())[0]
        assert ("click", ("#submit", "left")) in page.calls

    async def test_type_fills_and_submits(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        registry, _ = _registry(tmp_path, runtime)
        tool = registry.lookup("browser.type")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#q", "text": "hello", "submit": True})
        assert result == {"filled": "#q", "length": 5, "submitted": True}
        page = (await runtime.contexts[0].pages())[0]
        assert ("fill", ("#q", "hello")) in page.calls
        assert ("press", ("#q", "Enter")) in page.calls

    async def test_select_records_value(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        registry, _ = _registry(tmp_path, runtime)
        tool = registry.lookup("browser.select")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#size", "value": "l"})
        assert result == {"selector": "#size", "selected": ["l"]}


# --------------------------------------------------------------------------- #
# Inspect + extract
# --------------------------------------------------------------------------- #
class TestInspectExtract:
    async def test_inspect_returns_elements(
        self, tmp_path: Path, evaluate_stub: None
    ) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.inspect")
        with agent_context("agent-a"):
            await registry.lookup("browser.open").execute({"url": "https://example.com"})
            result = await tool.execute({"selector": "a"})
        assert result["url"] == "https://example.com"
        assert result["title"] == "Fake Title"
        assert result["count"] == 1
        assert result["elements"][0]["href"] == "https://example.com/home"
        assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS

    async def test_inspect_without_selector_has_no_elements(
        self, tmp_path: Path, evaluate_stub: None
    ) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.inspect")
        with agent_context("agent-a"):
            result = await tool.execute({})
        assert result["elements"] == []
        assert result["count"] == 0

    async def test_extract_selector_text(self, tmp_path: Path) -> None:
        runtime = tbs.FakeRuntime()
        registry, _ = _registry(tmp_path, runtime)
        tool = registry.lookup("browser.extract")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#body"})
        assert result["text"] == "text"
        assert result["format"] == "text"
        page = (await runtime.contexts[0].pages())[0]
        assert ("inner_text", ("#body",)) in page.calls

    async def test_extract_whole_page_text(
        self, tmp_path: Path, evaluate_stub: None
    ) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.extract")
        with agent_context("agent-a"):
            result = await tool.execute({})
        assert result["text"] == "page text"

    async def test_extract_whole_page_html(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.extract")
        with agent_context("agent-a"):
            result = await tool.execute({"format": "html"})
        assert result["text"] == "<html></html>"

    async def test_extract_truncates(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.extract")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#body", "max_chars": 2})
        assert result["text"] == "te"
        assert result["truncated"] is True


# --------------------------------------------------------------------------- #
# Artifacts: screenshot + download
# --------------------------------------------------------------------------- #
class TestArtifacts:
    async def test_screenshot_writes_into_the_configured_dir(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.screenshot")
        with agent_context("agent-a"):
            result = await tool.execute({"full_page": True})
        path = Path(result["path"])
        assert path.parent == tmp_path / "shots"
        assert path.suffix == ".png"
        assert result["full_page"] is True
        assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS

    async def test_screenshot_sanitizes_the_name(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.screenshot")
        with agent_context("agent-a"):
            result = await tool.execute({"name": "../../evil shot"})
        name = Path(result["path"]).name
        assert ".." not in name
        assert "/" not in name
        assert "\\" not in name
        assert name.startswith("evil_shot")

    async def test_download_writes_into_the_configured_dir(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.download")
        with agent_context("agent-a"):
            result = await tool.execute({"selector": "#dl", "name": "report"})
        path = Path(result["path"])
        assert path.parent == tmp_path / "downloads"
        assert path.name.startswith("report")
        assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS


# --------------------------------------------------------------------------- #
# Cookies + close
# --------------------------------------------------------------------------- #
class TestSession:
    async def test_set_then_get_cookies(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        set_tool = registry.lookup("browser.set_cookies")
        get_tool = registry.lookup("browser.cookies")
        with agent_context("agent-a"):
            added = await set_tool.execute({"cookies": [{"name": "sid", "value": "1"}]})
            got = await get_tool.execute({})
        assert added == {"added": 1, "cleared": False, "count": 1}
        assert got["count"] == 1
        assert await get_tool.verify(got, {}) is VerificationOutcome.SUCCESS

    async def test_clear_cookies(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        set_tool = registry.lookup("browser.set_cookies")
        with agent_context("agent-a"):
            await set_tool.execute({"cookies": [{"name": "sid", "value": "1"}]})
            cleared = await set_tool.execute({"clear": True})
        assert cleared["cleared"] is True
        assert cleared["count"] == 0

    async def test_set_cookies_rejects_non_array(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.set_cookies")
        with agent_context("agent-a"), pytest.raises(InvalidInputError):
            await tool.execute({"cookies": "not-a-list"})

    async def test_close_releases_the_session(self, tmp_path: Path) -> None:
        registry, manager = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.close")
        with agent_context("agent-a"):
            await registry.lookup("browser.open").execute({"url": "https://example.com"})
            assert manager.count == 1
            result = await tool.execute({})
        assert result == {"closed": True, "agent_id": "agent-a"}
        assert manager.count == 0
        assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS

    async def test_close_without_a_session_reports_false(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        tool = registry.lookup("browser.close")
        with agent_context("agent-a"):
            result = await tool.execute({})
        assert result["closed"] is False


# --------------------------------------------------------------------------- #
# Disabled / unavailable behaviour
# --------------------------------------------------------------------------- #
class TestUnavailable:
    async def test_disabled_manager_refuses(self, tmp_path: Path) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime(), enabled=False)
        tool = registry.lookup("browser.open")
        with agent_context("agent-a"), pytest.raises(DependencyUnavailableError) as info:
            await tool.execute({"url": "https://example.com"})
        assert info.value.details["dependency"] == "browser"

    async def test_permission_denied_for_download_below_level(
        self, tmp_path: Path, audit_sink: Any
    ) -> None:
        registry, _ = _registry(tmp_path, tbs.FakeRuntime())
        executor = _executor(registry, PermissionLevel.SAFE_ACTIONS, audit_sink)
        with pytest.raises(PermissionDeniedError):
            await executor.run(
                "browser.download",
                {"selector": "#dl"},
                principal="user:test",
                agent_id="agent-x",
            )
