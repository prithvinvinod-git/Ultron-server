"""Wake-word detection and the events it raises (T152, spec §25 / §26).

A wake word is what turns idle listening into a voice session. It may be heard
on the server (from a transcript) or on a device (the §26 ESP32 wake-word
device); either way :class:`~app.voice.wake.detector.WakeWordDetector` raises the
single :data:`~app.events.types.EventType.WAKE_DETECTED` event the voice manager
reacts to.
"""

from __future__ import annotations

from app.voice.wake.detector import WakeMatch, WakeWordDetector, build_wake_detector

__all__ = ["WakeMatch", "WakeWordDetector", "build_wake_detector"]
