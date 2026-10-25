"""Orb state vocabulary (spec §59.13 / §27).

The orb is a *display*: it renders state, it never invents it. The wire value is
a lowercase string so the desktop renderer, the PWA and the ESP32 all read the
same vocabulary without a second translation table.

``HIDDEN`` and ``DISCONNECTED`` are deliberately part of the state set even
though the server never publishes ``DISCONNECTED``: a disconnected orb has no
server to hear from, so the client derives it from the transport. Keeping it in
the vocabulary means every renderer names the same thing.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ORB_STATE_SCHEMA_VERSION", "PLACEHOLDER_STATES", "OrbState"]

#: Version of the ``ORB_*`` payload contract. Bumped when a payload field is
#: removed or changes meaning (additive fields do not bump it). The envelope
#: itself is the event bus's, which is stable by construction.
ORB_STATE_SCHEMA_VERSION = 1


class OrbState(StrEnum):
    """Every state a renderer may display."""

    HIDDEN = "hidden"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    EXECUTING = "executing"
    RESPONDING = "responding"
    SPEAKING = "speaking"
    NOTIFICATION = "notification"
    ERROR = "error"
    DISCONNECTED = "disconnected"


#: States a server publisher may emit. ``DISCONNECTED`` is client-derived: the
#: only server that could report its own disconnection is the one that is down.
PLACEHOLDER_STATES: frozenset[OrbState] = frozenset(OrbState) - {OrbState.DISCONNECTED}
