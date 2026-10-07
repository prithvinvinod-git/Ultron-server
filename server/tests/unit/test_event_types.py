"""Canonical event catalog (T030, spec §19 + the sections that extend it).

The catalog is a data module, so what is testable is *completeness* and
*derivation* — the two ways it can rot:

1. A later section names an event that never reaches the enum, and two
   producers end up spelling the same fact differently (the §64.9/§58
   ``DEVICE_OFFLINE`` problem the arc calls out by name).
2. A new prefix cannot reach a topic, so its events all land on ``system``
   and the panel that should have received them stays quiet.

Both lists below are transcribed from `server_arc.md` on purpose: the test is
the executable copy of the spec's tables, so a diff in either direction fails.
"""

from __future__ import annotations

import pytest

from app.events import bus
from app.events.bus import CONNECTED_EVENT_TYPE, LAG_EVENT_TYPE, topic_for
from app.events.types import (
    ALL_TOPICS,
    CANONICAL_EVENT_TYPES,
    KNOWN_TOPICS,
    SYSTEM_TOPIC,
    EventType,
    is_canonical,
)

pytestmark = pytest.mark.unit

# -- spec §19 (30) ----------------------------------------------------------
SECTION_19 = [
    "USER_MESSAGE",
    "TASK_CREATED",
    "TASK_STARTED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "AGENT_CREATED",
    "AGENT_STARTED",
    "AGENT_PAUSED",
    "AGENT_COMPLETED",
    "AGENT_FAILED",
    "TOOL_STARTED",
    "TOOL_COMPLETED",
    "TOOL_FAILED",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "BUILD_STARTED",
    "BUILD_FAILED",
    "TEST_FAILED",
    "DEVICE_CONNECTED",
    "DEVICE_DISCONNECTED",
    "WAKE_DETECTED",
    "STT_PARTIAL",
    "STT_FINAL",
    "TTS_STARTED",
    "TTS_COMPLETED",
    "CPU_HIGH",
    "RAM_HIGH",
    "DISK_LOW",
    "GPU_HIGH",
    "SCHEDULE_TRIGGERED",
]

# -- spec §27, desktop orb (5) ---------------------------------------------
SECTION_27 = [
    "ORB_SHOW",
    "ORB_HIDE",
    "ORB_STATE_CHANGED",
    "TRANSCRIPT_PARTIAL",
    "TRANSCRIPT_FINAL",
]

# -- spec §64.9, ESP32 display contract (11) --------------------------------
SECTION_64_9 = [
    "TASK_PROGRESS",
    "NODE_ONLINE",
    "NODE_OFFLINE",
    "SERVER_ONLINE",
    "SERVER_OFFLINE",
    "WINDOWS_ONLINE",
    "WINDOWS_OFFLINE",
    "LISTENING",
    "THINKING",
    "SPEAKING",
    "AGENT_STOPPED",
]

# -- spec §64.15, cross-node protocol (2) -----------------------------------
SECTION_64_15 = [
    "ESP32_ONLINE",
    "ESP32_OFFLINE",
]

# -- spec §65.15, telephony call lifecycle (8) ------------------------------
SECTION_65_15 = [
    "VOICE_CALL_CREATED",
    "VOICE_CALL_RINGING",
    "VOICE_CALL_CONNECTED",
    "VOICE_CALL_LISTENING",
    "VOICE_CALL_THINKING",
    "VOICE_CALL_SPEAKING",
    "VOICE_CALL_ENDED",
    "VOICE_CALL_FAILED",
]

# -- spec §66.8, catalog extensions (15) ------------------------------------
SECTION_66_8 = [
    "CONTEXT_UPDATED",
    "PLAN_CREATED",
    "PLAN_UPDATED",
    "NODE_CAPABILITIES_UPDATED",
    "VOICE_STARTED",
    "VOICE_LISTENING",
    "VOICE_THINKING",
    "VOICE_SPEAKING",
    "VOICE_ENDED",
    "MODEL_SELECTED",
    "MODEL_FAILED",
    "PERMISSION_REQUESTED",
    "PERMISSION_GRANTED",
    "PERMISSION_DENIED",
    "SYSTEM_ERROR",
]

ALL_SPEC_NAMES = (
    SECTION_19 + SECTION_27 + SECTION_64_9 + SECTION_64_15 + SECTION_65_15 + SECTION_66_8
)


