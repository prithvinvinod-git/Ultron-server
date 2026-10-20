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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    STATIC_PRINCIPAL_ID,
    STATIC_PRINCIPAL_USERNAME,
    Container,
)
from app.core.errors import UltronError
from app.events.bus import Subscription
from app.events.types import EventType
from app.observability import get_logger
from app.security.audit import ACTOR_USER
from app.security.authentication import Principal
from app.voice.manager import VoiceManager, VoiceSession, VoiceState
from app.voice.stt.base import AudioChunk

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
    talking, so close the turn now) and ``{"type":"stop"}`` (end the session).
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
        await _pump(websocket, manager, session_id, queue)
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
        if _handle_control(text, manager, session_id, queue):
            return


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
    return {
        "type": "session",
        "session_id": session.session_id,
        "state": session.state.value,
        "sample_rate": manager.stt.sample_rate,
        "stt": {"engine": manager.stt.name, "available": manager.stt.available},
        "tts": {
            "engine": manager.tts.name,
            "available": manager.tts.available,
            "content_type": manager.tts.content_type,
            "sample_rate": manager.tts.sample_rate,
        },
    }


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


__all__ = ["VoiceEngines", "router"]
