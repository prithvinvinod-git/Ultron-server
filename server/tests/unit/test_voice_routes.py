"""Route tests for the voice API (T154, spec §25 / §59.19).

Two surfaces are pinned here:

- ``GET /voice/engines`` -- the honest availability report the front door reads
  before offering a microphone button (§64.11).
- ``WS /voice/ws`` -- the realtime wire shape: audio frames up, ``state`` /
  ``stt_*`` / ``answer`` / ``tts_completed`` JSON frames and raw audio down, with
  barge-in over the live socket.

The container is stubbed (no PostgreSQL/Redis) and the engines are fakes, so no
model, network or audio device is touched. Everything between -- the manager, the
event bus, the socket plumbing -- is real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import VoiceSettings, reload_settings
from app.events.bus import EventBus
from app.main import create_app
from app.voice.manager import VoiceManager
from app.voice.stt.base import STTEngine, TranscriptChunk
from app.voice.tts.base import TTSEngine

pytestmark = pytest.mark.unit


class StreamingSTT(STTEngine):
    """Emits a partial then a final for every audio chunk it is handed."""

    name = "fake-stt"
    sample_rate = 16000

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return "hello"

    async def transcribe_stream(
        self, chunks: AsyncIterator[Any], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        async for _chunk in chunks:
            yield TranscriptChunk(text="hel", is_final=False)
            yield TranscriptChunk(text="hello", is_final=True)


class BoundarySTT(STTEngine):
    """Accumulates speech and finalizes only on an end-of-utterance marker.

    Mimics the real engines' §59.17 policy: a turn does not end just because
    audio arrived, only when the transport says the user stopped talking.
    """

    name = "boundary-stt"
    sample_rate = 16000

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return "hello"

    async def transcribe_stream(
        self, chunks: AsyncIterator[Any], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        buffer = b""
        async for chunk in chunks:
            if chunk.end_of_utterance:
                if buffer:
                    yield TranscriptChunk(text=buffer.decode(), is_final=True)
                    buffer = b""
                continue
            if chunk.is_speech and chunk.data:
                buffer += chunk.data
                yield TranscriptChunk(text=buffer.decode(), is_final=False)


class ToneTTS(TTSEngine):
    """Streams two fixed PCM chunks."""

    name = "fake-tts"
    sample_rate = 22050
    content_type = "audio/L16"

    def __init__(self) -> None:
        self.tags_seen: list[tuple[str, ...]] = []
        self.voices_seen: list[str | None] = []

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        self.tags_seen.append(tuple(tags or ()))
        self.voices_seen.append(voice)
        return b"\x01\x02\x03\x04"

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[bytes]:
        self.tags_seen.append(tuple(tags or ()))
        self.voices_seen.append(voice)
        yield b"\x01\x02"
        yield b"\x03\x04"


async def _respond(text: str, *, principal: str, session_id: str, history: Any) -> str:
    return f"you said {text}"


def _settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {"enabled": True, "session_ttl": 10, "interruption_enabled": True}
    base.update(over)
    return VoiceSettings(**base)


class _StubContainer:
    """The container slice the voice routes reach for."""

    def __init__(
        self,
        manager: VoiceManager,
        bus: EventBus,
        *,
        allow_anonymous: bool,
        voice_settings: VoiceSettings | None = None,
    ) -> None:
        self._manager = manager
        self._bus = bus
        self.settings = SimpleNamespace(
            voice=voice_settings or VoiceSettings(enabled=True),
            security=SimpleNamespace(allow_anonymous=allow_anonymous),
        )

    @property
    def voice(self) -> VoiceManager:
        return self._manager

    @property
    def events(self) -> EventBus:
        return self._bus


@asynccontextmanager
async def _no_lifespan(app: Any) -> AsyncIterator[None]:
    """Skip the real lifespan (PostgreSQL/Redis pings) but keep the ASGI portal."""
    yield


def _build_app(container: _StubContainer) -> Any:
    app = create_app()
    app.router.lifespan_context = _no_lifespan
    app.state.container = container
    return app


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    bus = EventBus()
    manager = VoiceManager(
        StreamingSTT(), ToneTTS(), responder=_respond, bus=bus, settings=_settings()
    )
    container = _StubContainer(manager, bus, allow_anonymous=True)
    app = _build_app(container)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class NamedTTS(ToneTTS):
    """A TTS engine that reports the provider name it stands for."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


