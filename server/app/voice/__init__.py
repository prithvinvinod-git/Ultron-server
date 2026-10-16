"""Voice subsystem: speech to text, text to speech and wake-word handling (§25).

Provider-independent by design (§25): the pipeline reaches STT/TTS behind the
:class:`~app.voice.stt.base.STTEngine` and :class:`~app.voice.tts.base.TTSEngine`
interfaces, and faster-whisper / Piper are merely the first local adapters (§59.17,
§59.18). Missing engines degrade honestly to a null engine that raises a typed
503 rather than faking speech (§64.11).

The pieces:

- :mod:`app.voice.stt` -- STT engines and the §59.17 utterance-boundary policy.
- :mod:`app.voice.tts` -- TTS engines and sentence-level synthesis (§59.18).
- :mod:`app.voice.wake` -- wake-word detection raising ``WAKE_DETECTED`` (§26).
- :mod:`app.voice.manager` -- sessions and the STT -> Core -> Agent -> TTS
  pipeline with barge-in (§59.19).
"""

from __future__ import annotations

from app.voice.manager import VoiceManager, VoiceReply, VoiceSession, VoiceState, VoiceTurn
from app.voice.stt.base import AudioChunk, STTEngine, TranscriptChunk
from app.voice.tts.base import TTSEngine
from app.voice.wake.detector import WakeWordDetector

__all__ = [
    "AudioChunk",
    "STTEngine",
    "TTSEngine",
    "TranscriptChunk",
    "VoiceManager",
    "VoiceReply",
    "VoiceSession",
    "VoiceState",
    "VoiceTurn",
    "WakeWordDetector",
]
