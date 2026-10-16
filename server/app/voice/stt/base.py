"""Speech-to-text engine interface (T150, spec §25 / §59.17).

The voice subsystem is provider-independent (§25): STT lives behind this
interface and the pipeline never imports an engine. faster-whisper is merely the
first adapter (§59.17), chosen for a specific hardware target (CPU-only i5, no
GPU assumption); a cloud provider is a drop-in behind the same ABC.

**``STT_ENGINE=none`` is a valid deployment, and it degrades honestly.** §59.17
and §64.11 both insist a missing capability is *reported*, not faked. So
:class:`NullSTTEngine` answers ``available == False`` and raises
:class:`~app.core.errors.DependencyUnavailableError` from every transcription
call -- a caller that forgets to check gets a clear 503, never a plausible-looking
but invented transcript.

**Engines are cheap to construct and open nothing until first use** (the same
discipline as the embedding providers, T127). Model weights and processes are
brought up lazily, so an unconfigured or missing engine is discovered at the
first utterance where it can be reported, not at process start where it would
take the server down.

Two helper value types carry audio and text across the seam:

- :class:`AudioChunk` -- a piece of audio plus a voice-activity flag, so the
  engine shares the caller's VAD rather than guessing (§59.19 requires the
  interruption and silence logic to agree).
- :class:`TranscriptChunk` -- text plus whether it is *final*. §59.17 requires a
  partial that can still be corrected by later audio and a final that will not
  change; that distinction is a first-class field, not a convention.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.core.errors import DependencyUnavailableError
from app.observability import get_logger

__all__ = ["AudioChunk", "NullSTTEngine", "STTEngine", "TranscriptChunk"]

_LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """A piece of audio plus its voice-activity decision (§59.19).

    ``is_speech`` is supplied by the caller's VAD, not computed here: the spec
    requires interruption detection and silence detection to *share* one VAD so
    they cannot disagree about whether the user is speaking. The engine consumes
    that decision.
    """

    #: PCM audio. Adapters declare the encoding and sample rate they expect.
    data: bytes

    #: Whether the VAD judged this chunk to contain speech.
    is_speech: bool = True


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    """Transcribed text with its finality and position (§59.17)."""

    #: The transcribed text. May be empty for a silent chunk.
    text: str

    #: ``True`` once the text will no longer change; ``False`` for a partial
    #: that later audio may still correct.
    is_final: bool = False

    #: Identifies which utterance this chunk belongs to, so a consumer can tell
    #: an appended correction from the start of a new turn.
    utterance_id: int = 0

    #: The language the text was transcribed in, when known.
    language: str | None = None

    #: Wall-clock duration of the audio this chunk covers, when the engine knows.
    duration: float | None = field(default=None)


class STTEngine(ABC):
    """Turns audio into text. Provider-independent (§25).

    Implementations are cheap to construct and open no connections or processes
    until first use. Every engine is expected to honour the §59.17 utterance
    rules via :meth:`transcribe_stream`; a one-shot :meth:`transcribe` is
    provided as the simple non-streaming path.
    """

    #: Short name for logs and audit rows (``"faster-whisper"``, ``"none"``).
    name: str = "unnamed"

    #: Sample rate the engine expects, in Hz. Whisper's native rate is 16 kHz;
    #: an engine that wants another rate overrides this and callers resample.
    sample_rate: int = 16000

    @property
    def available(self) -> bool:
        """Whether this engine can transcribe at all.

        ``False`` is a supported state, not an error. Callers that would raise a
        :class:`~app.core.errors.DependencyUnavailableError` can ask first and
        take the honest degraded path instead.
        """
        return True

    @property
    def unavailable_reason(self) -> str | None:
        """Why :attr:`available` is ``False``, or ``None`` when it is ``True``."""
        return None

    @abstractmethod
    async def transcribe(
        self, audio: bytes, *, language: str | None = None
    ) -> str:
        """Transcribe one complete utterance and return its final text.

        The non-streaming convenience path. Engines that support partial results
        still return only the final text here.
        """

    @abstractmethod
    def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        """Transcribe a stream of audio, emitting partial and final chunks.

        This is where the §59.17 policy lives: consecutive transcripts are
        de-overlapped and a pause does not end the utterance, so a mid-sentence
        silence produces *one* utterance with no duplicated text. Emits
        ``is_final=False`` chunks as text is corrected and an ``is_final=True``
        chunk when the utterance boundary fires.
        """

    async def aclose(self) -> None:
        """Release any held model or process. Safe to call more than once."""
        return None


class NullSTTEngine(STTEngine):
    """The honest "no speech-to-text here" engine.

    Chosen when ``STT_ENGINE=none`` or when a selected engine is unconfigured.
    Every transcription raises :class:`~app.core.errors.DependencyUnavailableError`,
    so an accidental dependency on voice input surfaces as a clear 503 rather
    than as an empty transcript a caller might mistake for silence.
    """

    name = "none"

    def __init__(self, *, reason: str = "") -> None:
        self._reason = reason or "no speech-to-text engine is configured"

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    async def transcribe(
        self, audio: bytes, *, language: str | None = None
    ) -> str:
        raise DependencyUnavailableError("stt", reason=self._reason)

    async def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        raise DependencyUnavailableError("stt", reason=self._reason)
        yield TranscriptChunk(text="")  # pragma: no cover - makes this an async gen
