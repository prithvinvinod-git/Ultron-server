"""Voice routes: engine availability and the realtime voice WebSocket (T154, §25).

Two surfaces:

``GET /voice/engines`` reports which STT and TTS engines are configured and
whether they can actually run, so the web front door can show "voice is
available" honestly instead of offering a button that fails at the first press
(§64.11).

``WS /voice/ws`` is the realtime conversation. The client streams raw
audio frames up; the server answers with JSON status/stt/answer frames and raw
synthesized audio down. It is a WebSocket rather than request/response because
voice is inherently a stream: partial transcripts, barge-in and progressive
playback only make sense while the connection is open.

**The socket is transport, not logic.** It owns no pipeline state: it feeds
:class:`~app.voice.manager.VoiceManager` a stream of :class:`AudioChunk` and
relays what the manager does -- turning the bus events it publishes into frames
and its synthesized chunks into binary frames. Everything speech-shaped
(utterance boundaries, the §59.17 partial/final rule, barge-in) stays in the
manager, so this route is thin and testable.

**Authentication is by query token.** A browser cannot set an ``Authorization``
header on a WebSocket handshake, so the session token rides as ``?token=``. It
is validated through the same authenticator every other route uses; an
unauthenticated handshake is accepted just long enough to send one error frame
and then closed with a policy-violation code.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    STATIC_PRINCIPAL_ID,
    STATIC_PRINCIPAL_USERNAME,
    Container,
)
from app.core.errors import DependencyUnavailableError, InvalidInputError, UltronError
from app.events.bus import Subscription
from app.events.types import EventType
from app.observability import get_logger
from app.security.audit import ACTOR_USER
from app.security.authentication import Principal
from app.voice.manager import VoiceManager, VoiceSession, VoiceState
from app.voice.stt import build_stt_engine
from app.voice.stt.base import AudioChunk
from app.voice.tts import build_tts_engine
from app.voice.tts.availability import (
    edge_tts_available,
    edge_tts_unavailable_reason,
)
from app.voice.tts.voices import (
    edge_voices,
    eleven_voices,
    normalize_edge_voice,
)

router = APIRouter(prefix="/voice", tags=["voice"])

_LOGGER = get_logger(__name__)

#: RFC 6455 policy-violation close code, used when a handshake is not authorized.
_WS_POLICY_VIOLATION = 1008

#: How many inbound audio frames may be buffered before the reader blocks. A
#: bound exists so a client that uploads faster than the pipeline drains cannot
#: grow the server's memory without limit.
_AUDIO_QUEUE_SIZE = 256

#: A JSON frame sender, already serialized against concurrent sends on the socket.
SendJson = Callable[[dict[str, Any]], Awaitable[None]]

#: An audio sink: receives each synthesized chunk as it is produced.
SendAudio = Callable[[bytes], Awaitable[None]]

#: Bus event type -> the WS ``type`` it is relayed as. Only the voice-topic
#: events the manager publishes are listed; anything else on the bus is left to
#: the ``/events`` stream.
_FRAME_FOR_EVENT: dict[str, str] = {
    EventType.STT_PARTIAL.value: "stt_partial",
    EventType.STT_FINAL.value: "stt_final",
    EventType.VOICE_LISTENING.value: "state",
    EventType.VOICE_THINKING.value: "state",
    EventType.VOICE_SPEAKING.value: "state",
    EventType.TTS_STARTED.value: "answer",
    EventType.TTS_COMPLETED.value: "tts_completed",
    EventType.VOICE_ENDED.value: "ended",
}


class EngineInfo(BaseModel):
    """One engine's identity and whether it can currently run."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    available: bool
    reason: str | None = None
    sample_rate: int
    content_type: str | None = None


class VoiceEngines(BaseModel):
    """The configured voice stack, as the front door needs to see it."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    stt: EngineInfo
    tts: EngineInfo


class TtsRequest(BaseModel):
    """A one-shot text-to-speech request (greetings, notifications, ...)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    voice: str | None = None
    #: The provider to synthesise with (``edge-tts``, ``elevenlabs``,
    #: ``fish-audio``, ``piper``). Empty means the configured engine.
    engine: str | None = None
    #: Delivery cues for the line (``calm``, ``excited``, ``angry``, ...). Only
    #: Fish Audio renders them; the other providers ignore them. A list and a
    #: comma-separated string are both accepted.
    tags: list[str] | str | None = None


