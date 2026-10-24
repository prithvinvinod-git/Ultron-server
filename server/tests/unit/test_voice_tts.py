"""Unit tests for the text-to-speech subsystem (T155, spec §59.18).

Covers sentence-level segmentation, the null engine's honest degradation, the
Piper adapter against an injected runner (no binary, no model), and the factory --
none of which need Piper installed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
import respx

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, UltronError
from app.voice.fallback import FallbackTTSEngine
from app.voice.tts import (
    EdgeTTSTTS,
    ElevenLabsTTS,
    FishAudioTTS,
    NullTTSEngine,
    PiperTTS,
    TTSEngine,
    create_tts_engine,
    edge_tts_available,
    split_sentences,
)


def _settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "tts_engine": "piper",
        "tts_model_path": "voice.onnx",
        "tts_binary_path": "piper",
        "tts_sample_rate": 22050,
    }
    base.update(over)
    return VoiceSettings(**base)


class RecordingRunner:
    """An injected Piper runner that returns fixed audio and records its argv."""

    def __init__(self, audio: bytes = b"pcm") -> None:
        self.audio = audio
        self.calls: list[tuple[list[str], str, float]] = []

    async def __call__(self, argv: Sequence[str], text: str, limit: float) -> bytes:
        self.calls.append((list(argv), text, limit))
        return self.audio


async def _collect(stream: Any) -> list[bytes]:
    return [chunk async for chunk in stream]


# --------------------------------------------------------------------------- #
# Sentence segmentation (§59.18).
# --------------------------------------------------------------------------- #
def test_split_sentences_splits_on_terminators() -> None:
    assert split_sentences("Hello there. How are you? Fine!") == [
        "Hello there.",
        "How are you?",
        "Fine!",
    ]


def test_split_sentences_handles_newlines_and_whitespace() -> None:
    assert split_sentences("  line one \n line two  ") == ["line one", "line two"]


def test_split_sentences_empty_input() -> None:
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


# --------------------------------------------------------------------------- #
# Null engine degrades honestly (§59.18, §64.11).
# --------------------------------------------------------------------------- #
def test_null_engine_is_unavailable() -> None:
    engine = NullTTSEngine(reason="no tts")
    assert engine.available is False
    assert engine.unavailable_reason == "no tts"


async def test_null_engine_synthesize_raises() -> None:
    engine = NullTTSEngine(reason="no tts")
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# Piper adapter, against an injected runner (§59.18).
# --------------------------------------------------------------------------- #
async def test_synthesize_returns_runner_audio() -> None:
    runner = RecordingRunner(b"\x01\x02")
    engine = PiperTTS(_settings(), runner=runner)
    audio = await engine.synthesize("hello")
    assert audio == b"\x01\x02"
    assert runner.calls[0][1] == "hello"


def test_piper_argv_is_a_literal_command() -> None:
    engine = PiperTTS(_settings())
    assert engine._argv(None) == ["piper", "--model", "voice.onnx", "--output_raw"]
    # A per-call voice override replaces the model, never the shell.
    assert engine._argv("other.onnx")[2] == "other.onnx"


async def test_synthesize_stream_is_sentence_level() -> None:
    runner = RecordingRunner()
    engine = PiperTTS(_settings(), runner=runner)
    chunks = await _collect(engine.synthesize_stream("One. Two three."))
    assert len(chunks) == 2
    assert [call[1] for call in runner.calls] == ["One.", "Two three."]


async def test_empty_text_synthesises_nothing() -> None:
    runner = RecordingRunner()
    engine = PiperTTS(_settings(), runner=runner)
    assert await engine.synthesize("   ") == b""
    assert runner.calls == []


async def test_empty_audio_from_runner_is_an_error() -> None:
    engine = PiperTTS(_settings(), runner=RecordingRunner(b""))
    with pytest.raises(UltronError):
        await engine.synthesize("hello")


def test_available_with_runner_is_true() -> None:
    engine = PiperTTS(_settings(), runner=RecordingRunner())
    assert engine.available is True
    assert engine.unavailable_reason is None


def test_unavailable_without_model_or_binary() -> None:
    # No model configured.
    no_model = PiperTTS(_settings(tts_model_path=""))
    assert no_model.available is False
    assert "TTS_MODEL_PATH" in (no_model.unavailable_reason or "")

    # Model present but the executable is not resolvable.
    no_binary = PiperTTS(_settings(tts_binary_path="piper-not-installed-xyz"))
    assert no_binary.available is False
    assert no_binary.unavailable_reason is not None


async def test_unavailable_engine_raises_on_synthesize() -> None:
    engine = PiperTTS(_settings(tts_model_path=""))
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# Factory (§59.18).
# --------------------------------------------------------------------------- #
def test_factory_returns_null_when_disabled() -> None:
    assert isinstance(create_tts_engine(VoiceSettings(enabled=False)), NullTTSEngine)


def test_factory_returns_null_for_none_engine() -> None:
    assert isinstance(
        create_tts_engine(VoiceSettings(enabled=True, tts_engine="none")), NullTTSEngine
    )


def test_factory_returns_piper() -> None:
    engine = create_tts_engine(_settings())
    assert isinstance(engine, PiperTTS)
    assert isinstance(engine, TTSEngine)


def test_factory_returns_elevenlabs() -> None:
    engine = create_tts_engine(
        _settings(tts_engine="elevenlabs", elevenlabs_api_key="sk_test")
    )
    assert isinstance(engine, ElevenLabsTTS)
    assert engine.available is True


def test_factory_returns_edge_tts() -> None:
    engine = create_tts_engine(_settings(tts_engine="edge-tts"))
    assert isinstance(engine, EdgeTTSTTS)


def test_factory_chains_a_configured_fallback() -> None:
    engine = create_tts_engine(
        _settings(tts_engine="elevenlabs", tts_fallback_engine="edge-tts")
    )
    assert isinstance(engine, FallbackTTSEngine)
    assert engine.name == "elevenlabs>edge-tts"


# --------------------------------------------------------------------------- #
# ElevenLabs cloud adapter (§59.18), against an injected synthesizer.
# --------------------------------------------------------------------------- #
class RecordingSynthesizer:
    """Returns fixed audio and records the text/voice it was handed."""

    def __init__(self, audio: bytes = b"mp3") -> None:
        self.audio = audio
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, text: str, voice: str | None) -> bytes:
        self.calls.append((text, voice))
        return self.audio


def test_elevenlabs_maps_pcm_output_format_to_raw_pcm() -> None:
    engine = ElevenLabsTTS(
        _settings(tts_engine="elevenlabs", elevenlabs_output_format="pcm_22050")
    )
    assert engine.content_type == "audio/L16"
    assert engine.sample_rate == 22050


def test_elevenlabs_maps_mp3_output_format_to_a_container() -> None:
    engine = ElevenLabsTTS(
        _settings(
            tts_engine="elevenlabs",
            elevenlabs_output_format="mp3_44100_128",
            tts_sample_rate=16000,
        )
    )
    assert engine.content_type == "audio/mpeg"
    assert engine.sample_rate == 16000


def test_elevenlabs_availability_is_the_key_presence() -> None:
    missing = ElevenLabsTTS(_settings(tts_engine="elevenlabs", elevenlabs_api_key=""))
    assert missing.available is False
    assert "ELEVENLABS_API_KEY" in (missing.unavailable_reason or "")

    configured = ElevenLabsTTS(
        _settings(tts_engine="elevenlabs", elevenlabs_api_key="sk_secret")
    )
    assert configured.available is True


async def test_elevenlabs_synthesize_uses_the_injected_synthesizer() -> None:
    synth = RecordingSynthesizer(b"\x01\x02")
    engine = ElevenLabsTTS(_settings(tts_engine="elevenlabs"), synthesizer=synth)
    assert await engine.synthesize("hello", voice="custom-voice") == b"\x01\x02"
    assert synth.calls == [("hello", "custom-voice")]


async def test_elevenlabs_stream_is_sentence_level() -> None:
    synth = RecordingSynthesizer()
    engine = ElevenLabsTTS(_settings(tts_engine="elevenlabs"), synthesizer=synth)
    chunks = await _collect(engine.synthesize_stream("One. Two three."))
    assert len(chunks) == 2
    assert [call[0] for call in synth.calls] == ["One.", "Two three."]


async def test_elevenlabs_unavailable_synthesize_raises() -> None:
    engine = ElevenLabsTTS(_settings(tts_engine="elevenlabs", elevenlabs_api_key=""))
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# edge-tts local adapter (§59.18), against an injected synthesizer.
# --------------------------------------------------------------------------- #
def test_edge_tts_declares_mp3_and_24khz() -> None:
    engine = EdgeTTSTTS(_settings(tts_engine="edge-tts"), synthesizer=RecordingSynthesizer())
    assert engine.content_type == "audio/mpeg"
    assert engine.sample_rate == 24000
    assert engine.available is True


def test_edge_tts_availability_follows_the_package_without_an_injected_synth() -> None:
    engine = EdgeTTSTTS(_settings(tts_engine="edge-tts"))
    assert engine.available is edge_tts_available()
    if not engine.available:
        assert engine.unavailable_reason is not None


async def test_edge_tts_synthesize_returns_audio() -> None:
    synth = RecordingSynthesizer(b"mp3bytes")
    engine = EdgeTTSTTS(_settings(tts_engine="edge-tts"), synthesizer=synth)
    assert await engine.synthesize("hello") == b"mp3bytes"


async def test_edge_tts_unavailable_synthesize_raises() -> None:
    if edge_tts_available():  # pragma: no cover - only when the package is installed
        pytest.skip("edge-tts is installed; nothing to degrade")
    engine = EdgeTTSTTS(_settings(tts_engine="edge-tts"))
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# Fallback chaining (§59.18): cloud first, local behind it.
# --------------------------------------------------------------------------- #
class FakeTTS(TTSEngine):
    def __init__(
        self,
        name: str,
        *,
        audio: bytes = b"",
        available: bool = True,
        reason: str | None = None,
        fail: bool = False,
    ) -> None:
        self.name = name
        self._audio = audio
        self._available = available
        self._reason = reason
        self._fail = fail

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        if self._fail:
            raise DependencyUnavailableError("tts", reason="primary broke")
        return self._audio


async def test_tts_fallback_uses_primary_when_available() -> None:
    engine = FallbackTTSEngine(
        FakeTTS("cloud", audio=b"cloud"), FakeTTS("local", audio=b"local")
    )
    assert engine.name == "cloud>local"
    assert await engine.synthesize("hi") == b"cloud"


async def test_tts_fallback_uses_the_local_engine_when_the_cloud_is_unavailable() -> None:
    engine = FallbackTTSEngine(
        FakeTTS("cloud", available=False, reason="no key"),
        FakeTTS("local", audio=b"local"),
    )
    assert engine.available is True
    assert await engine.synthesize("hi") == b"local"


async def test_tts_fallback_retries_the_secondary_when_the_primary_raises() -> None:
    engine = FallbackTTSEngine(
        FakeTTS("cloud", fail=True), FakeTTS("local", audio=b"local picked up")
    )
    assert await engine.synthesize("hi") == b"local picked up"


def test_tts_fallback_reports_unavailable_only_when_both_are() -> None:
    engine = FallbackTTSEngine(
        FakeTTS("cloud", available=False, reason="no key"),
        FakeTTS("local", available=False, reason="not installed"),
    )
    assert engine.available is False
    assert "no key" in (engine.unavailable_reason or "")
    assert "not installed" in (engine.unavailable_reason or "")


# --------------------------------------------------------------------------- #
# Fish Audio cloud adapter (§59.18): the cloned voice, over HTTP.
# --------------------------------------------------------------------------- #
_TTS_URL = "https://api.fish.audio/v1/tts"


def _fish_settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "tts_engine": "fish-audio",
        "fish_audio_api_key": "sk-fish-test",
        "fish_audio_voice_id": "ref-cloned-1",
        "fish_audio_output_format": "mp3",
    }
    base.update(over)
    return VoiceSettings(**base)


@respx.mock
async def test_fish_audio_synthesize_posts_the_reference_id() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3bytes"))
    engine = FishAudioTTS(_fish_settings())

    assert await engine.synthesize("hello") == b"mp3bytes"

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "hello"
    assert body["reference_id"] == "ref-cloned-1"
    assert body["format"] == "mp3"
    # The model id is optional: Fish Audio picks one, so nothing is sent unless
    # the deployment named a model.
    assert "model_id" not in body


@respx.mock
async def test_fish_audio_voice_override_names_another_clone() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    await engine.synthesize("hello", voice="ref-other")

    body = json.loads(route.calls[0].request.content)
    assert body["reference_id"] == "ref-other"


@respx.mock
async def test_fish_audio_renders_caller_tags_as_brackets() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    await engine.synthesize("hello there", tags=["calm"])

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "[calm] hello there"


@respx.mock
async def test_fish_audio_joins_several_tags_into_one_marker_block() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    await engine.synthesize("hello", tags=["sad", "[whispering]"])

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "[sad][whispering] hello"


@respx.mock
async def test_fish_audio_uses_the_deployment_tags_by_default() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings(fish_audio_tags="calm, friendly"))

    await engine.synthesize("hello")

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "[calm][friendly] hello"


@respx.mock
async def test_fish_audio_renders_the_legacy_paren_style() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings(fish_audio_tag_style="paren"))

    await engine.synthesize("hello", tags=["calm"])

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "(calm) hello"


@respx.mock
async def test_fish_audio_leaves_text_that_already_starts_with_a_marker() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    await engine.synthesize("[angry] hello", tags=["calm"])

    body = json.loads(route.calls[0].request.content)
    # The caller wrote the cue; prefixing another one would fight it.
    assert body["text"] == "[angry] hello"


@respx.mock
async def test_fish_audio_an_empty_tag_list_cancels_the_deployment_default() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings(fish_audio_tags="calm"))

    await engine.synthesize("hello", tags=[])

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "hello"


@respx.mock
async def test_fish_audio_tags_go_to_every_sentence() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    async for _ in engine.synthesize_stream("One. Two.", tags=["calm"]):
        pass

    texts = [json.loads(call.request.content)["text"] for call in route.calls]
    assert texts == ["[calm] One.", "[calm] Two."]


@respx.mock
async def test_fish_audio_accepts_preexisting_marker_spellings() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings())

    await engine.synthesize("hello", tags=["[Calm]", "(excited)"])

    body = json.loads(route.calls[0].request.content)
    assert body["text"] == "[calm][excited] hello"


@respx.mock
async def test_fish_audio_sends_the_model_id_when_configured() -> None:
    route = respx.post(_TTS_URL).mock(return_value=httpx.Response(200, content=b"mp3"))
    engine = FishAudioTTS(_fish_settings(fish_audio_model="speech-1.5"))

    await engine.synthesize("hello")

    body = json.loads(route.calls[0].request.content)
    assert body["model_id"] == "speech-1.5"


@respx.mock
async def test_fish_audio_402_says_the_credit_is_exhausted() -> None:
    respx.post(_TTS_URL).mock(
        return_value=httpx.Response(402, json={"message": "Insufficient API credit."})
    )
    engine = FishAudioTTS(_fish_settings())

    with pytest.raises(DependencyUnavailableError) as excinfo:
        await engine.synthesize("hello")

    assert "credit" in str(excinfo.value).lower()


@respx.mock
async def test_fish_audio_rejects_a_key_the_provider_refuses() -> None:
    respx.post(_TTS_URL).mock(return_value=httpx.Response(401, json={"message": "nope"}))
    engine = FishAudioTTS(_fish_settings())

    with pytest.raises(DependencyUnavailableError) as excinfo:
        await engine.synthesize("hello")

    assert "401" in str(excinfo.value)


def test_fish_audio_availability_needs_a_key_and_a_voice() -> None:
    no_key = FishAudioTTS(_fish_settings(fish_audio_api_key=""))
    assert no_key.available is False
    assert "FISH_AUDIO_API_KEY" in (no_key.unavailable_reason or "")

    no_voice = FishAudioTTS(_fish_settings(fish_audio_voice_id=""))
    assert no_voice.available is False
    assert "FISH_AUDIO_VOICE_ID" in (no_voice.unavailable_reason or "")

    assert FishAudioTTS(_fish_settings()).available is True


def test_fish_audio_content_type_follows_the_output_format() -> None:
    assert FishAudioTTS(_fish_settings()).content_type == "audio/mpeg"
    assert FishAudioTTS(_fish_settings(fish_audio_output_format="wav")).content_type == "audio/wav"
    assert FishAudioTTS(_fish_settings(fish_audio_output_format="pcm")).content_type == "audio/L16"


def test_factory_returns_fish_audio() -> None:
    engine = create_tts_engine(_fish_settings())
    assert isinstance(engine, FishAudioTTS)
    assert engine.name == "fish-audio"