@pytest.fixture
def selectable_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An API whose deployment runs Fish Audio and can build other providers."""
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    bus = EventBus()
    voice_settings = _settings(
        tts_engine="fish-audio",
        tts_fallback_engine="edge-tts",
        stt_engine="faster-whisper",
        stt_fallback_engine="groq",
        fish_audio_api_key="sk-fish-test",
        fish_audio_voice_id="ref-cloned-1",
    )
    manager = VoiceManager(
        StreamingSTT(),
        NamedTTS("fish-audio"),
        responder=_respond,
        bus=bus,
        settings=voice_settings,
        tts_factory=lambda name: NamedTTS(name),
    )
    container = _StubContainer(
        manager, bus, allow_anonymous=True, voice_settings=voice_settings
    )
    app = _build_app(container)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_engines_reports_the_configured_stack(api: TestClient) -> None:
    response = api.get("/voice/engines")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["stt"] == {
        "engine": "fake-stt",
        "available": True,
        "reason": None,
        "sample_rate": 16000,
        "content_type": None,
    }
    assert body["tts"]["engine"] == "fake-tts"
    assert body["tts"]["content_type"] == "audio/L16"
    assert body["tts"]["sample_rate"] == 22050


def _collect_until(ws: Any, stop_type: str, limit: int = 50) -> list[tuple[str, Any]]:
    """Read frames until the JSON frame of ``stop_type`` arrives (or fail)."""
    frames: list[tuple[str, Any]] = []
    for _ in range(limit):
        message = ws.receive()
        if message.get("bytes") is not None:
            frames.append(("bytes", message["bytes"]))
            continue
        payload = json.loads(message["text"])
        frames.append(("json", payload))
        if payload.get("type") == stop_type:
            return frames
    raise AssertionError(f"never saw a {stop_type!r} frame: {frames}")


def test_ws_streams_stt_answer_and_audio(api: TestClient) -> None:
    with api.websocket_connect("/voice/ws") as ws:
        session = ws.receive_json()
        assert session["type"] == "session"
        assert session["stt"]["engine"] == "fake-stt"
        assert session["tts"]["content_type"] == "audio/L16"
        assert session["sample_rate"] == 16000

        ws.send_bytes(b"raw-audio")
        frames = _collect_until(ws, "tts_completed")

    json_frames = [payload for kind, payload in frames if kind == "json"]
    types = [payload["type"] for payload in json_frames]
    assert "stt_partial" in types
    assert "stt_final" in types
    assert "answer" in types
    assert "tts_completed" in types

    partial = next(p for p in json_frames if p["type"] == "stt_partial")
    assert partial["text"] == "hel"
    final = next(p for p in json_frames if p["type"] == "stt_final")
    assert final["text"] == "hello"
    answer = next(p for p in json_frames if p["type"] == "answer")
    assert answer["text"] == "you said hello"

    audio = [payload for kind, payload in frames if kind == "bytes"]
    assert audio == [b"\x01\x02", b"\x03\x04"]


def test_ws_rejects_a_bad_control_frame(api: TestClient) -> None:
    """A malformed control frame is ignored; the session still answers audio."""
    with api.websocket_connect("/voice/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_bytes(b"raw-audio")
        frames = _collect_until(ws, "tts_completed")

    assert any(
        payload.get("type") == "answer" for kind, payload in frames if kind == "json"
    )


def test_ws_opens_even_when_anonymous_is_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built-in auth is removed, so the socket no longer gates on a token.

    ``security.allow_anonymous`` used to make the route refuse a socket with no
    credential (an ``AUTH_ERROR`` frame). With auth delegated to an external
    provider, every socket is attributed to the static principal and opens; the
    setting is dormant until a verifier is wired back in. This asserts the new
    contract: a session frame, never an auth error.
    """
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    manager = VoiceManager(StreamingSTT(), ToneTTS(), responder=_respond, settings=_settings())
    container = _StubContainer(manager, EventBus(), allow_anonymous=False)
    app = _build_app(container)
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        client.websocket_connect("/voice/ws") as ws,
    ):
        frame = ws.receive_json()
        assert frame["type"] == "session"
        assert frame["type"] != "error"


