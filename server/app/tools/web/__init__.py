"""The web tool family (T094, spec §31).

``web.fetch`` is the only web capability exposed here: it fetches an ``http(s)``
URL the SSRF guard permits, follows redirects with a per-hop re-check, and
returns a bounded body plus extracted readable text.
"""

from __future__ import annotations

from app.tools.web.fetch import WebFetchTool, build_web_tools
from app.tools.web.ssrf import assert_host_allowed, assert_url_allowed, parse_url

__all__ = [
    "WebFetchTool",
    "assert_host_allowed",
    "assert_url_allowed",
    "build_web_tools",
    "parse_url",
]
