"""Wake-word detection (T152, spec §25 / §26).

A voice session begins when a wake word is heard. The wake word can be detected
in two places, and both funnel into one event:

- **On the server**, from a transcript -- the fallback when a client streams
  audio without a local detector. :meth:`WakeWordDetector.detect` matches the
  configured words against recognised text.
- **On the device**, most importantly the §26 ESP32 wake-word device, which
  listens locally and sends a wake signal. :meth:`WakeWordDetector.signal`
  raises the same :data:`~app.events.types.EventType.WAKE_DETECTED` event, with
  ``source="esp32"``.

Both publish ``WAKE_DETECTED`` (§25's event list) rather than starting a session
themselves: the event is the seam, so the voice manager (T153) is the single
place that reacts to a wake by opening a session, whatever detected it.

**Detection is a word-boundary match, not a substring match.** ``"ultron"``
must match ``"Ultron, play some music"`` but must *not* match ``"ultronix"`` or a
word that merely contains the letters. Matching is case-insensitive and the
wake word is then stripped from the utterance so the command text handed to the
agent is ``"play some music"``, not ``"ultron play some music"``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.config import VoiceSettings
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability import get_logger

__all__ = ["WakeMatch", "WakeWordDetector", "build_wake_detector"]

_LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WakeMatch:
    """The result of matching a transcript against the wake words."""

    #: The wake word that matched, as configured (original casing).
    word: str

    #: The utterance with the wake word removed and whitespace collapsed.
    command: str


class WakeWordDetector:
    """Matches wake words in text and raises ``WAKE_DETECTED`` (spec §25/§26)."""

    def __init__(
        self,
        wake_words: Sequence[str],
        *,
        bus: EventBus | None = None,
    ) -> None:
        self.wake_words = tuple(word for word in wake_words if word.strip())
        self._bus = bus
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (word, re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE))
            for word in self.wake_words
        ]

    @property
    def enabled(self) -> bool:
        """Whether any wake word is configured."""
        return bool(self._patterns)

    def match(self, text: str) -> WakeMatch | None:
        """Return the first wake word found in ``text``, or ``None``.

        Pure and synchronous, so it can be tested without a bus or an event loop.
        """
        if not text or not self._patterns:
            return None
        for word, pattern in self._patterns:
            found = pattern.search(text)
            if found is not None:
                command = pattern.sub("", text, count=1)
                command = " ".join(command.split())
                command = command.lstrip(" ,.:;!?-").strip()
                return WakeMatch(word=word, command=command)
        return None

    async def detect(
        self,
        text: str,
        *,
        source: str = "stt",
        session_id: str | None = None,
        device_id: str | None = None,
    ) -> WakeMatch | None:
        """Match ``text`` and, on a hit, publish ``WAKE_DETECTED``.

        Returns the :class:`WakeMatch` (so a caller can continue straight into the
        command) or ``None`` for no wake word.
        """
        match = self.match(text)
        if match is None:
            return None
        await self._publish(
            wake_word=match.word,
            command=match.command,
            source=source,
            session_id=session_id,
            device_id=device_id,
        )
        return match

    async def signal(
        self,
        *,
        wake_word: str | None = None,
        source: str = "esp32",
        session_id: str | None = None,
        device_id: str | None = None,
    ) -> None:
        """Raise ``WAKE_DETECTED`` for a device-side detection (§26 ESP32).

        The device already decided a wake word was heard; this records it without
        re-matching text. ``wake_word`` defaults to the first configured word.
        """
        await self._publish(
            wake_word=wake_word or (self.wake_words[0] if self.wake_words else ""),
            command="",
            source=source,
            session_id=session_id,
            device_id=device_id,
        )

    async def _publish(
        self,
        *,
        wake_word: str,
        command: str,
        source: str,
        session_id: str | None,
        device_id: str | None,
    ) -> None:
        if self._bus is None:
            return
        payload = {
            "wake_word": wake_word,
            "command": command,
            "source": source,
            "session_id": session_id,
        }
        await self._bus.publish(
            EventType.WAKE_DETECTED, payload, device_id=device_id
        )
        _LOGGER.info(
            "wake word detected",
            extra={
                "event": "voice.wake_detected",
                "wake_word": wake_word,
                "source": source,
                "session_id": session_id,
            },
        )


def build_wake_detector(
    settings: VoiceSettings, bus: EventBus, *, wake_words: Iterable[str] | None = None
) -> WakeWordDetector:
    """Build a detector from settings, optionally overriding the wake words."""
    words = list(wake_words) if wake_words is not None else list(settings.wake_words)
    return WakeWordDetector(words, bus=bus)
