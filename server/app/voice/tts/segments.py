"""Sentence-level text segmentation for TTS (T151, spec §59.18).

§59.18 requires *sentence-level synthesis* so playback can begin before the whole
response is generated (§59.19's streaming pipeline). That needs the text split
into sentences first, and the splitter is kept pure and separate so it can be
tested in isolation -- the same discipline the STT utterance policy follows.

The splitter is deliberately conservative: it breaks after ``.``/``!``/``?``
followed by whitespace, and on newlines. It is not a full NLP tokeniser and does
not try to be one -- a mis-split only changes where a pause lands, never what is
said -- but it does keep the terminating punctuation with its sentence so the
concatenation of the parts reconstructs the input's wording.
"""

from __future__ import annotations

import re

__all__ = ["split_sentences"]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into non-empty, whitespace-trimmed sentences.

    Returns ``[]`` for empty or whitespace-only input. The concatenation of the
    parts preserves the original words (punctuation stays attached); only the
    boundary whitespace is dropped, which is what makes each part synthesise as
    its own utterance.
    """
    if not text or not text.strip():
        return []
    return [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
