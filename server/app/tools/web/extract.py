"""Readable-text extraction for the web tool (T094).

A fetch returns HTML, but a model wants the words. This strips tags, drops
script/style markup, and collapses whitespace, using only the standard library
so no new dependency is pulled in for what is a bounded, best-effort operation.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["extract_text"]

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head", "title"})
_WHITESPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """Collect visible text, ignoring the tags that never hold prose."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._parts.append(text)


def extract_text(html: str) -> str:
    """Return the visible text of ``html`` with whitespace collapsed to spaces."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return _WHITESPACE.sub(" ", " ".join(parser._parts)).strip()
