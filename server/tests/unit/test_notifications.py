"""T096 tests: the notification abstraction and providers (spec §45).

The contract under test is §45's decoupling: the caller holds only
``NotificationProvider``/``NotificationService``, a transport failure surfaces as
a failed *result* rather than an exception, and provider selection is a
construction decision.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from app.config.settings import NotificationSettings
from app.core.errors import ConfigMissingError, UltronError
from app.notifications import (
    LogNotificationProvider,
    Notification,
    NotificationLevel,
    NotificationProvider,
    NotificationResult,
    NotificationService,
    WebhookNotificationProvider,
    build_notification_provider,
    build_notification_service,
)

pytestmark = pytest.mark.unit


class _Capture(BaseHTTPRequestHandler):
    received: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append(json.loads(body.decode("utf-8")))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        """Silence the standard request logging."""


@pytest.fixture()
def webhook_url() -> Iterator[str]:
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        yield f"http://{host}:{port}/hook"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _FailingProvider(NotificationProvider):
    @property
    def name(self) -> str:
        return "failing"

    async def send(self, notification: Notification) -> NotificationResult:
        raise UltronError("transport down")


def test_log_is_the_default_provider() -> None:
    assert isinstance(build_notification_provider(NotificationSettings()), LogNotificationProvider)


def test_webhook_selected_when_configured() -> None:
    settings = NotificationSettings(default_provider="webhook", webhook_url="http://x/hook")
    provider = build_notification_provider(settings)
    assert isinstance(provider, WebhookNotificationProvider)


def test_webhook_without_url_falls_back_to_log() -> None:
    settings = NotificationSettings(default_provider="webhook", webhook_url="")
    assert isinstance(build_notification_provider(settings), LogNotificationProvider)


def test_webhook_requires_a_url() -> None:
    with pytest.raises(ConfigMissingError):
        WebhookNotificationProvider(NotificationSettings(webhook_url=""))


async def test_log_provider_always_delivers() -> None:
    result = await LogNotificationProvider().send(
        Notification(title="hi", message="there", level=NotificationLevel.WARNING)
    )
    assert result.delivered is True
    assert result.provider == "log"


async def test_webhook_provider_posts_json(webhook_url: str) -> None:
    provider = WebhookNotificationProvider(NotificationSettings(webhook_url=webhook_url))
    result = await provider.send(
        Notification(title="deploy", message="done", level=NotificationLevel.INFO, source="ci")
    )
    assert result.delivered is True
    assert _Capture.received[0]["title"] == "deploy"
    assert _Capture.received[0]["source"] == "ci"


async def test_service_reports_delivery_failure_without_raising() -> None:
    service = NotificationService(_FailingProvider())
    result = await service.notify("a", "b")
    assert result.delivered is False
    assert result.provider == "failing"


async def test_service_forwards_to_provider() -> None:
    service = build_notification_service(NotificationSettings())
    result = await service.notify("title", "body", source="test")
    assert result.delivered is True
