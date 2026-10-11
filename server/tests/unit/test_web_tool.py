"""T094 tests: the SSRF-guarded web fetch (spec §31).

The guard is tested without any network at all (loopback and metadata addresses
are refused by resolution, not by a live connection). The fetch behaviour is
tested against a throwaway HTTP server bound to 127.0.0.1, reached only by
turning the private-network allowance on — which is itself the proof that the
default is closed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config.settings import WebSettings
from app.core.errors import InvalidInputError, SsrfBlockedError
from app.tools.web import WebFetchTool, assert_host_allowed, assert_url_allowed, parse_url
from app.tools.web.extract import extract_text

pytestmark = pytest.mark.unit

_PAGE = (
    "<html><head><title>t</title><style>x{}</style></head>"
    "<body><p>Hello <b>world</b></p></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        body = _PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the standard request logging."""


@pytest.fixture()
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_parse_url_rejects_non_http_scheme() -> None:
    with pytest.raises(InvalidInputError):
        parse_url("file:///etc/passwd")


def test_parse_url_rejects_missing_host() -> None:
    with pytest.raises(InvalidInputError):
        parse_url("http:///nohost")


def test_loopback_is_blocked_by_default() -> None:
    with pytest.raises(SsrfBlockedError):
        assert_host_allowed("127.0.0.1", allow_private_networks=False)


def test_localhost_name_is_blocked_by_default() -> None:
    with pytest.raises(SsrfBlockedError):
        assert_host_allowed("localhost", allow_private_networks=False)


def test_link_local_metadata_is_blocked() -> None:
    with pytest.raises(SsrfBlockedError):
        assert_url_allowed("http://169.254.169.254/latest/meta-data/", allow_private_networks=False)


def test_private_networks_allowed_when_configured() -> None:
    assert_host_allowed("127.0.0.1", allow_private_networks=True)


def test_extract_text_drops_markup_and_scripts() -> None:
    text = extract_text(_PAGE)
    assert "Hello world" in text
    assert "x{}" not in text


async def test_fetch_is_blocked_without_allowance(base_url: str) -> None:
    tool = WebFetchTool(WebSettings(allow_private_networks=False))
    with pytest.raises(SsrfBlockedError):
        await tool.execute({"url": base_url})


async def test_fetch_reads_and_extracts(base_url: str) -> None:
    tool = WebFetchTool(WebSettings(allow_private_networks=True))
    result = await tool.execute({"url": base_url})
    assert result["status_code"] == 200
    assert "Hello world" in result["text"]
    assert result["truncated"] is False


async def test_fetch_follows_redirect_with_recheck(base_url: str) -> None:
    tool = WebFetchTool(WebSettings(allow_private_networks=True))
    result = await tool.execute({"url": f"{base_url}/redirect"})
    assert result["status_code"] == 200
    assert result["final_url"] == f"{base_url}/"
    assert "Hello world" in result["text"]


async def test_fetch_bounds_the_body(base_url: str) -> None:
    tool = WebFetchTool(WebSettings(allow_private_networks=True))
    result = await tool.execute({"url": base_url, "max_bytes": 10, "extract": False})
    assert result["bytes"] == 10
    assert result["truncated"] is True
