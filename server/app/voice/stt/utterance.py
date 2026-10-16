"""Utterance boundaries and overlap policy for streaming STT (T150, spec §59.17).

§59.17 names a specific defect to design against, so it is implemented here as a
policy that is *testable in isolation* rather than buried inside the whisper
adapter:

> A pause must not cause the preceding audio to be retransmitted as a new utterance.

Two things go wrong without an explicit policy, and both are guarded here:

1. **Chunk boundaries are not utterance boundaries.** A streaming transcriber
   hands back audio in fixed-size chunks. A chunk that happens to end on a
   mid-sentence pause is *not* the end of what the user said. If the pause
   closed the utterance, the next chunk would start a second one and the same
   sentence would be transcribed (and billed) twice. :class:`UtteranceBoundary`
   therefore only ends an utterance after a run of silence that meets a
   threshold -- a *pause* recovers, an *end-of-turn* does not.
2. **Consecutive chunks overlap.** Sliding-window transcribers re-transcribe the
   tail of the previous chunk as the head of the next, so the raw text is
   ``"play some music""play some music now"``. :class:`OverlapDeduper` strips
   the duplicated head so the running transcript reads once.

Both classes are pure and synchronous: they take text/flags and return text/flags
and touch no audio, no model and no event loop, so the policy can be asserted
directly by tests (and by the recorded-audio fixtures §59.17 asks for) without a
whisper install.
"""

from __future__ import annotations

__all__ = ["OverlapDeduper", "UtteranceBoundary"]


class OverlapDeduper:
    """Strips the duplicated head created when consecutive chunks overlap.

    The transcriber emits a *sliding window*, so chunk ``n+1`` usually begins
    with some of the tail of chunk ``n``. This class keeps a short memory of the
    running transcript and, on each :meth:`feed`, removes the longest suffix of
    that memory which is also a prefix of the new chunk. What is left is the genuinely
    new text.

    :param max_overlap: the largest overlap (in characters) that will ever be
        stripped. Overlap beyond the window is not a re-transcription, it is
        genuinely new content that happens to share a word, so the search is
        bounded and a single shared word never eats real speech.
    """

    def __init__(self, *, max_overlap: int = 64) -> None:
        if max_overlap < 0:
            raise ValueError("max_overlap must be non-negative")
        self.max_overlap = max_overlap
        self._tail = ""

    @property
    def tail(self) -> str:
        """The trailing context used to detect the next overlap."""
        return self._tail

    def reset(self) -> None:
        """Forget the running context (start of a new utterance)."""
        self._tail = ""

    def feed(self, text: str) -> str:
        """Return ``text`` with any overlap against the previous chunk removed.

        The returned text may be empty (the chunk was entirely a re-read of the
        tail), which is the correct answer, not an error.
        """
        if not text:
            return ""
        overlap = self._longest_overlap(text)
        new_text = text[overlap:]
        # Remember the tail of the *running* transcript, not just this chunk, so
        # an overlap spanning three chunks is still found.
        combined = (self._tail + new_text)[-self.max_overlap :] if self.max_overlap else ""
        self._tail = combined
        return new_text

    def _longest_overlap(self, text: str) -> int:
        window = min(self.max_overlap, len(self._tail), len(text))
        for size in range(window, 0, -1):
            if self._tail[-size:] == text[:size]:
                return size
        return 0


class UtteranceBoundary:
    """Separates a mid-sentence pause from a genuine end-of-turn (§59.17, §59.19).

    Speech-to-text streams in chunks, and some of those chunks are pure silence.
    The wrong behaviour -- the defect §59.17 names -- is to treat *any* silent
    chunk as "the user stopped talking" and close the utterance. The right
    behaviour is to require a *run* of silence that reaches a threshold before
    the turn ends, so a breath or a mid-sentence pause keeps the same utterance
    open and the next spoken chunk is appended to it rather than starting a new
    one.

    The threshold is expressed in *silent chunks*, not milliseconds, because the
    policy sits above the transcriber and must not depend on a particular chunk
    length. Callers that know their chunk duration can convert
    (``silence_threshold = ceil(silence_seconds / chunk_seconds)``).

    :param silence_threshold: how many consecutive non-speech chunks end a turn.
    """

    def __init__(self, *, silence_threshold: int = 3) -> None:
        if silence_threshold < 1:
            raise ValueError("silence_threshold must be at least 1")
        self.silence_threshold = silence_threshold
        self._silent_chunks = 0
        self._open = False

    @property
    def open(self) -> bool:
        """Whether an utterance is currently in progress."""
        return self._open

    @property
    def silence_run(self) -> int:
        """How many consecutive non-speech chunks have been seen."""
        return self._silent_chunks

    def feed(self, *, is_speech: bool) -> bool:
        """Advance the boundary with one chunk and report whether the turn ended.

        Returns ``True`` exactly once per completed utterance: the chunk that
        pushed the silence run to the threshold. A speech chunk resets the run
        and never ends a turn.
        """
        if is_speech:
            self._silent_chunks = 0
            self._open = True
            return False
        self._silent_chunks += 1
        if self._open and self._silent_chunks >= self.silence_threshold:
            self._open = False
            self._silent_chunks = 0
            return True
        return False

    def reset(self) -> None:
        """Force-close any open utterance (a session is ending or was barged into)."""
        self._silent_chunks = 0
        self._open = False
