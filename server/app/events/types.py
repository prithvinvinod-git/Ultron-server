"""Canonical event-type catalog and topic derivation (T030, spec §19).

One bus (§19), one spelling per event (§64.15), one module that owns both.
Producers publish a member of :class:`EventType`; every consumer — the SSE
stream (T023), the device WebSocket (T162), the node gateway (T141), the
built-in subscribers (T032) — reads the same names from here rather than
restating them.

**Provenance.** The task line scopes T030 at "33 event types from §19 +
orb/agent-window events": §19 lists 30 names and §27 adds the three ``ORB_*``
ones on top of them. The catalog below is the full union, because each later
section explicitly declares itself an *addition to this set* under the
no-rename rule: §27's two transcript names, §64.9's node/orb/agent states,
§64.15's ``ESP32_`` forms, §65.15's call lifecycle and §66.8's planner,
permission, voice-pipeline and model-fallback names. Folding them in now is
what makes this the single canonical set instead of a set every extension
task has to reopen.

**What is deliberately not here:**

- ``stream.lagged`` and ``stream.connected`` — they describe the transport,
  not the system, so they live in :mod:`app.events.bus` and a domain
  subscriber never mistakes one for a domain event.
- ``DEVICE_OFFLINE`` — §19 spells the same fact ``DEVICE_DISCONNECTED`` and
  §58's checklist spells it the other way; the arc records that mismatch as
  the *one* known spelling conflict and defers the resolution to T335. Adding
  both spellings here would make the conflict permanent instead of
  temporary, so only §19's spelling is canonical.

Topics are derived from the type name rather than passed per publish (§19),
so a publisher cannot file an event under a topic that contradicts its type.
Unknown names fall through to :data:`SYSTEM_TOPIC`, the safe direction: a
brand-new event reaches subscribers instead of silently reaching nobody.
"""

from __future__ import annotations

from enum import StrEnum

#: Wildcard topic: subscribe to everything.
ALL_TOPICS = "*"

#: Topic for events that belong to no other topic (resource alarms, the
#: core-flow names such as ``USER_MESSAGE``, and anything unrecognised).
SYSTEM_TOPIC = "system"


class EventType(StrEnum):
    """Every canonical event type, grouped by the section that named it."""

    # -- spec §19, the original 30 ----------------------------------------
    USER_MESSAGE = "USER_MESSAGE"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    AGENT_CREATED = "AGENT_CREATED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_PAUSED = "AGENT_PAUSED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    MODEL_REQUEST = "MODEL_REQUEST"
    MODEL_RESPONSE = "MODEL_RESPONSE"
    BUILD_STARTED = "BUILD_STARTED"
    BUILD_FAILED = "BUILD_FAILED"
    TEST_FAILED = "TEST_FAILED"
    DEVICE_CONNECTED = "DEVICE_CONNECTED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    WAKE_DETECTED = "WAKE_DETECTED"
    STT_PARTIAL = "STT_PARTIAL"
    STT_FINAL = "STT_FINAL"
    TTS_STARTED = "TTS_STARTED"
    TTS_COMPLETED = "TTS_COMPLETED"
    CPU_HIGH = "CPU_HIGH"
    RAM_HIGH = "RAM_HIGH"
    DISK_LOW = "DISK_LOW"
    GPU_HIGH = "GPU_HIGH"
    SCHEDULE_TRIGGERED = "SCHEDULE_TRIGGERED"

    # -- spec §27, desktop orb (5) ----------------------------------------
    ORB_SHOW = "ORB_SHOW"
    ORB_HIDE = "ORB_HIDE"
    ORB_STATE_CHANGED = "ORB_STATE_CHANGED"
    TRANSCRIPT_PARTIAL = "TRANSCRIPT_PARTIAL"
    TRANSCRIPT_FINAL = "TRANSCRIPT_FINAL"

    # -- spec §64.9, ESP32 display contract (11) --------------------------
    TASK_PROGRESS = "TASK_PROGRESS"
    NODE_ONLINE = "NODE_ONLINE"
    NODE_OFFLINE = "NODE_OFFLINE"
    SERVER_ONLINE = "SERVER_ONLINE"
    SERVER_OFFLINE = "SERVER_OFFLINE"
    WINDOWS_ONLINE = "WINDOWS_ONLINE"
    WINDOWS_OFFLINE = "WINDOWS_OFFLINE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    AGENT_STOPPED = "AGENT_STOPPED"

    # -- spec §64.15, cross-node protocol (2) -----------------------------
    ESP32_ONLINE = "ESP32_ONLINE"
    ESP32_OFFLINE = "ESP32_OFFLINE"

    # -- spec §65.15, telephony call lifecycle (8) ------------------------
    VOICE_CALL_CREATED = "VOICE_CALL_CREATED"
    VOICE_CALL_RINGING = "VOICE_CALL_RINGING"
    VOICE_CALL_CONNECTED = "VOICE_CALL_CONNECTED"
    VOICE_CALL_LISTENING = "VOICE_CALL_LISTENING"
    VOICE_CALL_THINKING = "VOICE_CALL_THINKING"
    VOICE_CALL_SPEAKING = "VOICE_CALL_SPEAKING"
    VOICE_CALL_ENDED = "VOICE_CALL_ENDED"
    VOICE_CALL_FAILED = "VOICE_CALL_FAILED"

    # -- spec §66.8, catalog extensions (15) ------------------------------
    CONTEXT_UPDATED = "CONTEXT_UPDATED"
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_UPDATED = "PLAN_UPDATED"
    NODE_CAPABILITIES_UPDATED = "NODE_CAPABILITIES_UPDATED"
    VOICE_STARTED = "VOICE_STARTED"
    VOICE_LISTENING = "VOICE_LISTENING"
    VOICE_THINKING = "VOICE_THINKING"
    VOICE_SPEAKING = "VOICE_SPEAKING"
    VOICE_ENDED = "VOICE_ENDED"
    MODEL_SELECTED = "MODEL_SELECTED"
    MODEL_FAILED = "MODEL_FAILED"
    PERMISSION_REQUESTED = "PERMISSION_REQUESTED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


