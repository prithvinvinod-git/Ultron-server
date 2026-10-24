"""Curated TTS voice registry and id normalisation (spec §25 / §59.18).

The reference client ships a curated voice list and never hands an arbitrary
string to the engine: a friendly key or an unknown value resolves to a
known-good voice, so a stale pick can never make synthesis fail with the
engine's opaque "no audio was received" (T151).

This module is that registry for ULTRON, kept engine-neutral: each adapter owns
its engine ids, but the **validation rule lives here once** so the routes, the
adapters and the pickers cannot disagree about what a valid voice is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "EDGE_DEFAULT_VOICE",
    "ELEVEN_DEFAULT_VOICE",
    "TtsVoice",
    "edge_voices",
    "eleven_voices",
    "normalize_edge_voice",
    "normalize_eleven_voice",
]

#: The Edge voice used when nothing valid is chosen. Known to always work.
EDGE_DEFAULT_VOICE = "en-US-AriaNeural"

#: The ElevenLabs voice used when nothing valid is chosen (public "Rachel").
ELEVEN_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"

#: An Edge neural voice id looks like ``en-US-AriaNeural`` /
#: ``en-GB-RyanNeural`` / ``en-US-AndrewMultilingualNeural``.
_EDGE_ID = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")


@dataclass(frozen=True, slots=True)
class TtsVoice:
    """One selectable voice: a stable key, a display label and the engine id."""

    id: str
    label: str


#: Curated Edge voices. Only ids verified to synthesise, ordered best-first
#: (the newer Multilingual generation sounds the most natural).
_EDGE_VOICES: tuple[TtsVoice, ...] = (
    TtsVoice("en-US-AndrewMultilingualNeural", "Andrew (US, male)"),
    TtsVoice("en-US-BrianMultilingualNeural", "Brian (US, male)"),
    TtsVoice("en-US-AvaMultilingualNeural", "Ava (US, female)"),
    TtsVoice("en-US-EmmaMultilingualNeural", "Emma (US, female)"),
    TtsVoice("en-US-AriaNeural", "Aria (US, female)"),
    TtsVoice("en-US-GuyNeural", "Guy (US, male)"),
    TtsVoice("en-US-JennyNeural", "Jenny (US, female)"),
    TtsVoice("en-US-ChristopherNeural", "Christopher (US, male)"),
    TtsVoice("en-GB-SoniaNeural", "Sonia (UK, female)"),
    TtsVoice("en-GB-RyanNeural", "Ryan (UK, male)"),
    TtsVoice("en-AU-NatashaNeural", "Natasha (AU, female)"),
    TtsVoice("en-IN-NeerjaNeural", "Neerja (IN, female)"),
)

#: Common ElevenLabs public voice ids (the account's own library also works).
_ELEVEN_VOICES: tuple[TtsVoice, ...] = (
    TtsVoice("21m00Tcm4TlvDq8ikWAM", "Rachel"),
    TtsVoice("EXAVITQu4vr4xnSDxMaL", "Bella"),
    TtsVoice("onwK4e9ZLuTAKqWW03F9", "Domi"),
    TtsVoice("JBFqnCBsd6RMkjVDRZzb", "George"),
    TtsVoice("pNInz6obpgDQGcFmaJgB", "Adam"),
    TtsVoice("IKne3meq5aSn9XLyUdCD", "Charlie"),
)

_EDGE_IDS = frozenset(voice.id for voice in _EDGE_VOICES)
_ELEVEN_IDS = frozenset(voice.id for voice in _ELEVEN_VOICES)


def edge_voices() -> list[TtsVoice]:
    """The curated Edge voice list offered to the front door."""
    return list(_EDGE_VOICES)


def eleven_voices() -> list[TtsVoice]:
    """The curated ElevenLabs voice list offered to the front door."""
    return list(_ELEVEN_VOICES)


def normalize_edge_voice(voice: str | None, *, fallback: str = EDGE_DEFAULT_VOICE) -> str:
    """Return a voice id the Edge endpoint will accept, never an invalid one.

    ``None``/empty, a curated id or a well-formed ``en-XX-NameNeural`` id is
    honoured; anything else (a stale friendly key, a name, garbage) resolves to
    ``fallback`` so synthesis cannot fail with the engine's opaque error.
    """
    candidate = (voice or "").strip()
    if not candidate:
        return fallback
    if candidate in _EDGE_IDS or _EDGE_ID.match(candidate):
        return candidate
    keyed = next((v for v in _EDGE_VOICES if v.label.lower().startswith(candidate.lower())), None)
    return keyed.id if keyed else fallback


def normalize_eleven_voice(
    voice: str | None, *, fallback: str = ELEVEN_DEFAULT_VOICE
) -> str:
    """Return an ElevenLabs voice id, falling back rather than guessing.

    ElevenLabs ids are opaque, so only a curated id or a non-empty value is
    accepted; the caller's configured ``elevenlabs_voice_id`` is supplied as the
    ``fallback``.
    """
    candidate = (voice or "").strip()
    if not candidate:
        return fallback
    if candidate in _ELEVEN_IDS:
        return candidate
    keyed = next(
        (v for v in _ELEVEN_VOICES if v.label.lower() == candidate.lower()), None
    )
    if keyed:
        return keyed.id
    # Opaque but non-empty: let ElevenLabs judge it (it returns a clear 4xx).
    return candidate
