"""Notification route tests (T510, spec §45/§29).

The dispatcher and the facade have their own suites; what these tests answer is
the part only the assembled routes can: does ``GET /notifications`` read the
curated event set back as history, does ``GET /notifications/providers`` report the
live transport honestly, and does ``POST /notifications/test`` report the real
outcome of a send.

Unlike the Core route tests this suite uses a **real** :class:`NotificationService`
over a recording provider and a **real** event store over in-memory SQLite: the
routes lean on the facade's failure handling and the repository's query, so a
double for either would let the suite pass while the wiring was wrong.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.dependencies import require_principal
from app.config import DatabaseSettings, reload_settings
from app.core.errors import UltronError
from app.database.models import Event
from app.database.repositories.events import EventRepository
from app.database.session import (
    Base,
    create_engine,
    create_session_factory,
    dispose,
    session_scope,
)
from app.main import create_app
from app.notifications import (
    Notification,
    NotificationLevel,
    NotificationProvider,
    NotificationResult,
    NotificationService,
)
from app.security.authentication import Principal

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


class _RecordingProvider(NotificationProvider):
    """A provider that records what it was asked to deliver, and can fail."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []
        self.fail_with: str | None = None

    @property
    def name(self) -> str:
        return "recording"

    async def send(self, notification: Notification) -> NotificationResult:
        if self.fail_with is not None:
            raise UltronError(self.fail_with)
        self.sent.append(notification)
        return NotificationResult(provider=self.name, delivered=True, detail="recorded")


class _NotificationSettings:
    """The slice of ``NotificationSettings`` the routes read."""

    def __init__(self, *, enabled: bool = True, webhook_url: str = "") -> None:
        self.enabled = enabled
        self.webhook_url = webhook_url


class _StubSettings:
    def __init__(self, notifications: _NotificationSettings) -> None:
        self.notifications = notifications


class _StubContainer:
    """The slice of the container the notification routes reach for."""

    def __init__(self, service: NotificationService, settings: Any, factory: Any) -> None:
        self._service = service
        self._settings = settings
        self._factory = factory

    @property
    def settings(self) -> Any:
        return self._settings

    @property
    def notifications(self) -> NotificationService:
        return self._service

    def session_scope(self) -> Any:
        return _scope(self._factory)


@asynccontextmanager
async def _scope(factory: Any) -> AsyncIterator[AsyncSession]:
    async with session_scope(factory) as session:
        yield session


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=uuid.uuid4(),
        username="tester",
        is_superuser=True,
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A private in-memory database with the production schema (T015 pattern)."""
    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@asynccontextmanager
async def _api(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    webhook_url: str = "",
    provider: _RecordingProvider | None = None,
) -> AsyncIterator[tuple[AsyncClient, _RecordingProvider, Any]]:
    """Assemble the app over a real engine, a recording provider, a stub container."""
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()

    factory = create_session_factory(engine)
    recorder = provider or _RecordingProvider()
    service = NotificationService(recorder)
    settings = _StubSettings(_NotificationSettings(enabled=enabled, webhook_url=webhook_url))
    container = _StubContainer(service, settings, factory)

    app = create_app()
    app.state.container = container
    app.dependency_overrides[require_principal] = _principal
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, recorder, factory
    app.dependency_overrides.clear()


async def _append(
    factory: Any,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    source: str = "ultron.core",
    occurred_at: datetime | None = None,
) -> Event:
    """Append one event through the production repository."""
    async with session_scope(factory) as session:
        return await EventRepository(session).append(
            event_type=event_type,
            payload=payload or {},
            source=source,
            occurred_at=occurred_at,
        )


# --------------------------------------------------------------------------- #
# GET /notifications
# --------------------------------------------------------------------------- #


async def test_history_is_empty_at_first(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, _factory):
        response = await client.get("/notifications")
    assert response.status_code == 200
    assert response.json() == []


async def test_history_returns_mapped_events_only(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, factory):
        await _append(factory, "TASK_FAILED", {"error": "boom"})
        await _append(factory, "TASK_CREATED", {"goal": "noise"})
        response = await client.get("/notifications")

    body = response.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "TASK_FAILED"
    assert body[0]["level"] == "warning"
    assert body[0]["title"] == "Task Failed"
    assert body[0]["message"] == "boom"


async def test_history_is_newest_first(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, factory):
        await _append(
            factory, "TASK_FAILED", {"error": "old"}, occurred_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        await _append(
            factory, "AGENT_FAILED", {"error": "new"}, occurred_at=datetime(2026, 5, 2, tzinfo=UTC)
        )
        response = await client.get("/notifications")

    body = response.json()
    assert [entry["message"] for entry in body] == ["new", "old"]


async def test_history_respects_the_limit(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, factory):
        for index in range(3):
            await _append(factory, "TASK_FAILED", {"error": str(index)})
        response = await client.get("/notifications", params={"limit": 2})

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_history_rejects_an_oversized_limit(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, _factory):
        response = await client.get("/notifications", params={"limit": 1001})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# GET /notifications/providers
# --------------------------------------------------------------------------- #


async def test_providers_reports_the_live_transport(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, _factory):
        response = await client.get("/notifications/providers")

    body = response.json()
    assert body["enabled"] is True
    assert body["active"] == "recording"
    by_name = {entry["name"]: entry for entry in body["providers"]}
    assert by_name["log"]["active"] is False
    assert by_name["webhook"]["available"] is False


async def test_providers_reports_webhook_available_when_configured(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch, webhook_url="https://hooks.example.test/x") as (
        client,
        _recorder,
        _factory,
    ):
        response = await client.get("/notifications/providers")

    by_name = {entry["name"]: entry for entry in response.json()["providers"]}
    assert by_name["webhook"]["available"] is True


# --------------------------------------------------------------------------- #
# POST /notifications/test
# --------------------------------------------------------------------------- #


async def test_test_send_delivers_and_reports(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, recorder, _factory):
        response = await client.post(
            "/notifications/test",
            json={"title": "hello", "message": "world", "level": "critical"},
        )

    body = response.json()
    assert body["delivered"] is True
    assert body["provider"] == "recording"
    assert len(recorder.sent) == 1
    sent = recorder.sent[0]
    assert sent.title == "hello"
    assert sent.level is NotificationLevel.CRITICAL
    assert sent.source == "ultron.api"


async def test_test_send_rejects_an_unknown_field(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch) as (client, _recorder, _factory):
        response = await client.post("/notifications/test", json={"channel": "email"})
    assert response.status_code == 422


async def test_provider_failure_is_reported_not_raised(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _RecordingProvider()
    recorder.fail_with = "transport down"
    async with _api(engine, monkeypatch, provider=recorder) as (client, _recorder, _factory):
        response = await client.post("/notifications/test", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["detail"] == "transport down"


async def test_disabled_subsystem_sends_nothing(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _api(engine, monkeypatch, enabled=False) as (client, recorder, _factory):
        response = await client.post("/notifications/test", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["detail"] == "notifications are disabled"
    assert recorder.sent == []
