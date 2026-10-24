"""Voice sessions and the STT -> Core -> Agent -> TTS pipeline (T153/T154, §25/§59.19).

Voice is **another interface onto the same agents and tools**, not a parallel
stack (§59.21). This manager owns that interface: it takes a session, runs the
pipeline, and keeps the conversation history. The model/agent step is a
collaborator (a :data:`Responder`) injected by whoever wires the container, so
this module depends on neither the container nor a model provider and is fully
testable with a fake.

**Barge-in is a first-class behaviour (§59.19).** When the user starts speaking
again while ULTRON is thinking or talking, the in-flight response is cancelled,
the queued synthesis is discarded, and the session returns to listening. The
interrupted turn is *marked*, not dropped, so the next turn still has the right
context -- §59.19's rule that "an interrupted partial turn is marked, not
silently dropped". Cancellation actually cancels: the audio loop checks the
interrupt flag before delivering each chunk and stops immediately, so no stale
audio plays over the user.

State is tracked per session as a small machine
(:class:`VoiceState`), and every transition publishes the matching
``VOICE_*`` event so clients (the desktop orb, the web page, a phone client) can
follow along. ``STT_PARTIAL``/``STT_FINAL`` and ``TTS_STARTED``/``TTS_COMPLETED``
bracket each stage (§25's event list).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Any, cast

from app.config import VoiceSettings
from app.core.errors import NotFoundError
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability import get_logger
from app.voice.stt.base import AudioChunk, STTEngine
from app.voice.tts.base import TTSEngine

__all__ = [
    "AudioSink",
    "Responder",
    "VoiceManager",
    "VoiceReply",
    "VoiceSession",
    "VoiceState",
    "VoiceTurn",
    "build_voice_manager",
]

_LOGGER = get_logger(__name__)

#: Runs the model/agent step for a final transcript. Injected by the container,
#: faked in tests, so the manager never imports a provider.
Responder = Callable[..., Any]  # (text, *, principal, session_id, history) -> str

#: Receives each synthesized audio chunk as it is produced (streaming playback).
AudioSink = Callable[[bytes], Any]

#: How long after we stop speaking a transcript may still be treated as our own
#: echo. Playback drains within a second or two, so anything later is the user.
_ECHO_GRACE_SECONDS = 4.0


def _append_partial(transcript: str, delta: str) -> str:
    """Append a partial-transcript delta, keeping word boundaries readable.

    Streaming engines emit only the *new* suffix of a growing transcript, and
    that suffix is stripped, so consecutive deltas would otherwise read
    ``"kitchenlights"``. A space is inserted only between two word characters,
    which leaves CJK text and punctuation alone.
    """
    if not transcript:
        return delta
    if not delta:
        return transcript
    if transcript[-1].isalnum() and delta[0].isalnum():
        return f"{transcript} {delta}"
    return transcript + delta


def _echo_words(text: str) -> list[str]:
    """Comparable words: lowercased, letters/apostrophes only, length > 2."""
    cleaned = "".join(char if (char.isalpha() or char == "'") else " " for char in text.lower())
    return [word for word in cleaned.split() if len(word) > 2]


def looks_like_echo(spoken: str, heard: str) -> bool:
    """Whether ``heard`` is (probably) ULTRON hearing its own reply ``spoken``.

    TTS plays through the speakers and bleeds back into the open microphone, so
    speech-to-text can transcribe our own words as a new command and the session
    answers itself forever. A transcript counts as an echo when it is contained
    in what we said (either direction, catching partial re-hears) or when at
    least half of its meaningful words already appeared in what we said. The
    ratio is taken over the *transcript*, so a short genuine command never
    overlaps a long spoken line enough to be swallowed.
    """
    spoken_clean = spoken.strip().lower()
    heard_clean = heard.strip().lower()
    if not spoken_clean or not heard_clean:
        return False

    heard_words = _echo_words(heard_clean)
    if not heard_words:
        return heard_clean in spoken_clean or spoken_clean in heard_clean

    if heard_clean in spoken_clean or spoken_clean in heard_clean:
        return True

    spoken_words = set(_echo_words(spoken_clean))
    overlap = sum(1 for word in heard_words if word in spoken_words)
    return overlap / len(heard_words) >= 0.5


class VoiceState(StrEnum):
    """Where a session is in the listen -> think -> speak loop (§59.19)."""

    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    """One spoken turn: what was said, by which side, and whether it was cut off."""

    role: str  # "user" or "assistant"
    text: str
    #: ``True`` for an assistant turn interrupted mid-synthesis (barge-in).
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class VoiceReply:
    """The outcome of one pipeline run."""

    #: The agent's full textual answer.
    text: str

    #: The audio that was synthesized. Truncated if the turn was interrupted.
    audio: bytes

    #: Individual audio chunks, in order (for streaming playback).
    chunks: tuple[bytes, ...] = ()

    #: Whether the turn was cut short by a barge-in (§59.19).
    interrupted: bool = False


@dataclass(slots=True)
class VoiceSession:
    """A live voice conversation. Mutable: its task and interrupt flags change."""

    session_id: str
    principal: str
    state: VoiceState = VoiceState.LISTENING
    device_id: str | None = None
    created_at: float = field(default_factory=monotonic)
    last_active: float = field(default_factory=monotonic)
    transcript: str = ""
    history: list[VoiceTurn] = field(default_factory=list)

    #: The text-to-speech engine the client picked for this session ("" = the
    #: deployment default). The client offers the providers ULTRON can run and
    #: the session names one, so switching provider is a per-session choice
    #: rather than a restart (§25.1).
    tts_engine: str = ""

    #: The voice id the client picked (a provider voice name, or a Fish Audio
    #: cloned-voice reference id). "" keeps the engine's configured default.
    tts_voice: str = ""

    #: The delivery cues the client picked for this session (``calm``,
    #: ``excited``, ...). Only Fish Audio reads them; another provider ignores
    #: them. Empty means the deployment's configured default.
    tts_tags: tuple[str, ...] = ()

    #: The speech-to-text engine the client picked ("" = the deployment
    #: default). "browser" never reaches the server: the browser recognizes
    #: locally and sends text.
    stt_engine: str = ""

    #: The assistant's most recent spoken line and when it finished being spoken,
    #: used to recognise the microphone picking up our own voice (echo guard).
    last_assistant_spoken: str = ""
    spoken_at: float = 0.0

    #: The in-flight pipeline task, so a barge-in can cancel it (§59.19).
    task: asyncio.Task[Any] | None = None

    #: Set by a barge-in; the audio loop stops delivering as soon as it is set.
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_expired(self) -> bool:
        return self.state is VoiceState.ENDED


class VoiceManager:
    """Owns voice sessions and the §25 pipeline. Transport-agnostic."""

    def __init__(
        self,
        stt: STTEngine,
        tts: TTSEngine,
        *,
        responder: Responder | None = None,
        bus: EventBus | None = None,
        settings: VoiceSettings | None = None,
        stt_factory: Callable[[str], STTEngine] | None = None,
        tts_factory: Callable[[str], TTSEngine] | None = None,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self._responder = responder
        self._bus = bus
        self.settings = settings or VoiceSettings()
        self._sessions: dict[str, VoiceSession] = {}
        self._pending: set[asyncio.Task[Any]] = set()
        #: Builds a *named* engine on demand, so a session can pick a provider
        # the deployment did not configure as its default (§25.1).
        self._stt_factory = stt_factory
        self._tts_factory = tts_factory
        self._stt_cache: dict[str, STTEngine] = {}
        self._tts_cache: dict[str, TTSEngine] = {}

    # -- session lifecycle ------------------------------------------------ #
    def create_session(
        self, *, principal: str = "anonymous", device_id: str | None = None
    ) -> VoiceSession:
        session_id = uuid.uuid4().hex
        session = VoiceSession(
            session_id=session_id, principal=principal, device_id=device_id
        )
        self._sessions[session_id] = session
        self._set_state(session, VoiceState.LISTENING, extra={"event": "voice.session_started"})
        _LOGGER.info(
            "voice session started",
            extra={"event": "voice.session_started", "session_id": session_id},
        )
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[VoiceSession]:
        return list(self._sessions.values())

    def end_session(self, session_id: str, *, reason: str = "ended") -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.task = None
        self._set_state(session, VoiceState.ENDED, extra={"reason": reason})
        _LOGGER.info(
            "voice session ended",
            extra={"event": "voice.session_ended", "session_id": session_id, "reason": reason},
        )
        self._sessions.pop(session_id, None)
        return True

    def sweep(self) -> list[str]:
        """Close sessions idle longer than the TTL. Returns the closed ids."""
        ttl = self.settings.session_ttl
        now = monotonic()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_active > ttl
        ]
        for sid in expired:
            self.end_session(sid, reason="idle timeout")
        return expired

    # -- engine selection -------------------------------------------------- #
    def select_engines(
        self,
        session_id: str,
        *,
        tts_engine: str | None = None,
        tts_voice: str | None = None,
        tts_tags: Sequence[str] | None = None,
        stt_engine: str | None = None,
    ) -> VoiceSession:
        """Apply a client engine choice to a session and return it.

        The client picks the speech-to-text and text-to-speech providers ULTRON
        offers; naming one here switches the session over without a restart
        (§25.1). An empty value leaves the current choice alone, and an engine
        that cannot run is reported and ignored rather than silently swapping
        in a different provider.

        ``tts_tags`` are the delivery cues for the session. An empty list clears
        them (the deployment default does not apply either), while ``None``
        leaves the current cues alone.
        """
        session = self._require(session_id)
        if tts_engine is not None:
            requested = tts_engine.strip()
            if requested:
                session.tts_engine = requested
        if tts_voice is not None:
            session.tts_voice = tts_voice.strip()
        if tts_tags is not None:
            session.tts_tags = tuple(tag.strip() for tag in tts_tags if tag.strip())
        if stt_engine is not None:
            requested = stt_engine.strip()
            if requested:
                session.stt_engine = requested
        _LOGGER.info(
            "voice engines selected",
            extra={
                "event": "voice.engines_selected",
                "session_id": session_id,
                "stt": session.stt_engine or self.stt.name,
                "tts": session.tts_engine or self.tts.name,
                "voice": session.tts_voice,
                "tags": list(session.tts_tags),
            },
        )
        return session

    def stt_for(self, session: VoiceSession) -> STTEngine:
        """The speech-to-text engine that serves this session.

        A session may name the engine it wants; the deployment default is used
        when it does not, or when the named engine cannot run -- a stale client
        preference must not leave the session unable to hear anything.
        """
        requested = session.stt_engine.strip()
        if not requested or requested in (self.stt.name, self.settings.stt_engine):
            return self.stt
        engine = self._stt_cache.get(requested)
        if engine is None:
            engine = self._build_stt(requested)
            self._stt_cache[requested] = engine
        return engine

    def tts_for(self, session: VoiceSession) -> TTSEngine:
        """The text-to-speech engine that serves this session.

        A session may name the engine it wants; the deployment default is used
        when it does not, or when the named engine cannot run. A requested
        engine that *can* run is chained to the deployment default, so a
        provider that fails at call time (an exhausted credit balance, a
        rejected key) still leaves the user with speech.
        """
        requested = session.tts_engine.strip()
        if not requested or requested in (self.tts.name, self.settings.tts_engine):
            return self.tts
        return self.resolve_tts(requested)

    def resolve_tts(self, name: str) -> TTSEngine:
        """The named TTS engine, chained to the configured one as its fallback.

        Used by the one-shot synthesis route, which names a provider without
        owning a session. The engine is cached, so repeated requests reuse one
        client (and one model download).
        """
        requested = name.strip()
        if not requested or requested in (self.tts.name, self.settings.tts_engine):
            return self.tts
        engine = self._tts_cache.get(requested)
        if engine is None:
            engine = self._build_tts(requested)
            self._tts_cache[requested] = engine
        return engine

    def _build_stt(self, name: str) -> STTEngine:
        """Build a named STT engine, degrading to the configured one.

        A requested engine is chained to the configured engine only when it is
        genuinely a different, available provider. A configured engine that is
        already a primary/fallback chain is never wrapped again -- naming its
        primary returns that chain, naming its fallback returns the fallback --
        so the reported name can never nest (``groq>faster-whisper>groq``).
        """
        if self._stt_factory is None:
            return self.stt
        engine = self._stt_factory(name)
        if not engine.available:
            _LOGGER.warning(
                "requested stt engine unavailable; using the configured engine",
                extra={
                    "event": "voice.stt_engine_unavailable",
                    "engine": name,
                    "reason": engine.unavailable_reason,
                },
            )
            return self.stt
        chosen = self._without_nesting(engine, self.stt)
        if chosen is not None:
            return chosen
        from app.voice.fallback import FallbackSTTEngine

        chained = FallbackSTTEngine(engine, self.stt)
        chained.name = f"{name}>{self.stt.name}"
        return chained

    def _build_tts(self, name: str) -> TTSEngine:
        """Build a named TTS engine, degrading to the configured one.

        See :meth:`_build_stt`: a chain is never nested, so selecting an engine
        that the configured chain already uses reports that engine (or the
        chain that starts with it) rather than a duplicated name.
        """
        if self._tts_factory is None:
            return self.tts
        engine = self._tts_factory(name)
        if not engine.available:
            _LOGGER.warning(
                "requested tts engine unavailable; using the configured engine",
                extra={
                    "event": "voice.tts_engine_unavailable",
                    "engine": name,
                    "reason": engine.unavailable_reason,
                },
            )
            return self.tts
        chosen = self._without_nesting(engine, self.tts)
        if chosen is not None:
            return chosen
        from app.voice.fallback import FallbackTTSEngine

        chained = FallbackTTSEngine(engine, self.tts)
        chained.name = f"{name}>{self.tts.name}"
        return chained

    @staticmethod
    def _without_nesting(
        engine: STTEngine | TTSEngine, default: STTEngine | TTSEngine
    ) -> Any | None:
        """The default (or its part) that already serves ``engine``, if any."""
        primary = getattr(default, "primary", default)
        fallback = getattr(default, "fallback", None)
        if engine.name == default.name:
            return default
        if engine.name == primary.name:
            return default
        if fallback is not None and engine.name == fallback.name:
            return fallback
        return None

    # -- pipeline --------------------------------------------------------- #
    async def handle_audio(
        self,
        session_id: str,
        chunks: AsyncIterator[AudioChunk],
        *,
        language: str | None = None,
        on_audio: AudioSink | None = None,
    ) -> list[VoiceReply]:
        """Transcribe a stream of audio and run a turn for each final utterance.

        Publishes ``STT_PARTIAL`` as text is recognised and ``STT_FINAL`` when an
        utterance completes, then hands the final transcript to the pipeline.
        Barge-in: a partial that arrives while the session is still speaking
        interrupts the in-flight turn (§59.19).

        ``on_audio`` receives each synthesized chunk as it is produced, so a
        transport (the WebSocket route) can stream playback while the turn is
        still being spoken rather than buffering the whole reply first.
        """
        session = self._require(session_id)
        replies: list[VoiceReply] = []
        session.transcript = ""
        self._set_state(session, VoiceState.LISTENING)

        async for chunk in self.stt_for(session).transcribe_stream(chunks, language=language):
            if not chunk.is_final:
                session.transcript = _append_partial(session.transcript, chunk.text)
                # A new utterance while we are still talking is a barge-in.
                if session.state is VoiceState.SPEAKING and self.settings.interruption_enabled:
                    await self.interrupt(session_id, reason="barge-in")
                self._publish(
                    EventType.STT_PARTIAL,
                    {"text": session.transcript, "session_id": session_id},
                    device_id=session.device_id,
                )
                continue

            text = chunk.text or session.transcript
            session.transcript = ""
            self._publish(
                EventType.STT_FINAL,
                {"text": text, "session_id": session_id},
                device_id=session.device_id,
            )
            if not text.strip():
                continue
            if self._is_echo(session, text):
                # The microphone picked up our own reply. Do not run a turn on it:
                # that is the self-answering loop. Stay listening.
                _LOGGER.info(
                    "voice echo dropped",
                    extra={
                        "event": "voice.echo_dropped",
                        "session_id": session_id,
                        "text": text[:200],
                    },
                )
                continue
            replies.append(await self.run_turn(session_id, text, on_audio=on_audio))
        return replies

    def _is_echo(self, session: VoiceSession, text: str) -> bool:
        """True when ``text`` is likely ULTRON hearing its own last reply.

        Only true inside ``_ECHO_GRACE_SECONDS`` of the last word we spoke, so a
        genuine command given after the reply finishes is never suppressed.
        """
        if not session.last_assistant_spoken.strip() or not text.strip():
            return False
        if monotonic() - session.spoken_at > _ECHO_GRACE_SECONDS:
            return False
        return looks_like_echo(session.last_assistant_spoken, text)

    async def run_turn(
        self,
        session_id: str,
        text: str,
        *,
        on_audio: AudioSink | None = None,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> VoiceReply:
        """Run one full turn: think, then speak, honouring barge-in (§59.19).

        Returns the agent's answer and the audio produced so far. If a barge-in
        interrupts the turn, ``interrupted`` is ``True``, the audio is truncated
        at the point of interruption, and the assistant turn is *marked*
        interrupted rather than dropped, so history stays coherent.

        ``voice`` is the caller's per-session TTS voice preference; ``None`` keeps
        the engine's configured default. The session's own choice wins over the
        caller's, so a client that names a provider and a voice gets both.
        ``tags`` are the delivery cues for this turn; the session's own choice
        wins here too, for the same reason.
        """
        session = self._require(session_id)
        session.interrupt.clear()
        session.history.append(VoiceTurn("user", text))
        self._set_state(session, VoiceState.THINKING)
        session.task = asyncio.current_task()

        try:
            answer = await self._respond(session, text)
        except asyncio.CancelledError:
            # The task itself was cancelled; mark the turn and re-raise.
            session.history.append(VoiceTurn("assistant", "", interrupted=True))
            self._set_state(session, VoiceState.LISTENING)
            raise
        finally:
            session.task = None

        session.history.append(VoiceTurn("assistant", answer))
        self._set_state(session, VoiceState.SPEAKING)
        self._publish(
            EventType.TTS_STARTED, {"text": answer, "session_id": session_id},
            device_id=session.device_id,
        )

        produced: list[bytes] = []
        interrupted = False
        engine = self.tts_for(session)
        spoken_voice = voice or session.tts_voice or None
        spoken_tags = tuple(tags) if tags is not None else session.tts_tags
        async for audio in engine.synthesize_stream(answer, voice=spoken_voice, tags=spoken_tags):
            if session.interrupt.is_set():
                interrupted = True
                break
            produced.append(audio)
            if on_audio is not None:
                await _maybe_await(on_audio(audio))

        if interrupted:
            # §59.19: mark the cut-off turn so the next turn has the right context.
            session.history[-1] = VoiceTurn("assistant", answer, interrupted=True)
        # Remember what we just said so a transcript that repeats it can be
        # recognised as our own echo rather than a new command.
        session.last_assistant_spoken = answer
        session.spoken_at = monotonic()
        self._publish(
            EventType.TTS_COMPLETED,
            {"interrupted": interrupted, "session_id": session_id},
            device_id=session.device_id,
        )
        self._set_state(session, VoiceState.LISTENING)
        return VoiceReply(
            text=answer,
            audio=b"".join(produced),
            chunks=tuple(produced),
            interrupted=interrupted,
        )

    async def run_text_turn(
        self,
        session_id: str,
        text: str,
        *,
        on_audio: AudioSink | None = None,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> VoiceReply:
        """Run one turn from a transcript the client already recognized.

        The local-STT path (browser Web Speech API): the browser owns
        recognition, so no audio is transcribed here. ``STT_FINAL`` is still
        published so the transport and any observers see the same turn boundary
        the audio path produces, then the turn runs exactly like any other.

        A barge-in while this turn is speaking still works: ``interrupt()`` sets
        the same flag ``run_turn`` checks, because both paths share one manager.
        """
        session = self._require(session_id)
        self._publish(
            EventType.STT_FINAL,
            {"text": text, "session_id": session_id},
            device_id=session.device_id,
        )
        if self._is_echo(session, text):
            # Browser STT can also transcribe our reply bleeding into the mic;
            # drop it rather than running the turn.
            _LOGGER.info(
                "voice echo dropped",
                extra={
                    "event": "voice.echo_dropped",
                    "session_id": session_id,
                    "text": text[:200],
                },
            )
            self._set_state(session, VoiceState.LISTENING)
            return VoiceReply(text="", audio=b"", chunks=(), interrupted=False)
        return await self.run_turn(
            session_id, text, on_audio=on_audio, voice=voice, tags=tags
        )

    async def interrupt(self, session_id: str, *, reason: str = "barge-in") -> bool:
        """Cancel the in-flight turn and return to listening (§59.19).

        Sets the interrupt flag (so the audio loop stops delivering at once) and
        cancels the pipeline task (so a model or synthesis call that is still
        running is actually cancelled, not merely ignored).
        """
        session = self._require(session_id)
        session.interrupt.set()
        task = session.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        _LOGGER.info(
            "voice interrupted",
            extra={"event": "voice.interrupted", "session_id": session_id, "reason": reason},
        )
        return True

    def barge_in(self, session_id: str) -> bool:
        """Signal a barge-in without tearing down the consuming loop.

        :meth:`interrupt` cancels the pipeline task, which is right for a
        single finite audio upload but fatal to a long-lived stream: the
        WebSocket session must keep listening after the user talks over ULTRON.
        This only sets the interrupt flag, so the audio loop stops delivering at
        the next chunk and :meth:`run_turn` returns the turn as interrupted,
        while :meth:`handle_audio` carries on with the next utterance.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.interrupt.set()
        _LOGGER.info(
            "voice barge-in",
            extra={"event": "voice.barge_in", "session_id": session_id},
        )
        return True

    # -- internals -------------------------------------------------------- #
    def _require(self, session_id: str) -> VoiceSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError("voice session", session_id)
        session.last_active = monotonic()
        return session

    async def _respond(self, session: VoiceSession, text: str) -> str:
        if self._responder is None:
            return ""
        result = await _maybe_await(
            self._responder(
                text,
                principal=session.principal,
                session_id=session.session_id,
                history=tuple(session.history),
            )
        )
        return cast(str, result)

    def _set_state(
        self, session: VoiceSession, state: VoiceState, *, extra: dict[str, Any] | None = None
    ) -> None:
        session.state = state
        event = {
            VoiceState.LISTENING: EventType.VOICE_LISTENING,
            VoiceState.THINKING: EventType.VOICE_THINKING,
            VoiceState.SPEAKING: EventType.VOICE_SPEAKING,
            VoiceState.ENDED: EventType.VOICE_ENDED,
        }[state]
        payload: dict[str, Any] = {"state": state.value, "session_id": session.session_id}
        if extra:
            payload.update(extra)
        self._publish(event, payload, device_id=session.device_id)

    def _publish(
        self, event_type: EventType, payload: dict[str, Any], *, device_id: str | None = None
    ) -> None:
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g. a synchronous call outside async context):
            # there is nowhere to deliver the event, so drop it rather than fail.
            return
        task = loop.create_task(
            _guarded_publish(self._bus, event_type, payload, device_id),
            name=f"voice-publish-{event_type.value.lower()}",
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def flush(self) -> None:
        """Await every in-flight event publish (tests use this for ordering)."""
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)


async def _guarded_publish(
    bus: EventBus, event_type: EventType, payload: dict[str, Any], device_id: str | None
) -> None:
    try:
        await bus.publish(event_type, payload, device_id=device_id)
    except Exception:
        _LOGGER.exception(
            "voice event publish failed", extra={"event": "voice.publish_failed"}
        )


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def build_voice_manager(
    settings: VoiceSettings,
    bus: EventBus,
    *,
    stt: STTEngine,
    tts: TTSEngine,
    responder: Responder | None = None,
    stt_factory: Callable[[str], STTEngine] | None = None,
    tts_factory: Callable[[str], TTSEngine] | None = None,
) -> VoiceManager:
    """Wire a manager from settings, the event bus and the chosen engines.

    ``stt_factory``/``tts_factory`` build a *named* engine on demand, which is
    what lets a session switch provider without a restart (§25.1).
    """
    return VoiceManager(
        stt,
        tts,
        responder=responder,
        bus=bus,
        settings=settings,
        stt_factory=stt_factory,
        tts_factory=tts_factory,
    )
