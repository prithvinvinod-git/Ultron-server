"""Gateway tests (T141/T147, spec §64.10-§64.15).

The gateway is the server's half of the node protocol: registration, heartbeat,
command dispatch with result correlation, and typed unavailability. Every test
drives the real gateway over the T146 double, so what is asserted is the actual
correlation and event behaviour — no socket, no Windows, no sleeps beyond the
one timeout test that needs a clock.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agents.computer.gateway import NodeGateway
from app.agents.computer.protocol import (
    NodeHeartbeat,
    NodeRegistration,
    NodeResult,
    NodeStatus,
)
from app.config.settings import ComputerNodeSettings
from app.core.errors import NodeError, NodeNotFoundError, ToolError
from app.events.bus import EventBus
from tests.support.node_double import FakeNode

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _registration(node: FakeNode, credential: str | None = None) -> NodeRegistration:
    return NodeRegistration.model_validate(node.registration(credential=credential))


async def _register(gateway: NodeGateway, node: FakeNode, credential: str | None = None) -> Any:
    node.bind(gateway)
    return await gateway.register(_registration(node, credential), node.transport)


async def _drain(subscription: Any, *, wait: float = 0.05) -> list[Any]:
    """Collect the events a subscription has queued, without blocking."""
    events: list[Any] = []
    while True:
        try:
            envelope = await asyncio.wait_for(subscription.get(), timeout=wait)
        except TimeoutError:
            break
        if envelope is None:
            break
        events.append(envelope)
    return events


def _types(events: list[Any]) -> list[str]:
    return [str(event.event_type) for event in events]


@pytest.fixture
def settings() -> ComputerNodeSettings:
    return ComputerNodeSettings()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def gateway(settings: ComputerNodeSettings, bus: EventBus) -> NodeGateway:
    # Auth is exercised by its own tests; the rest drive an open gateway so the
    # only variable under test is the behaviour being asserted.
    return NodeGateway(settings=settings, bus=bus, require_auth=False)


@pytest.fixture
def secured(settings: ComputerNodeSettings, bus: EventBus) -> NodeGateway:
    return NodeGateway(settings=settings, bus=bus, require_auth=True)


# --------------------------------------------------------------------------- #
# Registration (§64.10, §64.13)
# --------------------------------------------------------------------------- #


class TestRegistration:
    async def test_a_valid_registration_is_accepted(
        self, gateway: NodeGateway, settings: ComputerNodeSettings
    ) -> None:
        node = FakeNode()
        hello = await _register(gateway, node)
        assert hello.accepted is True
        assert hello.node_id == node.node_id
        assert hello.heartbeat_interval == settings.heartbeat

    async def test_registration_without_credential_is_refused_when_auth_required(
        self, secured: NodeGateway
    ) -> None:
        node = FakeNode(credential="")
        hello = await _register(secured, node, credential="")
        assert hello.accepted is False
        assert hello.reason is not None
        assert secured.online_count == 0

    async def test_registration_with_a_wrong_credential_is_refused(
        self, secured: NodeGateway
    ) -> None:
        node = FakeNode()
        hello = await _register(secured, node, credential="not-a-real-token")
        assert hello.accepted is False
        assert secured.list_nodes() == []

    async def test_an_issued_credential_is_accepted(self, secured: NodeGateway) -> None:
        token = secured.issue_credential("windows-1")
        node = FakeNode(node_id="windows-1")
        hello = await _register(secured, node, credential=token)
        assert hello.accepted is True

    async def test_a_revoked_credential_is_refused(self, secured: NodeGateway) -> None:
        token = secured.issue_credential("windows-1")
        assert secured.revoke_credential("windows-1") is True
        node = FakeNode(node_id="windows-1")
        hello = await _register(secured, node, credential=token)
        assert hello.accepted is False

    async def test_auth_can_be_waived_for_local_development(self) -> None:
        gateway = NodeGateway(require_auth=False)
        node = FakeNode(credential="")
        hello = await _register(gateway, node, credential="")
        assert hello.accepted is True

    async def test_a_full_registry_refuses_a_new_node(self, settings: ComputerNodeSettings) -> None:
        gateway = NodeGateway(settings=settings, max_nodes=1, require_auth=False)
        assert (await _register(gateway, FakeNode(node_id="a"), credential="")).accepted is True
        second = await _register(gateway, FakeNode(node_id="b"), credential="")
        assert second.accepted is False
        assert second.reason is not None

    async def test_re_registering_replaces_the_transport(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        replacement = FakeNode(node_id=node.node_id, capabilities=("windows_ui",))
        replacement.bind(gateway)
        hello = await gateway.register(_registration(replacement), replacement.transport)
        assert hello.accepted is True
        assert node.transport.closed is True
        stored = gateway.get(node.node_id)
        assert stored.capabilities == frozenset({"windows_ui"})


class TestRegistrationEvents:
    async def test_online_events_are_published(self, gateway: NodeGateway, bus: EventBus) -> None:
        subscription = bus.subscribe()
        await _register(gateway, FakeNode())
        events = await _drain(subscription)
        assert "NODE_ONLINE" in _types(events)
        assert "WINDOWS_ONLINE" in _types(events)

    async def test_every_lifecycle_event_carries_the_node_id(
        self, gateway: NodeGateway, bus: EventBus
    ) -> None:
        subscription = bus.subscribe()
        await _register(gateway, FakeNode())
        events = await _drain(subscription)
        assert all(event.payload.get("node_id") == "windows-node-1" for event in events)

    async def test_capability_refresh_publishes_the_update(
        self, gateway: NodeGateway, bus: EventBus
    ) -> None:
        node = FakeNode()
        await _register(gateway, node)
        subscription = bus.subscribe()
        await gateway.heartbeat(
            NodeHeartbeat(node_id=node.node_id, capabilities=["windows_ui", "screenshots"])
        )
        events = await _drain(subscription)
        assert "NODE_CAPABILITIES_UPDATED" in _types(events)


# --------------------------------------------------------------------------- #
# Heartbeat and availability (§64.14)
# --------------------------------------------------------------------------- #


class TestHeartbeat:
    async def test_a_heartbeat_refreshes_last_seen(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        stored = gateway.get(node.node_id)
        stored.last_seen = datetime.now(UTC) - timedelta(seconds=5)
        await gateway.heartbeat(NodeHeartbeat(node_id=node.node_id))
        assert (datetime.now(UTC) - stored.last_seen).total_seconds() < 1

    async def test_a_heartbeat_updates_status(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        await gateway.heartbeat(NodeHeartbeat(node_id=node.node_id, status=NodeStatus.DEGRADED))
        assert gateway.get(node.node_id).status is NodeStatus.DEGRADED

    async def test_a_heartbeat_for_an_unknown_node_is_a_not_found(
        self, gateway: NodeGateway
    ) -> None:
        with pytest.raises(NodeNotFoundError):
            await gateway.heartbeat(NodeHeartbeat(node_id="ghost"))


class TestSweep:
    async def test_a_stale_node_is_marked_offline(self) -> None:
        gateway = NodeGateway(settings=ComputerNodeSettings(offline_after=1), require_auth=False)
        node = FakeNode()
        await _register(gateway, node, credential="")
        gateway.get(node.node_id).last_seen = datetime.now(UTC) - timedelta(seconds=30)
        offline = await gateway.sweep()
        assert offline == [node.node_id]
        assert gateway.get(node.node_id).status is NodeStatus.OFFLINE
        assert gateway.online_count == 0

    async def test_sweep_publishes_offline_events(self, bus: EventBus) -> None:
        gateway = NodeGateway(
            settings=ComputerNodeSettings(offline_after=1), bus=bus, require_auth=False
        )
        node = FakeNode()
        await _register(gateway, node, credential="")
        gateway.get(node.node_id).last_seen = datetime.now(UTC) - timedelta(seconds=30)
        subscription = bus.subscribe()
        await gateway.sweep()
        events = await _drain(subscription)
        assert "NODE_OFFLINE" in _types(events)
        assert "WINDOWS_OFFLINE" in _types(events)

    async def test_a_live_node_is_not_swept(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        assert await gateway.sweep() == []


class TestDisconnect:
    async def test_disconnect_marks_the_node_offline(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        assert await gateway.disconnect(node.node_id, reason="bye") is True
        assert gateway.get(node.node_id).status is NodeStatus.OFFLINE

    async def test_disconnect_of_an_unknown_node_is_false(self, gateway: NodeGateway) -> None:
        assert await gateway.disconnect("ghost") is False

    async def test_disconnect_fails_a_pending_command(self, gateway: NodeGateway) -> None:
        node = FakeNode(auto_respond=False)
        await _register(gateway, node)
        task = asyncio.create_task(gateway.dispatch(node.node_id, "computer.screenshot"))
        await asyncio.sleep(0)
        await gateway.disconnect(node.node_id, reason="unplugged")
        with pytest.raises(NodeError):
            await task


# --------------------------------------------------------------------------- #
# Dispatch and correlation (§64.11)
# --------------------------------------------------------------------------- #


class TestDispatch:
    async def test_a_command_is_delivered_and_its_result_returned(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(handlers={"computer.list_windows": lambda _a: ["Notepad", "Explorer"]})
        await _register(gateway, node)
        result = await gateway.dispatch(node.node_id, "computer.list_windows", {"node": "x"})
        assert result == ["Notepad", "Explorer"]
        sent = node.transport.sent[-1]
        assert sent["type"] == "command"
        assert sent["tool"] == "computer.list_windows"
        assert sent["arguments"] == {"node": "x"}

    async def test_the_command_carries_the_principal_and_correlation(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(handlers={"computer.click": lambda _a: "ok"})
        await _register(gateway, node)
        await gateway.dispatch(
            node.node_id,
            "computer.click",
            {"x": 1},
            principal="user:alice",
            correlation_id="corr-9",
            task_id="task-9",
        )
        sent = node.transport.sent[-1]
        assert sent["principal"] == "user:alice"
        assert sent["correlation_id"] == "corr-9"
        assert sent["task_id"] == "task-9"

    async def test_dispatch_to_an_unknown_node_is_unavailable(
        self, gateway: NodeGateway
    ) -> None:
        with pytest.raises(NodeError) as caught:
            await gateway.dispatch("ghost", "computer.screenshot")
        assert caught.value.code.value == "NODE_UNAVAILABLE"

    async def test_dispatch_to_an_offline_node_is_unavailable(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        await gateway.disconnect(node.node_id)
        with pytest.raises(NodeError):
            await gateway.dispatch(node.node_id, "computer.screenshot")

    async def test_dispatch_without_the_required_capability_is_unavailable(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(capabilities=("windows_ui",))
        await _register(gateway, node)
        with pytest.raises(NodeError) as caught:
            await gateway.dispatch(node.node_id, "computer.screenshot", capability="screenshots")
        assert caught.value.details is not None
        assert caught.value.details.get("capability") == "screenshots"

    async def test_a_node_reported_failure_is_a_tool_error(self, gateway: NodeGateway) -> None:
        def boom(_args: dict[str, Any]) -> Any:
            raise RuntimeError("application not found")

        node = FakeNode(handlers={"computer.open_application": boom})
        await _register(gateway, node)
        with pytest.raises(ToolError) as caught:
            await gateway.dispatch(node.node_id, "computer.open_application", {"name": "nope"})
        assert caught.value.tool == "computer.open_application"

    async def test_an_unimplemented_tool_is_a_tool_error(self, gateway: NodeGateway) -> None:
        node = FakeNode(handlers={})
        await _register(gateway, node)
        with pytest.raises(ToolError):
            await gateway.dispatch(node.node_id, "computer.screenshot")

    async def test_a_silent_node_times_out(self, gateway: NodeGateway) -> None:
        node = FakeNode(auto_respond=False)
        await _register(gateway, node)
        with pytest.raises(NodeError) as caught:
            await gateway.dispatch(node.node_id, "computer.screenshot", limit=0.05)
        assert "no result" in str(caught.value)

    async def test_a_dropped_connection_is_unavailable(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        node.transport.connection_error = ConnectionError
        with pytest.raises(NodeError) as caught:
            await gateway.dispatch(node.node_id, "computer.screenshot")
        assert "dropped" in str(caught.value)

    async def test_dispatch_after_shutdown_is_unavailable(
        self, bus: EventBus, settings: ComputerNodeSettings
    ) -> None:
        gateway = NodeGateway(settings=settings, bus=bus, require_auth=False)
        node = FakeNode()
        await _register(gateway, node, credential="")
        await gateway.stop()
        with pytest.raises(NodeError):
            await gateway.dispatch(node.node_id, "computer.screenshot")

    async def test_deliver_of_an_unknown_result_is_false(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        stray = NodeResult(command_id=uuid.uuid4(), node_id=node.node_id, status="ok")
        assert await gateway.deliver(stray) is False


# --------------------------------------------------------------------------- #
# Queries (§64.10)
# --------------------------------------------------------------------------- #


class TestQueries:
    async def test_list_nodes_is_sorted_by_id(self, gateway: NodeGateway) -> None:
        await _register(gateway, FakeNode(node_id="b"), credential="")
        await _register(gateway, FakeNode(node_id="a"), credential="")
        assert [n.node_id for n in gateway.list_nodes()] == ["a", "b"]

    async def test_get_of_an_unknown_node_is_a_not_found(self, gateway: NodeGateway) -> None:
        with pytest.raises(NodeNotFoundError):
            gateway.get("ghost")

    async def test_maybe_of_an_unknown_node_is_none(self, gateway: NodeGateway) -> None:
        assert gateway.maybe("ghost") is None

    async def test_find_by_capability_ranks_online_first(self, gateway: NodeGateway) -> None:
        online = FakeNode(node_id="b", capabilities=("screenshots",))
        offline = FakeNode(node_id="a", capabilities=("screenshots",))
        await _register(gateway, online)
        await _register(gateway, offline)
        await gateway.disconnect(offline.node_id)
        matches = gateway.find_by_capability("screenshots", online_only=False)
        assert [n.node_id for n in matches] == ["b", "a"]

    async def test_find_by_capability_online_only_excludes_offline(
        self, gateway: NodeGateway
    ) -> None:
        node = FakeNode(capabilities=("screenshots",))
        await _register(gateway, node)
        await gateway.disconnect(node.node_id)
        assert gateway.find_by_capability("screenshots") == []

    async def test_snapshot_counts_nodes(self, gateway: NodeGateway) -> None:
        await _register(gateway, FakeNode(node_id="a"))
        await _register(gateway, FakeNode(node_id="b"))
        snapshot = gateway.snapshot()
        assert snapshot["nodes"] == 2
        assert snapshot["online"] == 2
        assert snapshot["by_type"]["windows"] == 2


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


class TestLifecycle:
    async def test_start_and_stop_are_idempotent(self, gateway: NodeGateway) -> None:
        await gateway.start()
        await gateway.start()
        await gateway.stop()
        await gateway.stop()

    async def test_stop_closes_transports(self, gateway: NodeGateway) -> None:
        node = FakeNode()
        await _register(gateway, node)
        await gateway.stop()
        assert node.transport.closed is True
        assert gateway.list_nodes() == []