def test_ws_utterance_end_closes_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client-VAD end frame finalizes the utterance and runs the turn."""
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    bus = EventBus()
    manager = VoiceManager(
        BoundarySTT(), ToneTTS(), responder=_respond, bus=bus, settings=_settings()
    )
    container = _StubContainer(manager, bus, allow_anonymous=True)
    app = _build_app(container)

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        client.websocket_connect("/voice/ws") as ws,
    ):
        ws.receive_json()  # session
        ws.send_bytes(b"hello")
        frames = _collect_until(ws, "stt_partial")
        partial = next(p for kind, p in frames if kind == "json" and p["type"] == "stt_partial")
        assert partial["text"] == "hello"

        ws.send_text(json.dumps({"type": "utterance_end"}))
        frames = _collect_until(ws, "tts_completed")

    json_frames = [payload for kind, payload in frames if kind == "json"]
    final = next(p for p in json_frames if p["type"] == "stt_final")
    assert final["text"] == "hello"
    answer = next(p for p in json_frames if p["type"] == "answer")
    assert answer["text"] == "you said hello"


# --------------------------------------------------------------------------- #
# Provider selection (§25.1): the client picks a provider the deployment does
# not run as its default, and the server honours the pick.
# --------------------------------------------------------------------------- #
def test_options_lists_every_provider_with_its_availability(
    selectable_api: TestClient,
) -> None:
    response = selectable_api.get("/voice/options")
    assert response.status_code == 200
    body = response.json()

    engines = [provider["engine"] for provider in body["tts"]]
    # The configured provider comes first, then the rest are still offered.
    assert engines[0] == "fish-audio"
    assert set(engines) >= {"fish-audio", "edge-tts", "elevenlabs", "piper"}

    fish = next(p for p in body["tts"] if p["engine"] == "fish-audio")
    assert fish["available"] is True
    # The cloned voice is offered as that provider's voice, so it can be picked
    # without retyping its id.
    assert fish["default_voice"] == "ref-cloned-1"
    assert [voice["id"] for voice in fish["voices"]] == ["ref-cloned-1"]

    stt_engines = [provider["engine"] for provider in body["stt"]]
    assert stt_engines[0] == "faster-whisper"
    assert set(stt_engines) >= {"faster-whisper", "groq", "browser"}
    browser = next(p for p in body["stt"] if p["engine"] == "browser")
    assert browser["available"] is True


def test_options_reports_a_provider_that_cannot_run(selectable_api: TestClient) -> None:
    body = selectable_api.get("/voice/options").json()

    eleven = next(p for p in body["tts"] if p["engine"] == "elevenlabs")
    assert eleven["available"] is False
    assert "ELEVENLABS_API_KEY" in (eleven["reason"] or "")

    groq = next(p for p in body["stt"] if p["engine"] == "groq")
    assert groq["available"] is False
    assert groq["reason"]


def test_tts_route_honours_the_named_provider(selectable_api: TestClient) -> None:
    response = selectable_api.post("/voice/tts", json={"text": "hello", "engine": "edge-tts"})
    assert response.status_code == 200
    assert response.headers["X-TTS-Engine"].startswith("edge-tts")
    assert response.content == b"\x01\x02\x03\x04"


def test_tts_route_reports_a_provider_that_cannot_run(selectable_api: TestClient) -> None:
    response = selectable_api.post("/voice/tts", json={"text": "hello", "engine": "elevenlabs"})
    assert response.status_code == 503


def test_ws_config_frame_switches_the_session_provider(
    selectable_api: TestClient,
) -> None:
    """A ``config`` frame names the providers the client wants for this session."""
    with selectable_api.websocket_connect("/voice/ws") as ws:
        session = ws.receive_json()
        assert session["type"] == "session"
        assert session["tts"]["engine"] == "fish-audio"

        ws.send_text(
            json.dumps(
                {"type": "config", "tts": {"engine": "edge-tts", "voice": "ref-cloned-1"}}
            )
        )
        # The session is reported back, so the client picks up the new engine's
        # rate and media type before its next turn.
        frame = next(
            payload
            for kind, payload in _collect_until(ws, "session", limit=10)
            if kind == "json" and payload.get("type") == "session"
        )

    assert frame["tts"]["engine"] == "edge-tts"
    # The deployment's own engine stays behind it as the degraded path.
    assert frame["tts"]["fallback"] == "fish-audio"


def test_ws_text_turn_can_name_a_provider(selectable_api: TestClient) -> None:
    """A browser-STT turn may name the provider to speak with."""
    with selectable_api.websocket_connect("/voice/ws") as ws:
        ws.receive_json()
        ws.send_text(
            json.dumps(
                {
                    "type": "text",
                    "text": "hello there",
                    "tts": {"engine": "edge-tts", "voice": "ref-cloned-1"},
                }
            )
        )
        frames = _collect_until(ws, "tts_completed")

    json_frames = [payload for kind, payload in frames if kind == "json"]
    answer = next(p for p in json_frames if p["type"] == "answer")
    assert answer["text"] == "you said hello there"


# --------------------------------------------------------------------------- #
# Delivery cues: emotion tags travel with the request, not with the deployment.
# --------------------------------------------------------------------------- #
@pytest.fixture
def recording_api(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, ToneTTS]]:
    """An API whose TTS engine records the cues and voices it was handed."""
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    bus = EventBus()
    tts = ToneTTS()
    manager = VoiceManager(
        StreamingSTT(), tts, responder=_respond, bus=bus, settings=_settings()
    )
    container = _StubContainer(manager, bus, allow_anonymous=True)
    app = _build_app(container)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, tts


def test_tts_route_passes_the_delivery_cues(
    recording_api: tuple[TestClient, ToneTTS],
) -> None:
    client, tts = recording_api
    response = client.post(
        "/voice/tts", json={"text": "hello", "tags": ["calm", "friendly"]}
    )

    assert response.status_code == 200
    assert tts.tags_seen[-1] == ("calm", "friendly")


def test_tts_route_accepts_a_comma_separated_cue_string(
    recording_api: tuple[TestClient, ToneTTS],
) -> None:
    client, tts = recording_api
    client.post("/voice/tts", json={"text": "hello", "tags": "calm, excited"})

    assert tts.tags_seen[-1] == ("calm", "excited")


def test_ws_config_frame_records_the_delivery_cues(
    recording_api: tuple[TestClient, ToneTTS],
) -> None:
    client, tts = recording_api
    with client.websocket_connect("/voice/ws") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "config", "tts": {"tags": ["calm"]}}))
        frame = next(
            payload
            for kind, payload in _collect_until(ws, "session", limit=10)
            if kind == "json" and payload.get("type") == "session"
        )

        # The cues are persisted for the session: a turn that names none still
        # speaks with them.
        ws.send_text(json.dumps({"type": "text", "text": "hello"}))
        _collect_until(ws, "tts_completed")

    assert frame["tts"]["tags"] == ["calm"]
    assert tts.tags_seen[-1] == ("calm",)


def test_ws_text_turn_can_name_the_delivery_cues(
    recording_api: tuple[TestClient, ToneTTS],
) -> None:
    client, tts = recording_api
    with client.websocket_connect("/voice/ws") as ws:
        ws.receive_json()
        ws.send_text(
            json.dumps(
                {"type": "text", "text": "hello", "tts": {"tags": ["angry"]}}
            )
        )
        _collect_until(ws, "tts_completed")

    assert tts.tags_seen[-1] == ("angry",)
