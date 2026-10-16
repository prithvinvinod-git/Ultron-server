"""Computer tool tests (T143/T147, spec §13/§64.11).

The tools are thin declarations over the gateway: they name a capability, resolve
a target, strip the routing argument and dispatch. These tests pin the parts that
are only true of the *tools* — target resolution by id and by kind, capability
gating, the ``node`` argument not leaking into the command, and the verification
verdicts — over the T146 double, so nothing here needs a node.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.computer.gateway import NodeGateway
from app.agents.computer.protocol import NodeRegistration
from app.config.settings import ComputerNodeSettings
from app.core.errors import InvalidInputError, NodeError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.computer.tools import (
    ComputerClickTool,
    ComputerCloseApplicationTool,
    ComputerKeysTool,
    ComputerListWindowsTool,
    ComputerOpenApplicationTool,
    ComputerScreenshotTool,
    ComputerTypeTool,
    build_computer_tools,
)
from tests.support.node_double import FakeNode

pytestmark = pytest.mark.unit

_ALL_CAPABILITIES = ("windows_ui", "screenshots", "windows_apps")


@pytest.fixture
def gateway() -> NodeGateway:
    return NodeGateway(settings=ComputerNodeSettings(), require_auth=False)


async def _register(gateway: NodeGateway, node: FakeNode) -> FakeNode:
    node.bind(gateway)
    registration = NodeRegistration.model_validate(node.registration(credential=""))
    await gateway.register(registration, node.transport)
    return node


def _handlers(**by_tool: Any) -> dict[str, Any]:
    return dict(by_tool)


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #


class TestInventory:
    def test_builds_seven_tools(self, gateway: NodeGateway) -> None:
        tools = build_computer_tools(gateway)
        assert len(tools) == 7
        assert len({tool.name for tool in tools}) == 7

    def test_every_tool_is_window_scoped(self, gateway: NodeGateway) -> None:
        assert all(tool.node_scope == "windows" for tool in build_computer_tools(gateway))

    def test_only_the_screenshot_is_audited(self, gateway: NodeGateway) -> None:
        audited = {tool.name for tool in build_computer_tools(gateway) if tool.audit}
        assert audited == {"computer.screenshot"}

    def test_permission_levels(self, gateway: NodeGateway) -> None:
        levels = {tool.name: tool.permission_level for tool in build_computer_tools(gateway)}
        assert levels["computer.list_windows"] is PermissionLevel.READ_ONLY
        assert levels["computer.screenshot"] is PermissionLevel.READ_ONLY
        assert levels["computer.click"] is PermissionLevel.SAFE_ACTIONS
        assert levels["computer.type"] is PermissionLevel.SAFE_ACTIONS
        assert levels["computer.keys"] is PermissionLevel.SAFE_ACTIONS
        assert levels["computer.open_application"] is PermissionLevel.EXECUTE_PROGRAMS
        assert levels["computer.close_application"] is PermissionLevel.SAFE_ACTIONS


# --------------------------------------------------------------------------- #
# Dispatch and target resolution
# --------------------------------------------------------------------------- #


class TestDispatch:
    async def test_list_windows_dispatches_to_the_only_node(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(
            capabilities=_ALL_CAPABILITIES,
            handlers=_handlers(**{"computer.list_windows": lambda _a: {"windows": []}}),
        )
        await _register(gateway, node)
        result = await ComputerListWindowsTool(gateway).execute({})
        assert result == {"windows": []}
        assert node.transport.sent[-1]["tool"] == "computer.list_windows"

    async def test_the_node_argument_is_not_sent_to_the_node(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(
            node_id="windows-1",
            capabilities=_ALL_CAPABILITIES,
            handlers=_handlers(**{"computer.list_windows": lambda _a: {"windows": []}}),
        )
        await _register(gateway, node)
        await ComputerListWindowsTool(gateway).execute({"node": "windows-1"})
        sent = node.transport.sent[-1]
        assert "node" not in sent["arguments"]

    async def test_an_explicit_node_id_is_honoured(self, gateway: NodeGateway) -> None:
        first = FakeNode(node_id="a", capabilities=_ALL_CAPABILITIES)
        second = FakeNode(
            node_id="b",
            capabilities=_ALL_CAPABILITIES,
            handlers=_handlers(**{"computer.list_windows": lambda _a: {"windows": ["b"]}}),
        )
        await _register(gateway, first)
        await _register(gateway, second)
        result = await ComputerListWindowsTool(gateway).execute({"node": "b"})
        assert result == {"windows": ["b"]}
        assert first.transport.sent == []

    async def test_an_explicit_node_kind_resolves_by_type(self, gateway: NodeGateway) -> None:
        node = FakeNode(
            capabilities=_ALL_CAPABILITIES,
            handlers=_handlers(**{"computer.list_windows": lambda _a: {"windows": []}}),
        )
        await _register(gateway, node)
        result = await ComputerListWindowsTool(gateway).execute({"node": "windows"})
        assert result == {"windows": []}

    async def test_an_unknown_node_is_unavailable(self, gateway: NodeGateway) -> None:
        with pytest.raises(NodeError):
            await ComputerListWindowsTool(gateway).execute({"node": "ghost"})

    async def test_a_node_without_the_capability_is_unavailable(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(capabilities=("windows_ui",))
        await _register(gateway, node)
        with pytest.raises(NodeError):
            await ComputerScreenshotTool(gateway).execute({})

    async def test_no_online_node_is_unavailable(self, gateway: NodeGateway) -> None:
        with pytest.raises(NodeError):
            await ComputerListWindowsTool(gateway).execute({})

    async def test_an_offline_node_is_skipped_in_discovery(self, gateway: NodeGateway) -> None:
        offline = FakeNode(node_id="a", capabilities=_ALL_CAPABILITIES)
        online = FakeNode(
            node_id="b",
            capabilities=_ALL_CAPABILITIES,
            handlers=_handlers(**{"computer.list_windows": lambda _a: {"windows": ["b"]}}),
        )
        await _register(gateway, offline)
        await _register(gateway, online)
        await gateway.disconnect(offline.node_id)
        result = await ComputerListWindowsTool(gateway).execute({})
        assert result == {"windows": ["b"]}


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #


class TestArgumentValidation:
    async def test_click_requires_coordinates(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerClickTool(gateway).execute({"x": 1})

    async def test_click_rejects_negative_coordinates(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerClickTool(gateway).execute({"x": -1, "y": 0})

    async def test_type_requires_text(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerTypeTool(gateway).execute({})

    async def test_keys_rejects_empty_string(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerKeysTool(gateway).execute({"keys": ""})

    async def test_keys_rejects_a_list_with_an_empty_entry(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerKeysTool(gateway).execute({"keys": ["ctrl", ""]})

    async def test_keys_rejects_a_non_string(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerKeysTool(gateway).execute({"keys": 42})

    async def test_open_application_requires_a_name(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerOpenApplicationTool(gateway).execute({})

    async def test_close_application_requires_a_target(self, gateway: NodeGateway) -> None:
        with pytest.raises(InvalidInputError):
            await ComputerCloseApplicationTool(gateway).execute({})


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


class TestVerification:
    async def test_list_windows_verifies_on_a_window_list(self, gateway: NodeGateway) -> None:
        tool = ComputerListWindowsTool(gateway)
        assert await tool.verify({"windows": []}, {}) is VerificationOutcome.SUCCESS
        assert await tool.verify({}, {}) is VerificationOutcome.UNVERIFIED

    async def test_screenshot_verifies_on_a_path(self, gateway: NodeGateway) -> None:
        tool = ComputerScreenshotTool(gateway)
        assert await tool.verify({"path": "C:/shot.png"}, {}) is VerificationOutcome.SUCCESS
        assert await tool.verify({"path": ""}, {}) is VerificationOutcome.FAILED

    async def test_a_default_verdict_is_unverified(self, gateway: NodeGateway) -> None:
        assert await ComputerClickTool(gateway).verify({}, {}) is VerificationOutcome.UNVERIFIED
