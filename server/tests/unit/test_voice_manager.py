"""Unit tests for the voice manager (T155, spec §25 / §59.19).

Covers session lifecycle, the STT -> Core -> Agent -> TTS pipeline, the event
stream a client follows, and barge-in (§59.19): an in-flight turn is cancelled,
its queued synthesis discarded, and the interrupted turn is *marked* rather than
dropped. Every engine is faked, so no model, network or audio is involved.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.config import VoiceSettings
from app.core.errors import NotFoundError
from app.events.bus import EventBus, Subscription
from app.events.types import EventType
from app.voice.manager import VoiceManager, VoiceReply, VoiceState
from app.voice.stt.base import STTEngine, TranscriptChunk
from app.voice.tts.base import TTSEngine


class FakeSTT(STTEngine):
    name = "fake-stt"

    def __init__(self, events: list[tuple[str, bool]]) -> None:
        self._events = events

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return ""

    async def transcribe_stream(
        self, chunks: AsyncIterator[Any], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        for text, is_final in self._events:
            yield TranscriptChunk(text=text, is_final=is_final, utterance_id=0)


class FakeTTS(TTSEngine):
    name = "fake-tts"

    def __init__(self, chunks: tuple[bytes, ...] = (b"aa", b"bb", b"cc")) -> None:
        self._chunks = chunks
        self.streamed: list[str] = []

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        return b"".join(self._chunks)

    async def synthesize_stream(
        self, text: str, *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.streamed.append(text)
        for chunk in self._chunks:
            yield chunk


def echo(text: str, *, principal: str, session_id: str, history: Any) -> str:
    return f"echo:{text}"


def _settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {"enabled": True, "session_ttl": 10, "interruption_enabled": True}
    base.update(over)
    return VoiceSettings(**base)


def _manager(
    stt: STTEngine, tts: TTSEngine, bus: EventBus | None = None, responder: Any = echo
) -> VoiceManager:
    return VoiceManager(
        stt, tts, responder=responder, bus=bus, settings=_settings()
    )


async def _drain(sub: Subscription) -> list[Any]:
    events: list[Any] = []
    while True:
        try:
            events.append(await asyncio.wait_for(sub.get(), timeout=0.05))
        except TimeoutError:
            return events


# --------------------------------------------------------------------------- #
# Session lifecycle.
# --------------------------------------------------------------------------- #
def test_create_and_get_session() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    session = manager.create_session(principal="alice")
    assert manager.get(session.session_id) is session
    assert session.principal == "alice"
    assert session.state is VoiceState.LISTENING
    assert manager.list_sessions() == [session]


def test_end_session_removes_it() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    session = manager.create_session()
    assert manager.end_session(session.session_id) is True
    assert manager.get(session.session_id) is None


def test_end_unknown_session_is_false() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    assert manager.end_session("nope") is False


def test_sweep_closes_idle_sessions() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    session = manager.create_session()
    session.last_active -= 1000  # far past the TTL
    closed = manager.sweep()
    assert closed == [session.session_id]
    assert manager.get(session.session_id) is None


async def test_unknown_session_raises_not_found() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    with pytest.raises(NotFoundError):
        await manager.run_turn("nope", "hello")


# --------------------------------------------------------------------------- #
# The pipeline (§25): think then speak, with events a client can follow.
# --------------------------------------------------------------------------- #
async def test_run_turn_happy_path() -> None:
    bus = EventBus()
    manager = _manager(FakeSTT([]), FakeTTS(), bus)
    session = manager.create_session(principal="alice")
    await manager.flush()  # let the create-session event land before subscribing
    sub = bus.subscribe()
    try:
        reply = await manager.run_turn(session.session_id, "hello")
        await manager.flush()
        events = [e.event_type.value for e in await _drain(sub)]
    finally:
        sub.close()
        bus.close()

    assert reply.text == "echo:hello"
    assert isinstance(reply, VoiceReply)
    assert reply.audio == b"aabbcc"
    assert reply.interrupted is False
    # History records both sides.
    assert [(t.role, t.text) for t in session.history] == [
        ("user", "hello"),
        ("assistant", "echo:hello"),
    ]
    # State machine: think -> speak -> listen, bracketed by the TTS events.
    expected = [
        EventType.VOICE_THINKING.value,
        EventType.VOICE_SPEAKING.value,
        EventType.TTS_STARTED.value,
        EventType.TTS_COMPLETED.value,
        EventType.VOICE_LISTENING.value,
    ]
    filtered = [e for e in events if e in set(expected)]
    assert filtered == expected


async def test_run_turn_streams_audio_to_sink() -> None:
    manager = _manager(FakeSTT([]), FakeTTS((b"1", b"2")))
    session = manager.create_session()
    received: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    reply = await manager.run_turn(session.session_id, "hi", on_audio=sink)
    assert received == [b"1", b"2"]
    assert reply.chunks == (b"1", b"2")


async def test_handle_audio_runs_a_turn_per_final() -> None:
    bus = EventBus()
    stt = FakeSTT([("play ", False), ("play music", True)])
    manager = _manager(stt, FakeTTS(), bus)
    session = manager.create_session()
    await manager.flush()
    sub = bus.subscribe()

    async def _chunks() -> AsyncIterator[Any]:
        yield b"audio"

    try:
        replies = await manager.handle_audio(session.session_id, _chunks())
        await manager.flush()
        events = [e.event_type.value for e in await _drain(sub)]
    finally:
        sub.close()
        bus.close()

    assert [r.text for r in replies] == ["echo:play music"]
    assert EventType.STT_PARTIAL.value in events
    assert EventType.STT_FINAL.value in events


# --------------------------------------------------------------------------- #
# Barge-in (§59.19).
# --------------------------------------------------------------------------- #
async def test_barge_in_cancels_the_turn_and_marks_it() -> None:
    manager = _manager(FakeSTT([]), FakeTTS((b"1", b"2", b"3")))
    session = manager.create_session()

    async def sink(chunk: bytes) -> None:
        # On the first delivered chunk, the user starts talking again.
        if chunk == b"1":
            await manager.interrupt(session.session_id, reason="barge-in")

    reply = await manager.run_turn(session.session_id, "tell me a story", on_audio=sink)

    assert reply.interrupted is True
    # The synthesis stops at the interruption; the rest is discarded.
    assert reply.chunks == (b"1",)
    assert reply.audio == b"1"
    # The interrupted assistant turn is marked, not dropped.
    assert session.history[-1].role == "assistant"
    assert session.history[-1].interrupted is True
    # Back to listening.
    assert session.state is VoiceState.LISTENING


async def test_interrupt_does_nothing_without_a_turn() -> None:
    manager = _manager(FakeSTT([]), FakeTTS())
    session = manager.create_session()
    # No in-flight turn: interrupt just sets the flag and returns True.
    assert await manager.interrupt(session.session_id) is True
    assert session.interrupt.is_set() is True


async def test_handle_audio_forwards_audio_to_sink() -> None:
    """``on_audio`` reaches ``run_turn``, so a transport can stream playback."""
    manager = _manager(FakeSTT([("hi", True)]), FakeTTS((b"x", b"y")))
    session = manager.create_session()
    received: list[bytes] = []

    async def _chunks() -> AsyncIterator[Any]:
        yield b"audio"

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    replies = await manager.handle_audio(session.session_id, _chunks(), on_audio=sink)
    assert [r.text for r in replies] == ["echo:hi"]
    assert received == [b"x", b"y"]


def test_barge_in_sets_the_flag_without_cancelling() -> None:
    """``barge_in`` stops the audio loop but leaves the consuming loop alive.

    Where :meth:`interrupt` cancels the pipeline task (ending a finite upload),
    ``barge_in`` only raises the flag so a long-lived socket keeps listening.
    """
    manager = _manager(FakeSTT([]), FakeTTS())
    session = manager.create_session()
    assert manager.barge_in(session.session_id) is True
    assert session.interrupt.is_set() is True
    assert manager.barge_in("missing") is False


async def test_barge_in_ends_the_turn_but_handle_audio_continues() -> None:
    """A barge-in during synthesis marks one turn; the next utterance still runs."""
    bus = EventBus()
    stt = FakeSTT([("first", True), ("second", True)])
    tts = FakeTTS((b"1", b"2", b"3"))
    manager = _manager(stt, tts, bus)
    session = manager.create_session()

    replies: list[VoiceReply] = []
    barged = {"done": False}

    async def _chunks() -> AsyncIterator[Any]:
        yield b"audio"

    async def sink(chunk: bytes) -> None:
        # Talk over the first answer only.
        if chunk == b"1" and not barged["done"]:
            barged["done"] = True
            manager.barge_in(session.session_id)

    # Drain the two utterances the fake STT yields; capture replies as they land.
    collected: list[VoiceReply] = []

    async def run() -> None:
        collected.extend(
            await manager.handle_audio(session.session_id, _chunks(), on_audio=sink)
        )

    await run()
    replies.extend(collected)
    assert [r.text for r in replies] == ["echo:first", "echo:second"]
    assert replies[0].interrupted is True
    assert replies[1].interrupted is False
    assert session.state is VoiceState.LISTENING
    bus.close()