class VoiceOption(BaseModel):
    """One selectable voice for a TTS provider."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class TtsProviderOptions(BaseModel):
    """A TTS provider the front door can offer, with its voices."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    label: str
    available: bool
    reason: str | None = None
    default_voice: str = ""
    voices: list[VoiceOption] = []


class SttProviderOptions(BaseModel):
    """A speech-to-text provider the front door can offer."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    label: str
    available: bool
    reason: str | None = None
    sample_rate: int = 16000


class VoiceOptions(BaseModel):
    """Everything the Settings voice pickers need in one call."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    stt_engines: list[str]
    stt: list[SttProviderOptions] = []
    tts: list[TtsProviderOptions]


#: Cap on a single one-shot synthesis, mirroring the reference client's guard.
_TTS_MAX_CHARS = 4000


@router.get(
    "/engines",
    response_model=VoiceEngines,
    summary="Report the configured STT/TTS engines and their availability",
)
async def engines(container: Container) -> VoiceEngines:
    """Describe the voice stack without opening it.

    Building the manager is cheap and opens nothing (engines load lazily), so
    this is safe to call on page load. ``available`` is the honest answer from
    each engine's own probe, never a guess (§64.11).
    """
    manager = container.voice
    stt, tts = manager.stt, manager.tts
    return VoiceEngines(
        enabled=bool(container.settings.voice.enabled),
        stt=EngineInfo(
            engine=stt.name,
            available=stt.available,
            reason=stt.unavailable_reason,
            sample_rate=stt.sample_rate,
        ),
        tts=EngineInfo(
            engine=tts.name,
            available=tts.available,
            reason=tts.unavailable_reason,
            sample_rate=tts.sample_rate,
            content_type=tts.content_type,
        ),
    )