class TestCatalogCompleteness:
    def test_every_spec_name_is_in_the_enum(self) -> None:
        missing = [name for name in ALL_SPEC_NAMES if name not in EventType]
        assert missing == [], f"spec names absent from EventType: {missing}"

    def test_the_catalog_is_exactly_the_spec_union(self) -> None:
        """No ghost entries either: the enum may not grow beyond the spec."""
        assert set(EventType) == set(ALL_SPEC_NAMES)

    def test_group_counts_match_the_source_sections(self) -> None:
        assert len(SECTION_19) == 30
        assert len(SECTION_27) == 5
        assert len(SECTION_64_9) == 11
        assert len(SECTION_64_15) == 2
        assert len(SECTION_65_15) == 8
        assert len(SECTION_66_8) == 15
        assert len(EventType) == 71

    def test_transport_signals_are_not_domain_events(self) -> None:
        """``stream.*`` describes the pipe; a domain subscriber must not see it."""
        assert LAG_EVENT_TYPE not in EventType
        assert CONNECTED_EVENT_TYPE not in EventType
        assert not is_canonical(LAG_EVENT_TYPE)
        assert not is_canonical(CONNECTED_EVENT_TYPE)

    def test_the_device_offline_spelling_conflict_is_unresolved_by_guessing(
        self,
    ) -> None:
        """§19 says DEVICE_DISCONNECTED; §58 says DEVICE_OFFLINE.

        The arc defers the winner to T335 — until then the catalog carries
        exactly one spelling, and it is the §19 one.
        """
        assert "DEVICE_OFFLINE" not in EventType
        assert EventType.DEVICE_DISCONNECTED in EventType

    def test_every_member_serialises_to_its_spec_spelling(self) -> None:
        for member in EventType:
            assert member.value == member.value.upper()
            assert str(member) == member.value

    def test_canonical_membership_works_on_plain_strings(self) -> None:
        assert is_canonical("TASK_STARTED")
        assert is_canonical(EventType.TASK_STARTED)
        assert not is_canonical("task.started")
        assert frozenset(EventType) == CANONICAL_EVENT_TYPES


class TestTopicDerivation:
    @pytest.mark.parametrize(
        ("event_type", "topic"),
        [
            # §19 behaviour must not move
            ("TASK_CREATED", "task"),
            ("AGENT_STARTED", "agent"),
            ("USER_MESSAGE", SYSTEM_TOPIC),
            ("CPU_HIGH", SYSTEM_TOPIC),
            ("SCHEDULE_TRIGGERED", "schedule"),
            # §64.9 / §64.15 node lifecycle
            ("TASK_PROGRESS", "task"),
            ("NODE_ONLINE", "node"),
            ("SERVER_OFFLINE", "node"),
            ("WINDOWS_ONLINE", "node"),
            ("ESP32_OFFLINE", "node"),
            ("NODE_CAPABILITIES_UPDATED", "node"),
            # §27 / §64.9 orb states
            ("ORB_SHOW", "orb"),
            ("TRANSCRIPT_FINAL", "orb"),
            ("LISTENING", "orb"),
            ("THINKING", "orb"),
            ("SPEAKING", "orb"),
            ("AGENT_STOPPED", "agent"),
            # voice pipeline vs call lifecycle — order in the prefix table
            ("VOICE_STARTED", "voice"),
            ("VOICE_CALL_CONNECTED", "call"),
            ("VOICE_CALL_FAILED", "call"),
            # §66.8
            ("PERMISSION_DENIED", "permission"),
            ("MODEL_FAILED", "model"),
            ("SYSTEM_ERROR", SYSTEM_TOPIC),
            ("PLAN_CREATED", SYSTEM_TOPIC),
            ("CONTEXT_UPDATED", SYSTEM_TOPIC),
        ],
    )
    def test_derivation(self, event_type: str, topic: str) -> None:
        assert topic_for(event_type) == topic

    def test_every_canonical_type_lands_on_a_known_topic(self) -> None:
        strays = {
            name: topic_for(name) for name in EventType if topic_for(name) not in KNOWN_TOPICS
        }
        assert strays == {}

    def test_known_topics_carries_the_wildcard_and_the_system_topic(self) -> None:
        assert ALL_TOPICS in KNOWN_TOPICS
        assert SYSTEM_TOPIC in KNOWN_TOPICS

    def test_an_unknown_type_reaches_the_system_topic(self) -> None:
        assert topic_for("SOMETHING_BRAND_NEW") == SYSTEM_TOPIC
        assert topic_for("TASKS_CREATED") == SYSTEM_TOPIC


class TestModuleContracts:
    def test_the_bus_reexports_the_catalog_helpers_without_drift(self) -> None:
        """Existing code imports these from the bus; the object must be shared."""
        from app.events import types as types_module

        assert bus.topic_for is types_module.topic_for
        assert bus.KNOWN_TOPICS is types_module.KNOWN_TOPICS
        assert bus.SYSTEM_TOPIC is types_module.SYSTEM_TOPIC
        assert bus.ALL_TOPICS is types_module.ALL_TOPICS
