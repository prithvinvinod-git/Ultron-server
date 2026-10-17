"""IoT agent tests (T164, spec §26).

The agent acts on the §26 device layer. Every test drives the real
``DeviceManager`` over the T161 double, so the deterministic path (dispatch) and
the event path are asserted against actual device behaviour — no socket, no
hardware. The model path runs a real ``ToolCallingLoop`` over a ``FakeProvider``
and an empty tool registry (the loop is the only thing under test there, not the
tool family a deployment would supply).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.iot import IoTAgent
from app.config import ModelRouterSettings
from app.config.settings import DeviceSettings
from app.core.errors import DeviceError, InvalidInputError
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel
from app.devices.manager import DeviceManager
from app.devices.protocol import DeviceEventMessage, DeviceRegister
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import FinishReason, ModelResponse, ToolDefinition
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.support.device_double import FakeDevice

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doubles and fixtures
# --------------------------------------------------------------------------- #


class RecordingBus(EventBus):
    """A real bus that also keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def manager() -> DeviceManager:
    return DeviceManager(settings=DeviceSettings(), bus=EventBus(), require_auth=False)


async def _registered(
    manager: DeviceManager,
    *,
    device_id: str = "esp32-1",
    handlers: dict[str, Any] | None = None,
) -> FakeDevice:
    device = FakeDevice(
        device_id=device_id,
        handlers=handlers or {"display.show": lambda args: {"shown": args.get("text")}},
    )
    device.bind(manager)
    await manager.register(
        DeviceRegister.model_validate(device.registration(credential=None)), device.transport
    )
    return device


def _answer(text: str = "done") -> ModelResponse:
    return ModelResponse(
        model="llama3", content=text, finish_reason=FinishReason.STOP, provider="fake"
    )


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _executor(audit_sink: Any) -> ToolExecutor:
    return ToolExecutor(
        registry=ToolRegistry(),
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink), default_level=PermissionLevel.DESTRUCTIVE
        ),
        events=EventBus(),
    )


# --------------------------------------------------------------------------- #
# Declaration and construction
# --------------------------------------------------------------------------- #


class TestDeclaration:
    def test_declares_its_type_and_description(self) -> None:
        assert IoTAgent.agent_type == "iot.agent"
        assert IoTAgent.description.strip()

    def test_tools_without_a_loop_are_refused(self, manager: DeviceManager) -> None:
        with pytest.raises(ValueError):
            IoTAgent(
                manager=manager,
                tools=[ToolDefinition(name="device.list", description="list devices")],
            )


# --------------------------------------------------------------------------- #
# Deterministic path: dispatch through the manager
# --------------------------------------------------------------------------- #


class TestDispatch:
    async def test_a_device_action_is_dispatched_and_reported(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = await _registered(manager)
        agent = IoTAgent(manager=manager, bus=bus)

        report = await agent.run(
            {"device_id": device.device_id, "action": "display.show", "arguments": {"text": "hi"}}
        )

        assert report["result"] == {"shown": "hi"}
        assert report["device_id"] == device.device_id
        assert report["device"]["device_id"] == device.device_id
        assert report["action"] == "display.show"
        assert report["completed"] is True

    async def test_an_action_on_an_unknown_device_is_a_typed_error(
        self, manager: DeviceManager
    ) -> None:
        agent = IoTAgent(manager=manager)
        with pytest.raises(DeviceError):
            await agent.run({"device_id": "ghost", "action": "display.show"})

    async def test_an_action_without_a_device_is_refused(self, manager: DeviceManager) -> None:
        agent = IoTAgent(manager=manager)
        with pytest.raises(InvalidInputError):
            await agent.run({"action": "display.show"})

    async def test_progress_is_published(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = await _registered(manager)
        agent = IoTAgent(manager=manager, bus=bus)

        await agent.run({"device_id": device.device_id, "action": "display.show"})

        phases = [
            payload.get("phase")
            for event_type, payload in bus.published
            if event_type == EventType.TASK_PROGRESS.value
        ]
        assert "plan" in phases


# --------------------------------------------------------------------------- #
# Event path: the handler for events T161 leaves to the agent
# --------------------------------------------------------------------------- #


class TestHandleEvent:
    async def test_a_sensor_event_produces_a_report(self, manager: DeviceManager) -> None:
        device = await _registered(manager)
        agent = IoTAgent(manager=manager)
        event = DeviceEventMessage.model_validate(
            {
                "type": "event",
                "device_id": device.device_id,
                "event_type": "sensor_event",
                "payload": {"temperature": 21},
            }
        )

        report = await agent.handle_event(event)

        assert report["event_type"] == "sensor_event"
        assert report["device_id"] == device.device_id
        assert report["action"] is None
        assert report["completed"] is True
        assert report["devices"]["online"] == 1

    async def test_an_event_dict_in_context_is_accepted(self, manager: DeviceManager) -> None:
        device = await _registered(manager)
        agent = IoTAgent(manager=manager)

        report = await agent.run(
            {
                "event": {
                    "type": "event",
                    "device_id": device.device_id,
                    "event_type": "button_press",
                }
            }
        )

        assert report["event_type"] == "button_press"

    async def test_an_empty_context_is_refused(self, manager: DeviceManager) -> None:
        agent = IoTAgent(manager=manager)
        with pytest.raises(InvalidInputError):
            await agent.run({})


# --------------------------------------------------------------------------- #
# Model path: the optional §16 loop
# --------------------------------------------------------------------------- #


class TestModelPath:
    async def test_a_prompt_drives_the_loop(
        self, manager: DeviceManager, audit_sink: Any
    ) -> None:
        provider = FakeProvider(name="ollama", local=True, responses=[_answer("checked")])
        agent = IoTAgent(
            manager=manager,
            router=_router(provider),
            executor=_executor(audit_sink),
            tools=[ToolDefinition(name="device.list", description="list devices")],
        )

        report = await agent.run({"prompt": "check the devices"})

        assert report["answer"] == "checked"
        assert report["completed"] is True
        assert report["stop_reason"] == "completed"

    async def test_an_event_can_drive_the_loop(
        self, manager: DeviceManager, audit_sink: Any
    ) -> None:
        device = await _registered(manager)
        provider = FakeProvider(name="ollama", local=True, responses=[_answer("noted")])
        agent = IoTAgent(
            manager=manager,
            router=_router(provider),
            executor=_executor(audit_sink),
            tools=[ToolDefinition(name="device.list", description="list devices")],
        )

        report = await agent.run(
            {"event": {"type": "event", "device_id": device.device_id, "event_type": "wake_word"}}
        )

        assert report["answer"] == "noted"
        assert report["event_type"] == "wake_word"