@router.post(
    "/tts",
    summary="Synthesise one line of text to audio (no agent turn)",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
async def tts(container: Container, body: TtsRequest) -> Response:
    """Synthesise a single line directly, without running the agent.

    The entry greeting and similar one-off lines do not need a model call, so
    this returns the audio for exactly the text given. The engine's own media
    type is preserved (``audio/mpeg`` for Edge, ``audio/L16`` for Piper) so the
    client can play it with a plain ``<audio>`` element.

    ``engine`` names the provider to use. The named engine is chained to the
    configured one, so a provider that fails at call time (an exhausted credit
    balance, a rejected key) still returns speech; a provider that cannot run
    at all is reported as an error rather than quietly answered with a
    different voice (§64.11).
    """
    text = body.text.strip()
    if not text:
        raise InvalidInputError("text is required")
    if len(text) > _TTS_MAX_CHARS:
        raise InvalidInputError(f"text too long (max {_TTS_MAX_CHARS} chars)")
    engine = container.voice.tts
    requested = (body.engine or "").strip()
    if requested:
        engine = _named_tts(container, requested)
    if not engine.available:
        raise DependencyUnavailableError(
            "tts", reason=engine.unavailable_reason or "no tts engine"
        )
    audio = await engine.synthesize(text, voice=body.voice, tags=_tags(body.tags))
    return Response(
        content=audio,
        media_type=engine.content_type,
        headers={"Cache-Control": "no-store", "X-TTS-Engine": engine.name},
    )


def _named_tts(container: Container, name: str) -> Any:
    """The named TTS engine, chained to the configured one as its fallback."""
    settings = container.settings.voice
    probe = build_tts_engine(settings, name)
    if not probe.available:
        raise DependencyUnavailableError(
            "tts",
            reason=probe.unavailable_reason or f"the '{name}' engine cannot run",
        )
    return container.voice.resolve_tts(name)


_TTS_LABELS: dict[str, str] = {
    "edge-tts": "Edge TTS (free)",
    "elevenlabs": "ElevenLabs (cloud)",
    "piper": "Piper (local)",
    "fish-audio": "Fish Audio (cloned voice)",
}

_STT_LABELS: dict[str, str] = {
    "groq": "Groq Whisper (cloud)",
    "faster-whisper": "Whisper (local, faster-whisper)",
    "browser": "Browser (local, Web Speech API)",
}

#: Every provider the front door may offer, in the order they are shown. The
#: configured engines come first so the deployment's own choice is the default
#: the client picks.
_TTS_ORDER: tuple[str, ...] = ("fish-audio", "edge-tts", "elevenlabs", "piper")
_STT_ORDER: tuple[str, ...] = ("faster-whisper", "groq", "browser")


@router.get(
    "/options",
    response_model=VoiceOptions,
    summary="List selectable STT/TTS providers with their honest availability",
)
async def options(container: Container) -> VoiceOptions:
    """Return the voice pickers' data in one honest call.

    Every provider ULTRON knows about is listed -- not just the ones the
    deployment happens to run -- because the client may switch provider per
    session (§25.1). ``available`` mirrors the engine's own probe, so the front
    door greys out a provider that is present in config but cannot run (e.g.
    ElevenLabs with no key) instead of offering it and failing at synthesis
    (§64.11).

    A cloned voice (Fish Audio) is a *voice*, not a provider: the configured
    ``FISH_AUDIO_VOICE_ID`` is offered as that provider's default voice, so the
    user can select the voice they cloned without retyping its id.
    """
    settings = container.settings.voice
    manager = container.voice

    providers: list[TtsProviderOptions] = []
    for engine_name in _ordered(settings.tts_engine, settings.tts_fallback_engine, _TTS_ORDER):
        provider = _tts_provider(engine_name, settings)
        if provider is not None:
            providers.append(provider)

    # The primary engine is what the manager actually built; if it differs from
    # the list above (a fallback is live), make sure it is represented.
    if not providers:
        providers.append(
            TtsProviderOptions(
                engine=manager.tts.name,
                label=_TTS_LABELS.get(manager.tts.name, manager.tts.name),
                available=manager.tts.available,
                reason=manager.tts.unavailable_reason,
            )
        )

    stt_providers: list[SttProviderOptions] = []
    for engine_name in _ordered(settings.stt_engine, settings.stt_fallback_engine, _STT_ORDER):
        stt_providers.append(_stt_provider(engine_name, settings))

    return VoiceOptions(
        enabled=bool(settings.enabled),
        stt_engines=[provider.engine for provider in stt_providers],
        stt=stt_providers,
        tts=providers,
    )


def _ordered(primary: str, fallback: str, order: tuple[str, ...]) -> list[str]:
    """The configured engines first, then the rest in their listed order."""
    names = [name for name in (primary, fallback) if name and name != "none"]
    names.extend(name for name in order if name not in names)
    return list(dict.fromkeys(names))


def _tts_provider(engine_name: str, settings: Any) -> TtsProviderOptions | None:
    """One TTS provider, with its voices and its honest availability."""
    if engine_name == "edge-tts":
        return TtsProviderOptions(
            engine="edge-tts",
            label=_TTS_LABELS["edge-tts"],
            available=edge_tts_available(),
            reason=edge_tts_unavailable_reason(),
            default_voice=normalize_edge_voice(settings.edge_tts_voice),
            voices=_to_options(edge_voices()),
        )
    if engine_name == "elevenlabs":
        return TtsProviderOptions(
            engine="elevenlabs",
            label=_TTS_LABELS["elevenlabs"],
            available=bool(settings.elevenlabs_api_key.get_secret_value().strip()),
            reason=(
                None
                if settings.elevenlabs_api_key.get_secret_value().strip()
                else "ELEVENLABS_API_KEY is not configured"
            ),
            default_voice=settings.elevenlabs_voice_id,
            voices=_to_options(eleven_voices()),
        )
    if engine_name == "piper":
        return TtsProviderOptions(
            engine="piper",
            label=_TTS_LABELS["piper"],
            available=bool(settings.tts_model_path),
            reason=(
                None
                if settings.tts_model_path
                else "no Piper voice model is configured"
            ),
            default_voice=settings.tts_model_path,
            voices=[],
        )
    if engine_name == "fish-audio":
        voice_id = settings.fish_audio_voice_id.strip()
        available = bool(
            settings.fish_audio_api_key.get_secret_value().strip() and voice_id
        )
        return TtsProviderOptions(
            engine="fish-audio",
            label=_TTS_LABELS["fish-audio"],
            available=available,
            reason=(
                None
                if available
                else "FISH_AUDIO_API_KEY and FISH_AUDIO_VOICE_ID must be configured"
            ),
            default_voice=voice_id,
            # A cloned voice is the provider's own voice: its reference id is
            # what Fish Audio speaks with.
            voices=(
                [VoiceOption(id=voice_id, label="Your cloned voice")]
                if voice_id
                else []
            ),
        )
    return None


def _stt_provider(engine_name: str, settings: Any) -> SttProviderOptions:
    """One STT provider, with its honest availability."""
    if engine_name == "browser":
        return SttProviderOptions(
            engine="browser",
            label=_STT_LABELS["browser"],
            available=True,
            reason=None,
            sample_rate=16000,
        )
    engine = build_stt_engine(settings, engine_name)
    return SttProviderOptions(
        engine=engine_name,
        label=_STT_LABELS.get(engine_name, engine_name),
        available=engine.available,
        reason=engine.unavailable_reason,
        sample_rate=engine.sample_rate,
    )


def _to_options(voices: list[Any]) -> list[VoiceOption]:
    return [VoiceOption(id=voice.id, label=voice.label) for voice in voices]


@router.websocket("/ws")
async def voice_ws(websocket: WebSocket) -> None:
    """Realtime voice: audio frames up, STT/answer/audio frames down.

    The protocol is deliberately small. Down, JSON frames:

    - ``session`` -- opened once: the session id, sample rate, and the engine names.
    - ``state`` -- ``listening`` / ``thinking`` / ``speaking``.
    - ``stt_partial`` / ``stt_final`` -- the recognized text (§59.17).
    - ``answer`` -- the assistant's full textual answer.
    - ``tts_completed`` -- synthesis finished (``interrupted`` for a barge-in).
    - ``ended`` / ``error`` -- terminal.
    - binary frames -- raw synthesized audio (rate/encoding in the ``session``
      frame; ``audio/L16`` means little-endian PCM).

    Up, binary frames are audio; JSON control frames are ``{"type":"interrupt"}``
    (barge-in), ``{"type":"utterance_end"}`` (the client's VAD saw the user stop
    talking, so close the turn now), ``{"type":"config"}`` (the client picked a
    speech-to-text / text-to-speech provider or voice) and ``{"type":"stop"}``
    (end the session).
    """
    container = getattr(websocket.app.state, "container", None)
    if container is None:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    await websocket.accept()

    principal = _principal()
    await _run_session(websocket, container, principal)


def _principal() -> Principal:
    """The caller for a voice session.

    Built-in authentication has been removed (auth moves to an external provider,
    Firebase), so every voice socket is attributed to the same static
    administrator — no ``token`` query parameter is checked. The WS handshake is
    still accepted before this runs, so a future verifier can close an
    unauthenticated socket here instead; that is the single place this changes.
    """
    return Principal(
        actor_type=ACTOR_USER,
        user_id=STATIC_PRINCIPAL_ID,
        username=STATIC_PRINCIPAL_USERNAME,
        is_superuser=True,
    )


async def _run_session(
    websocket: WebSocket, container: Container, principal: Principal
) -> None:
    """Drive one voice session for the life of the socket."""
    manager = container.voice
    session = manager.create_session(principal=principal.actor_id)
    session_id = session.session_id
    queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=_AUDIO_QUEUE_SIZE)
    send_lock = asyncio.Lock()

    async def send_json(payload: dict[str, Any]) -> None:
        await _safe_send_json(websocket, payload, lock=send_lock)

    async def send_audio(data: bytes) -> None:
        if not data:
            return
        async with send_lock:
            with contextlib.suppress(Exception):
                await websocket.send_bytes(data)

    try:
        subscription = container.events.subscribe(topics=["voice"])
    except Exception as exc:
        await send_json(_error_frame(exc))
        manager.end_session(session_id, reason="subscribe failed")
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    await send_json(_session_frame(manager, session))

    forward_task = asyncio.create_task(
        _forward_events(subscription, session_id, send_json),
        name=f"voice-forward-{session_id}",
    )
    pipeline_task = asyncio.create_task(
        _pipeline(manager, session_id, queue, send_audio, send_json),
        name=f"voice-pipeline-{session_id}",
    )

    try:
        await _pump(websocket, manager, session_id, queue, send_json, send_audio)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await send_json(_error_frame(exc))
    finally:
        forward_task.cancel()
        pipeline_task.cancel()
        for task in (forward_task, pipeline_task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        subscription.close()
        manager.end_session(session_id, reason="socket closed")
        _LOGGER.info(
            "voice websocket closed",
            extra={"event": "voice.ws_closed", "session_id": session_id},
        )


async def _pipeline(
    manager: VoiceManager,
    session_id: str,
    queue: asyncio.Queue[AudioChunk | None],
    on_audio: SendAudio,
    send_json: SendJson,
) -> None:
    """Run the manager pipeline against the audio queue, reporting failures as frames."""
    try:
        await manager.handle_audio(session_id, _audio_stream(queue), on_audio=on_audio)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _LOGGER.exception(
            "voice pipeline failed",
            extra={"event": "voice.pipeline_failed", "session_id": session_id},
        )
        await send_json(_error_frame(exc))


async def _audio_stream(
    queue: asyncio.Queue[AudioChunk | None],
) -> AsyncIterator[AudioChunk]:
    """Yield queued audio until a ``None`` sentinel closes the stream."""
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        yield chunk


async def _forward_events(
    subscription: Subscription,
    session_id: str,
    send_json: SendJson,
) -> None:
    """Relay this session's voice-bus events to the client as JSON frames."""
    while True:
        envelope = await subscription.get()
        if envelope is None:
            return
        if envelope.payload.get("session_id") != session_id:
            continue
        frame_type = _FRAME_FOR_EVENT.get(envelope.event_type)
        if frame_type is None:
            continue
        await send_json(_event_frame(frame_type, envelope.payload))


async def _pump(
    websocket: WebSocket,
    manager: VoiceManager,
    session_id: str,
    queue: asyncio.Queue[AudioChunk | None],
    send_json: SendJson,
    send_audio: SendAudio,
) -> None:
    """Feed inbound frames: binary audio into the pipeline, JSON as control.

    A frame of audio that arrives while ULTRON is thinking or speaking is a
    barge-in (§59.19): the interrupt flag is raised so the current turn stops,
    and the same audio starts the next utterance. The flag is used rather than a
    task cancel so this socket keeps listening afterwards.
    """
    interruptible = {VoiceState.THINKING, VoiceState.SPEAKING}
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return

        data = message.get("bytes")
        if data:
            await queue.put(AudioChunk(data=data))
            session = manager.get(session_id)
            if (
                session is not None
                and manager.settings.interruption_enabled
                and session.state in interruptible
            ):
                manager.barge_in(session_id)
            continue

        text = message.get("text")
        if text is None:
            continue
        # A ``{"type":"config"}`` frame names the providers the client wants
        # for this session; the server answers with a fresh ``session`` frame so
        # the client learns the engine's rate and media type.
        if _is_config_frame(text):
            await _apply_config(text, manager, session_id, send_json)
            continue
        # A ``{"type":"text"}`` frame carries a transcript the *client* produced
        # (browser Web Speech API when the user chose the local STT option), so
        # the turn runs without the server transcribing anything.
        if _is_text_turn(text):
            await _run_text_turn(text, manager, session_id, send_audio)
            continue
        if _handle_control(text, manager, session_id, queue):
            return


def _is_config_frame(text: str) -> bool:
    """Whether a JSON frame is a client engine-selection frame."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "config"


async def _apply_config(
    text: str,
    manager: VoiceManager,
    session_id: str,
    send_json: SendJson,
) -> None:
    """Apply the client's provider choice and report the session back.

    The client picks the speech-to-text and text-to-speech providers ULTRON
    offers (§25.1); naming one here switches the session over. The reply is a
    ``session`` frame, so the client picks up the new engine's sample rate and
    media type before the next turn.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    tts = payload.get("tts")
    tts_engine = tts.get("engine") if isinstance(tts, dict) else payload.get("tts_engine")
    tts_voice = tts.get("voice") if isinstance(tts, dict) else payload.get("voice")
    tts_tags = tts.get("tags") if isinstance(tts, dict) else payload.get("tts_tags")
    stt = payload.get("stt")
    stt_engine = stt.get("engine") if isinstance(stt, dict) else stt

    session = manager.select_engines(
        session_id,
        tts_engine=tts_engine if isinstance(tts_engine, str) else None,
        tts_voice=tts_voice if isinstance(tts_voice, str) else None,
        tts_tags=_tags(tts_tags),
        stt_engine=stt_engine if isinstance(stt_engine, str) else None,
    )
    await send_json(_session_frame(manager, session))


def _tags(raw: object) -> list[str] | None:
    """The delivery cues in a frame, or ``None`` when the frame names none.

    A list of strings and a comma-separated string are both accepted, and an
    empty value means "no cues" rather than "leave the current ones alone".
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        parts = [part for part in raw if isinstance(part, str)]
    else:
        return None
    return [part.strip() for part in parts if part.strip()]


def _is_text_turn(text: str) -> bool:
    """Whether a JSON frame is a client-provided transcript turn."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "text"


async def _run_text_turn(
    text: str,
    manager: VoiceManager,
    session_id: str,
    send_audio: SendAudio,
) -> None:
    """Run one turn from a transcript the client already recognized.

    Used when the client chose the local (browser Web Speech API) STT option: the
    browser owns recognition, so the server is handed the finished text and skips
    its own STT entirely. The manager publishes ``STT_FINAL`` for parity with the
    audio path and streams the synthesized reply back through ``send_audio``.

    The frame may also name the text-to-speech provider and voice for this
    turn, so a client that switched provider mid-conversation is obeyed.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    transcript = str(payload.get("text", "")).strip()
    if not transcript:
        return
    tts = payload.get("tts")
    tts_engine = tts.get("engine") if isinstance(tts, dict) else payload.get("tts_engine")
    raw_voice = payload.get("voice")
    if raw_voice is None and isinstance(tts, dict):
        raw_voice = tts.get("voice")
    voice = raw_voice.strip() if isinstance(raw_voice, str) and raw_voice.strip() else None
    raw_tags = tts.get("tags") if isinstance(tts, dict) else payload.get("tts_tags")
    tags = _tags(raw_tags)
    if isinstance(tts_engine, str) and tts_engine.strip():
        manager.select_engines(session_id, tts_engine=tts_engine)
    if tags is not None:
        manager.select_engines(session_id, tts_tags=tags)
    await manager.run_text_turn(
        session_id, transcript, on_audio=send_audio, voice=voice, tags=tags
    )


def _handle_control(
    text: str,
    manager: VoiceManager,
    session_id: str,
    queue: asyncio.Queue[AudioChunk | None],
) -> bool:
    """Apply one JSON control frame. Returns ``True`` when the session should end."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    kind = payload.get("type")
    if kind == "interrupt":
        manager.barge_in(session_id)
        return False
    if kind == "utterance_end":
        queue.put_nowait(AudioChunk(data=b"", is_speech=False, end_of_utterance=True))
        return False
    if kind == "stop":
        queue.put_nowait(None)
        return True
    return False


def _session_frame(manager: VoiceManager, session: VoiceSession) -> dict[str, Any]:
    """Describe the session, including the engines it is actually using.

    The engines are the session's, not the deployment's: a client that switched
    provider needs the new engine's sample rate and media type to play the
    audio it receives (§25.1). A chained engine reports the provider it speaks
    with and the one behind it, so the client sees the provider it picked
    rather than the deployment's internal chain.
    """
    stt = manager.stt_for(session)
    tts = manager.tts_for(session)
    return {
        "type": "session",
        "session_id": session.session_id,
        "state": session.state.value,
        "sample_rate": stt.sample_rate,
        "stt": {"engine": _engine_name(stt), "available": stt.available},
        "tts": {
            "engine": _engine_name(tts),
            "fallback": _fallback_name(tts),
            "available": tts.available,
            "content_type": tts.content_type,
            "sample_rate": tts.sample_rate,
            "tags": list(session.tts_tags),
        },
    }


def _engine_name(engine: Any) -> str:
    """The provider an engine speaks with (the primary of a chain)."""
    primary = getattr(engine, "primary", None)
    return primary.name if primary is not None else engine.name


def _fallback_name(engine: Any) -> str | None:
    """The provider behind an engine, when there is one."""
    fallback = getattr(engine, "fallback", None)
    return fallback.name if fallback is not None else None


def _event_frame(frame_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Shape one bus event payload into a client frame."""
    if frame_type == "state":
        return {"type": "state", "state": payload.get("state")}
    if frame_type in {"stt_partial", "stt_final"}:
        return {"type": frame_type, "text": payload.get("text", "")}
    if frame_type == "answer":
        return {"type": "answer", "text": payload.get("text", "")}
    if frame_type == "tts_completed":
        return {"type": "tts_completed", "interrupted": bool(payload.get("interrupted"))}
    if frame_type == "ended":
        return {"type": "ended", "reason": payload.get("reason")}
    return {"type": frame_type}


def _error_frame(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, UltronError):
        return {"type": "error", "code": exc.code.value, "message": str(exc)}
    return {"type": "error", "code": "INTERNAL", "message": str(exc)}


async def _safe_send_json(
    websocket: WebSocket, payload: dict[str, Any], *, lock: asyncio.Lock | None = None
) -> None:
    """Send a JSON frame, swallowing the failure of an already-closed socket."""
    text = json.dumps(payload, ensure_ascii=False, default=str)

    async def _send() -> None:
        with contextlib.suppress(Exception):
            await websocket.send_text(text)

    if lock is None:
        await _send()
        return
    async with lock:
        await _send()


__all__ = ["VoiceEngines", "VoiceOptions", "router"]
