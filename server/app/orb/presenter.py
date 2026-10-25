"""Bus → orb state presenter (spec §27 / §59.13).

`ORB_SHOW`, `ORB_HIDE`, `ORB_STATE_CHANGED` and `TRANSCRIPT_*` were declared in
the event catalog but had no producer, so every orb (the console header, the
desktop shell, the ESP32) sat on `idle` forever. This subscriber is that
producer.

**It is a projection, not a controller.** It listens to the events the system
already publishes — voice, STT/TTS, agents, tools, tasks, permissions — and
republishes *one* current state. Nothing here starts a voice session, runs an
agent, or knows a renderer exists. A new client can therefore resync with
``GET /orb/state`` instead of replaying history, and the same state reaches the
SSE stream, the PWA and the Electron orb.

**Transitions come from events, never timers.** There is no demo animation loop
and no "pretend listening" clock: a state changes because one of the mapped
events arrived. The single deliberate exception is ``WAKE_DETECTED``, which
surfaces ``listening`` *before* the client's microphone reports in, because the
wake signal is the user's intent to speak and the client starts capture in the
same breath. Its payload carries ``pending: true`` until ``VOICE_LISTENING``
confirms real capture — a renderer that wants to be strict can show a wake ring
instead of a live mic.

**Stale and duplicate events are safe.** Every event carries a monotonic bus id;
the presenter ignores anything at or below the last id it handled, so a replayed
frame or a double delivery cannot move the orb backwards.

The state is process-local. A server restart resets ids to 0; clients reconnect
with a stale ``Last-Event-ID`` and the bus returns no replay, then the client's
``GET /orb/state`` resync is authoritative.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.events.bus import EventBus, EventEnvelope, Subscription
from app.events.types import EventType
from app.observability import get_logger
from app.orb.states import ORB_STATE_SCHEMA_VERSION, OrbState

__all__ = ["OrbPresenter"]

_LOGGER = get_logger(__name__)

#: Voice phases in display precedence. A session can only be in one phase, but
#: several sessions may overlap (a device and the desktop); the most "active"
#: one wins so the orb never looks idle while ULTRON is speaking.
_VOICE_PRECEDENCE: tuple[tuple[str, OrbState], ...] = (
    ("speaking", OrbState.SPEAKING),
    ("listening", OrbState.LISTENING),
    ("thinking", OrbState.THINKING),
)

#: Failures that colour the orb red. Every one of them is recoverable: the next
#: listening turn or agent/task start clears the flag.
_FAILURE_EVENTS = frozenset(
    {
        EventType.TASK_FAILED,
        EventType.AGENT_FAILED,
        EventType.TOOL_FAILED,
        EventType.MODEL_FAILED,
        EventType.SYSTEM_ERROR,
    }
)

_AGENT_ACTIVE = frozenset({EventType.AGENT_CREATED, EventType.AGENT_STARTED})
_AGENT_DONE = frozenset(
    {EventType.AGENT_COMPLETED, EventType.AGENT_FAILED, EventType.AGENT_STOPPED}
)
_TASK_ACTIVE = frozenset({EventType.TASK_CREATED, EventType.TASK_STARTED, EventType.TASK_PROGRESS})
_TASK_DONE = frozenset({EventType.TASK_COMPLETED, EventType.TASK_FAILED})

#: Events that clear a sticky error/notification because real work resumed.
_CLEARS_ATTENTION = frozenset(
    {
        EventType.WAKE_DETECTED,
        EventType.VOICE_LISTENING,
        EventType.AGENT_STARTED,
        EventType.TASK_STARTED,
    }
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _identifier(envelope: EventEnvelope, key: str) -> str | None:
    """Best-effort stable id for an activity, from envelope or payload."""
    direct = getattr(envelope, key, None)
    if isinstance(direct, str) and direct:
        return direct
    value = envelope.payload.get(key)
    return value if isinstance(value, str) and value else None


class OrbPresenter:
    """Publish the current orb state derived from real Core events."""

    name = "orb_presenter"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._subscription: Subscription | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_id = 0
        self._state = OrbState.IDLE
        self._visible = True
        self._updated_at = datetime.now(UTC)
        self._source_event: str | None = None
        self._source_id: int | None = None
        self._voice: dict[str, str] = {}
        self._speaking = False
        self._responding = False
        self._agents: set[str] = set()
        self._tasks: set[str] = set()
        self._tools = 0
        self._wake: dict[str, Any] | None = None
        self._error: dict[str, Any] | None = None
        self._notification: dict[str, Any] | None = None
        self._transcripts: dict[str, str] = {}
        self._published = 0
        self._ignored = 0

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> OrbState:
        return self._state

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def published(self) -> int:
        return self._published

    def snapshot(self) -> dict[str, Any]:
        """The resync document for ``GET /orb/state`` and late clients."""
        return {
            "schema_version": ORB_STATE_SCHEMA_VERSION,
            "state": self._state.value,
            "visible": self._visible,
            "updated_at": self._updated_at.isoformat(),
            "source_event": self._source_event,
            "source_event_id": self._source_id,
            "active": {
                "voice_sessions": len(self._voice),
                "speaking": self._speaking,
                "responding": self._responding,
                "agents": len(self._agents),
                "tasks": len(self._tasks),
                "tools": self._tools,
            },
            "wake": dict(self._wake) if self._wake else None,
            "error": dict(self._error) if self._error else None,
            "notification": dict(self._notification) if self._notification else None,
            "transcripts": dict(self._transcripts),
        }

    def counters(self) -> dict[str, int]:
        return {
            "published": self._published,
            "ignored": self._ignored,
            "dropped": self._subscription.dropped if self._subscription is not None else 0,
        }

    async def start(self) -> None:
        """Subscribe to the bus and begin projecting."""
        if self._subscription is not None:
            return
        self._subscription = self._bus.subscribe()
        self._task = asyncio.create_task(self._run(), name=f"{self.name}-worker")
        _LOGGER.info(
            "orb presenter started",
            extra={"event": "orb.presenter_started", "state": self._state.value},
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        self._subscription.close()
        if self._task is not None:
            await self._task
        self._task = None
        self._subscription = None
        _LOGGER.info(
            "orb presenter stopped",
            extra={"event": "orb.presenter_stopped", **self.counters()},
        )

    async def show(self, *, reason: str = "manual") -> dict[str, Any]:
        """Explicitly show the orb (a client action, also the API's POST)."""
        await self._bus.publish(
            EventType.ORB_SHOW,
            {"schema_version": ORB_STATE_SCHEMA_VERSION, "reason": reason},
        )
        return self.snapshot()

    async def hide(self, *, reason: str = "manual") -> dict[str, Any]:
        """Explicitly hide the orb (a client action, also the API's POST)."""
        await self._bus.publish(
            EventType.ORB_HIDE,
            {"schema_version": ORB_STATE_SCHEMA_VERSION, "reason": reason},
        )
        return self.snapshot()

    async def resync(self) -> dict[str, Any]:
        """Return and re-broadcast the current state.

        Called when a client reconnects: the SSE stream replays at most
        ``EVENT_REPLAY_SIZE`` events, so a client that was away longer than that
        window has no reliable way to know the state. Re-publishing makes the
        live stream self-healing as well.
        """
        await self._emit(self._state, reason="resync", force=True)
        return self.snapshot()

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            _LOGGER.error(
                "orb presenter worker started without a subscription",
                extra={"event": "orb.presenter_misstarted"},
            )
            return
        while True:
            envelope = await subscription.get()
            if envelope is None:
                return
            if envelope.id <= self._last_id:
                # Replay/double delivery: never move the orb backwards.
                self._ignored += 1
                continue
            self._last_id = envelope.id
            try:
                await self._handle(envelope)
            except Exception:
                # A projection bug must never take down the bus consumer.
                _LOGGER.exception(
                    "orb presenter failed to handle an event",
                    extra={
                        "event": "orb.presenter_error",
                        "event_type": envelope.event_type,
                    },
                )

    async def _handle(self, envelope: EventEnvelope) -> None:
        event_type = envelope.event_type
        payload = envelope.payload

        # Own output: re-reading it would either loop or freeze stale state.
        if event_type == EventType.ORB_STATE_CHANGED:
            return

        if event_type == EventType.ORB_SHOW:
            self._visible = True
            await self._emit(self._derive(), reason=_text(payload.get("reason")) or "show")
            return
        if event_type == EventType.ORB_HIDE:
            self._visible = False
            await self._emit(OrbState.HIDDEN, reason=_text(payload.get("reason")) or "hide")
            return

        if event_type in _CLEARS_ATTENTION:
            self._error = None
            self._notification = None

        if event_type == EventType.WAKE_DETECTED:
            self._visible = True
            self._wake = {
                "wake_word": _text(payload.get("wake_word")),
                "source": _text(payload.get("source")),
                "command": _text(payload.get("command")),
            }
            # The wake event is the display's cue to appear even when it was
            # hidden; clients react to ORB_SHOW, not to a state they might miss.
            await self._bus.publish(
                EventType.ORB_SHOW,
                {
                    "schema_version": ORB_STATE_SCHEMA_VERSION,
                    "reason": "wake_detected",
                    "source": self._wake["source"],
                },
                device_id=envelope.device_id,
            )

        elif event_type in (EventType.STT_PARTIAL, EventType.STT_FINAL):
            await self._transcript(envelope, partial=event_type == EventType.STT_PARTIAL)

        elif event_type == EventType.VOICE_LISTENING:
            session = _text(payload.get("session_id")) or "voice"
            self._voice[session] = "listening"
            self._wake = None
            self._speaking = False
        elif event_type == EventType.VOICE_THINKING:
            session = _text(payload.get("session_id")) or "voice"
            self._voice[session] = "thinking"
        elif event_type == EventType.VOICE_SPEAKING:
            session = _text(payload.get("session_id")) or "voice"
            self._voice[session] = "speaking"
            self._speaking = True
        elif event_type == EventType.VOICE_ENDED:
            session = _text(payload.get("session_id"))
            if session:
                self._voice.pop(session, None)
                self._transcripts.pop(session, None)
            else:
                self._voice.clear()
            self._wake = None
            self._speaking = False
            self._responding = False
        elif event_type == EventType.TTS_STARTED:
            self._speaking = True
            self._responding = False
        elif event_type == EventType.TTS_COMPLETED:
            self._speaking = False

        elif event_type in _AGENT_ACTIVE:
            agent = _identifier(envelope, "agent_id")
            if agent:
                self._agents.add(agent)
        elif event_type in _AGENT_DONE:
            agent = _identifier(envelope, "agent_id")
            if agent:
                self._agents.discard(agent)
        elif event_type == EventType.TOOL_STARTED:
            self._tools += 1
        elif event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED):
            self._tools = max(0, self._tools - 1)
        elif event_type in _TASK_ACTIVE:
            task = _identifier(envelope, "task_id")
            if task:
                self._tasks.add(task)
        elif event_type in _TASK_DONE:
            task = _identifier(envelope, "task_id")
            if task:
                self._tasks.discard(task)
            elif event_type == EventType.TASK_FAILED:
                self._tasks.clear()
        elif event_type == EventType.TASK_COMPLETED:
            self._tasks.clear()

        elif event_type == EventType.MODEL_RESPONSE:
            # Only meaningful inside a voice turn: text-only chat leaves the orb
            # alone rather than pulsing "responding" for a turn nobody hears.
            if self._voice:
                self._responding = True

        elif event_type == EventType.PERMISSION_REQUESTED:
            self._notification = {
                "kind": "permission_requested",
                "message": _text(payload.get("tool"))
                or _text(payload.get("message"))
                or "ULTRON needs permission",
                "event_id": envelope.id,
            }
        elif event_type in (EventType.PERMISSION_GRANTED, EventType.PERMISSION_DENIED):
            self._notification = None

        if event_type in _FAILURE_EVENTS:
            self._error = {
                "code": _text(payload.get("error_code")) or event_type,
                "message": _text(payload.get("error"))
                or _text(payload.get("message"))
                or event_type.replace("_", " ").lower(),
                "event_id": envelope.id,
            }

        # Nothing below published a state for this event; derive and emit.
        await self._emit(
            self._derive(),
            reason=event_type,
            source_event=event_type,
            source_id=envelope.id,
        )

    def _derive(self) -> OrbState:
        if not self._visible:
            return OrbState.HIDDEN
        for phase, state in _VOICE_PRECEDENCE:
            if phase in self._voice.values():
                return state
        if self._wake is not None:
            # Wake signal, capture not confirmed yet (see module docstring).
            return OrbState.LISTENING
        if self._agents or self._tasks or self._tools:
            return OrbState.EXECUTING
        if self._speaking:
            return OrbState.SPEAKING
        if self._responding:
            return OrbState.RESPONDING
        if self._notification is not None:
            return OrbState.NOTIFICATION
        if self._error is not None:
            return OrbState.ERROR
        return OrbState.IDLE

    async def _transcript(self, envelope: EventEnvelope, *, partial: bool) -> None:
        text = _text(envelope.payload.get("text"))
        if not text:
            return
        session = _text(envelope.payload.get("session_id")) or "voice"
        self._transcripts[session] = text
        await self._bus.publish(
            EventType.TRANSCRIPT_PARTIAL if partial else EventType.TRANSCRIPT_FINAL,
            {
                "schema_version": ORB_STATE_SCHEMA_VERSION,
                "text": text,
                "partial": partial,
                "session_id": None if session == "voice" else session,
                "source_event_id": envelope.id,
            },
            task_id=envelope.task_id,
            agent_id=envelope.agent_id,
            device_id=envelope.device_id,
        )

    async def _emit(
        self,
        state: OrbState,
        *,
        reason: str,
        source_event: str | None = None,
        source_id: int | None = None,
        force: bool = False,
    ) -> None:
        if state is OrbState.DISCONNECTED:  # client-derived only
            return
        if not force and state == self._state:
            return
        previous = self._state
        self._state = state
        self._updated_at = datetime.now(UTC)
        self._source_event = source_event
        self._source_id = source_id
        payload: dict[str, Any] = {
            "schema_version": ORB_STATE_SCHEMA_VERSION,
            "state": state.value,
            "previous_state": previous.value,
            "reason": reason,
            "visible": self._visible,
            "active": {
                "voice_sessions": len(self._voice),
                "speaking": self._speaking,
                "responding": self._responding,
                "agents": len(self._agents),
                "tasks": len(self._tasks),
                "tools": self._tools,
            },
        }
        if self._wake:
            payload["wake"] = dict(self._wake)
            payload["pending"] = True
        if self._error:
            payload["error"] = dict(self._error)
        if self._notification:
            payload["notification"] = dict(self._notification)
        await self._bus.publish(EventType.ORB_STATE_CHANGED, payload)
        self._published += 1
