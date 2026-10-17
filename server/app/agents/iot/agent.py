"""The IoT agent (T164): the §26 device layer's worker.

Spec §26 makes the ESP32/IoT layer a *device* concern and §8 lists an "IoT
Agent" among ULTRON's workers. The device protocol (T160) and the gateway (T161)
are the wire and the registry; the transports (T162/T163) are how a device
reaches them. This agent is the **worker** that acts on that layer: it turns a
device event — or a command about devices — into a governed action and a report.

It is deliberately shaped like :class:`~app.agents.computer.ComputerAgent`: a
declaration plus one ``run``, handed its collaborators rather than naming them.
The model-driven cycle is :class:`~app.agents.loop.ToolCallingLoop` (T068),
optionally pre-advertised with ``device.*`` tool definitions so a model can plan
device work through §16's governed path (schema → permission → policy →
execution → verification → event). The model path is **optional**: with no loop
configured the agent still acts deterministically through the
:class:`~app.devices.manager.DeviceManager`'s ``dispatch``, which is what a
deployment without a device tool family needs. Either way the agent never holds a
socket and never imports a board — the manager owns the connection (§26: no board
hard-coded in the Core).

Two entry points, one body:

- ``run(context)`` — the agent contract. ``context`` may carry an ``event`` (a
  ``DeviceEventMessage`` or its dict form) to react to, and/or a ``device_id`` +
  ``action`` (+ ``arguments``) to dispatch, and/or a ``prompt`` for the model.
- ``handle_event(event)`` — the direct hook for the events the manager *declines*
  to canonicalize. ``DeviceManager.report`` returns ``False`` for
  ``button_press``, ``sensor_event`` and ``audio_stream`` and hands them "to the
  IoT agent" (T161); this is where a transport or a deployment routes them.

Failure discipline mirrors the other workers: a typed :class:`UltronError` is
re-raised untouched, and anything unexpected is wrapped in ``AgentError``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.base import Agent
from app.agents.loop import ToolCallingLoop, ToolLoopResult
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import MessageRole
from app.devices.manager import DeviceManager
from app.devices.protocol import DeviceCapabilities, DeviceEventMessage
from app.events.bus import EventBus
from app.events.types import EventType
from app.models.base import ModelMessage, ToolDefinition
from app.models.router import ModelRouter
from app.tools.executor import ToolExecutor

__all__ = ["IoTAgent"]

#: The default system prompt: what the worker is and how it should act.
_DEFAULT_SYSTEM_PROMPT = (
    "You are ULTRON's IoT agent. Act on the connected devices using only the "
    "provided device.* tools and the reported device state. Devices advertise "
    "their capabilities; never assume a device can do something it has not "
    "advertised. Prefer a single, targeted command over a burst, and after each "
    "command confirm from the result that the device did what was asked. When the "
    "goal is met — or cannot be met with the available devices — stop calling "
    "tools and answer with a concise report of what you did and observed."
)


class IoTAgent(Agent):
    """Act on the §26 device layer: react to device events, dispatch device commands."""

    agent_type = "iot.agent"
    description = "React to device events and dispatch commands through the device gateway."

    def __init__(
        self,
        *,
        manager: DeviceManager,
        router: ModelRouter | None = None,
        executor: ToolExecutor | None = None,
        tools: Sequence[ToolDefinition] = (),
        bus: EventBus | None = None,
        capability: str = "default",
        max_iterations: int = 8,
        max_tool_calls: int = 24,
        system_prompt: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to the device gateway and, optionally, the §16 pipeline.

        ``tools`` are the ``device.*`` tool *definitions* advertised to the model.
        They are optional: a deployment with a device tool family passes a
        ``router`` and an ``executor`` to drive the model, while one without still
        uses the agent for deterministic ``dispatch``. Supplying tools without
        both is a misconfiguration and is refused at construction.
        """
        super().__init__(**agent_kwargs)
        definitions = tuple(tools)
        self._manager = manager
        self._bus = bus
        self._loop: ToolCallingLoop | None = None
        if definitions:
            if router is None or executor is None:
                raise ValueError("device tools need both a router and a tool executor")
            self._loop = ToolCallingLoop(
                router=router,
                executor=executor,
                tools=definitions,
                capability=capability,
                max_iterations=max_iterations,
                max_tool_calls=max_tool_calls,
                system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            )

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """React to a device event and/or dispatch a device command over ``context``."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "iot agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    async def handle_event(self, event: DeviceEventMessage) -> Mapping[str, Any]:
        """React to one device event — the hook for the events T161 leaves here.

        ``DeviceManager.report`` publishes the canonical counterpart of
        ``wake_word`` / ``device_online`` / ``device_offline`` and returns
        ``True``; for ``button_press`` / ``sensor_event`` / ``audio_stream`` it
        returns ``False`` and the event is the agent's to handle. This is that
        entry point, and it is the same body ``run`` executes.
        """
        return await self.run({"event": event.model_dump(mode="json")})

    # -- the run ------------------------------------------------------------ #
    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        event = _event_from(context)
        device_id = (event.device_id if event is not None else None) or _optional_text(
            context, "device_id"
        )
        action = _optional_text(context, "action")
        prompt = _optional_text(context, "prompt")
        task_id = _optional_text(context, "task_id") or _own_task_id(self)
        principal = _optional_text(context, "principal") or str(self.agent_id)

        if event is None and action is None and prompt is None:
            raise InvalidInputError(
                "an iot task needs an event, a device action, or a prompt",
                details={"keys": sorted(str(key) for key in context)},
            )

        resolved = self._manager.maybe(device_id) if device_id else None
        view = resolved.to_capabilities() if resolved is not None else None

        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "plan",
                "device_id": device_id,
                "event_type": event.event_type if event is not None else None,
                "action": action,
                "online": self._manager.online_count,
            },
        )

        dispatched: Any = None
        if action is not None:
            if device_id is None:
                raise InvalidInputError("a device action needs a device_id")
            dispatched = await self._manager.dispatch(
                device_id,
                action,
                _mapping(context.get("arguments")),
                principal=principal,
            )

        loop_result: ToolLoopResult | None = None
        if self._loop is not None and (prompt is not None or event is not None):
            loop_result = await self._loop.run(
                (
                    ModelMessage(
                        role=MessageRole.USER,
                        content=_context_message(prompt, event, view),
                    ),
                ),
                task_id=task_id,
                principal=principal,
            )

        return self._report(task_id, device_id, event, view, action, dispatched, loop_result)

    # -- the report --------------------------------------------------------- #
    def _report(
        self,
        task_id: str,
        device_id: str | None,
        event: DeviceEventMessage | None,
        view: DeviceCapabilities | None,
        action: str | None,
        dispatched: Any,
        loop_result: ToolLoopResult | None,
    ) -> dict[str, Any]:
        return {
            "agent": self.agent_type,
            "task_id": task_id,
            "device_id": device_id,
            "event_type": event.event_type if event is not None else None,
            "device": view.model_dump(mode="json") if view is not None else None,
            "action": action,
            "result": dispatched,
            "answer": loop_result.content if loop_result is not None else None,
            "completed": loop_result.completed if loop_result is not None else True,
            "stop_reason": loop_result.stop_reason.value if loop_result is not None else "done",
            "iterations": loop_result.iterations if loop_result is not None else 0,
            "tool_calls": loop_result.tool_calls if loop_result is not None else 0,
            "devices": self._manager.snapshot(),
        }

    async def _progress(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """Publish one ephemeral ``TASK_PROGRESS`` (no bus: nowhere to publish)."""
        if self._bus is None:
            return
        await self._bus.publish(
            EventType.TASK_PROGRESS,
            payload,
            task_id=task_id,
            agent_id=str(self.agent_id),
            persist=False,
        )


# --------------------------------------------------------------------------- #
# Context helpers (stateless, mirroring the other workers' split)
# --------------------------------------------------------------------------- #
def _event_from(context: Mapping[str, Any]) -> DeviceEventMessage | None:
    """Coerce ``context['event']`` into a ``DeviceEventMessage``, or ``None``."""
    raw = context.get("event")
    if raw is None:
        return None
    if isinstance(raw, DeviceEventMessage):
        return raw
    if isinstance(raw, Mapping):
        return DeviceEventMessage.model_validate(dict(raw))
    raise InvalidInputError("event must be a device event message")


def _optional_text(context: Mapping[str, Any], key: str) -> str | None:
    value = context.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    raise InvalidInputError("a device command's arguments must be an object")


def _own_task_id(agent: Agent) -> str:
    """The agent's task id, then its agent id — the loop's own fallback (§6)."""
    return str(agent.task_id) if agent.task_id is not None else str(agent.agent_id)


def _context_message(
    prompt: str | None, event: DeviceEventMessage | None, view: DeviceCapabilities | None
) -> str:
    """Build the model's user message from a prompt and/or a device event."""
    lines: list[str] = []
    if prompt is not None:
        lines.append(prompt)
    if event is not None:
        detail = f"Device event '{event.event_type}' from device '{event.device_id}'."
        if event.payload:
            detail = f"{detail} Payload: {dict(event.payload)}."
        lines.append(detail)
    if view is not None:
        capabilities = ", ".join(view.capabilities) or "none advertised"
        lines.append(
            f"Target device '{view.device_id}' ({view.device_type or 'unknown type'}) "
            f"advertises: {capabilities}."
        )
    return "\n".join(lines) if lines else "(no context)"