#: The whole catalog as plain strings, for membership checks against values
#: that never went through the enum (env config, wire payloads, log fields).
CANONICAL_EVENT_TYPES: frozenset[str] = frozenset(EventType)

#: Orb display states named without a prefix (§64.9 "orb / agent states").
#: Matched by exact name before the prefix table, so ``LISTENING`` cannot be
#: swallowed by a future prefix and a voice event cannot claim an orb state.
_EXACT_ORB_STATES: frozenset[str] = frozenset(
    {EventType.LISTENING, EventType.THINKING, EventType.SPEAKING}
)

#: Ordered prefix table. Order is load-bearing: ``VOICE_CALL_`` must be
#: tested before ``VOICE_`` or every call-lifecycle event lands on the voice
#: pipeline topic, which is a different panel with different subscribers.
_PREFIX_TOPIC: tuple[tuple[str, str], ...] = (
    ("TASK_", "task"),
    ("AGENT_", "agent"),
    ("TOOL_", "tool"),
    ("MODEL_", "model"),
    ("BUILD_", "build"),
    ("TEST_", "test"),
    ("DEVICE_", "device"),
    ("WAKE_", "voice"),
    ("STT_", "voice"),
    ("TTS_", "voice"),
    ("SCHEDULE_", "schedule"),
    ("ORB_", "orb"),
    ("TRANSCRIPT_", "orb"),
    ("VOICE_CALL_", "call"),
    ("VOICE_", "voice"),
    ("NODE_", "node"),
    ("SERVER_", "node"),
    ("WINDOWS_", "node"),
    ("ESP32_", "node"),
    ("PERMISSION_", "permission"),
)

#: Every topic the derivation can produce, plus the wildcard. Derived from the
#: tables rather than restated, so a new prefix cannot be added without its
#: topic becoming subscribable on the stream in the same edit.
KNOWN_TOPICS: frozenset[str] = frozenset(
    {ALL_TOPICS, SYSTEM_TOPIC} | {topic for _, topic in _PREFIX_TOPIC} | {"orb"}
)


def topic_for(event_type: str) -> str:
    """Return the topic an event type belongs to.

    Derived from the type name rather than passed in, so a publisher cannot
    file an event under a topic that contradicts its type. An unknown type
    becomes :data:`SYSTEM_TOPIC`, which is the safe direction: a new event
    type reaches subscribers instead of silently reaching nobody.
    """
    if event_type in _EXACT_ORB_STATES:
        return "orb"
    for prefix, topic in _PREFIX_TOPIC:
        if event_type.startswith(prefix):
            return topic
    return SYSTEM_TOPIC


def is_canonical(event_type: str) -> bool:
    """Whether the name is in the canonical catalog (transport names are not)."""
    return event_type in CANONICAL_EVENT_TYPES


__all__ = [
    "ALL_TOPICS",
    "CANONICAL_EVENT_TYPES",
    "KNOWN_TOPICS",
    "SYSTEM_TOPIC",
    "EventType",
    "is_canonical",
    "topic_for",
]
