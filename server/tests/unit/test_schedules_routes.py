"""Schedule route tests (T509, spec §44/§29).

The scheduler's firing, locking and trigger logic is covered by its own suite;
what these tests answer is the part only the assembled routes can: does
``GET /schedules`` serialise a row, does a create arm the schedule, does an edit
re-arm it, and do the two state writes and a manual fire reach the manager.

Unlike the Core route tests, this suite uses a **real** :class:`ScheduleManager`
over an in-memory SQLite database rather than a fake. The routes deliberately
lean on the manager for correctness (validation, the resume recompute, the fire
claim), so a double would let the suite pass while the wiring was wrong; the
in-memory engine gives the real manager a real session without PostgreSQL or
Redis. Only the task a firing produces is faked, because the task graph is not
this suite's subject.
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
from app.config.settings import SchedulerSettings
from app.database.models import Schedule, Task, TaskPriority, TaskStatus
from app.database.session import (
    Base,
    create_engine,
    create_session_factory,
    dispose,
    session_scope,
)
from app.main import create_app
from app.scheduler.manager import ScheduleManager
from app.security.authentication import Principal

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"

#: Frozen clock, so "is this due" and "what is the next fire" are deterministic.
FIXED_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


class _StubContainer:
    """The slice of the container the schedule routes reach for.

    ``session_scope`` is the real unit of work over the test engine, so the two
    read routes read through the production repository exactly as deployed.
    """

    def __init__(self, manager: Any, factory: Any) -> None:
        self._manager = manager
        self._factory = factory

    @property
    def schedule_manager(self) -> Any:
        return self._manager

    def session_scope(self) -> Any:
        return _scope(self._factory)


async def _create_task(session: AsyncSession, schedule: Schedule) -> Task:
    """The injected task factory: a persisted task, standing in for the graph."""
    task = Task(
        goal=schedule.name,
        status=TaskStatus.PENDING,
        priority=TaskPriority.NORMAL,
        retry_count=0,
        max_retries=3,
    )
    session.add(task)
    await session.flush()
    return task


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=uuid.uuid4(),
        username="tester",
        is_superuser=True,
    )


class _Api:
    """The assembled app plus the manager and engine behind it."""

    def __init__(self, client: AsyncClient, manager: Any) -> None:
        self.client = client
        self.manager = manager


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


@pytest.fixture
async def api(monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine) -> AsyncIterator[_Api]:
    """The real app on a real manager over SQLite, with the principal stubbed.

    ``ASGITransport`` (not ``TestClient``) so the app shares this test's event
    loop with the engine; a sync client would run the app in a second loop and
    the shared in-memory connection would be used from two places at once.
    """
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()

    factory = create_session_factory(engine)
    manager = ScheduleManager(
        settings=SchedulerSettings(timezone="UTC"),
        session_scope=lambda: _scope(factory),
        create_task=_create_task,
        now=lambda: FIXED_NOW,
    )

    app = create_app()
    app.state.container = _StubContainer(manager, factory)
    app.dependency_overrides[require_principal] = _principal
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield _Api(client, manager)
    app.dependency_overrides.clear()


@asynccontextmanager
async def _scope(factory: Any) -> AsyncIterator[AsyncSession]:
    """The container's ``session_scope`` seam, wired to the test factory."""
    async with session_scope(factory) as session:
        yield session


async def _create_schedule(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "nightly", "kind": "interval", "interval_seconds": 3600}
    body.update(overrides)
    response = await client.post("/schedules", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# --------------------------------------------------------------------------- #
# GET /schedules
# --------------------------------------------------------------------------- #


async def test_list_schedules_is_empty_by_default(api: _Api) -> None:
    response = await api.client.get("/schedules")
    assert response.status_code == 200
    assert response.json() == []


async def test_a_created_schedule_is_listed(api: _Api) -> None:
    created = await _create_schedule(api.client)
    response = await api.client.get("/schedules")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [created["id"]]


async def test_active_only_hides_a_paused_schedule(api: _Api) -> None:
    created = await _create_schedule(api.client)
    await api.client.post(f"/schedules/{created['id']}/pause")
    active = await api.client.get("/schedules", params={"active_only": "true"})
    assert active.json() == []
    everything = await api.client.get("/schedules")
    assert len(everything.json()) == 1


# --------------------------------------------------------------------------- #
# POST /schedules
# --------------------------------------------------------------------------- #


async def test_create_arms_an_interval_schedule(api: _Api) -> None:
    created = await _create_schedule(api.client)
    assert created["kind"] == "interval"
    assert created["status"] == "active"
    assert created["interval_seconds"] == 3600
    assert created["next_run_at"] is not None
    assert created["run_count"] == 0


async def test_create_a_cron_schedule_without_an_expression_is_422(api: _Api) -> None:
    response = await api.client.post("/schedules", json={"name": "bad", "kind": "cron"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_create_rejects_a_unknown_field(api: _Api) -> None:
    response = await api.client.post(
        "/schedules",
        json={"name": "x", "kind": "interval", "interval_seconds": 60, "status": "active"},
    )
    assert response.status_code == 422


async def test_create_rejects_a_non_positive_interval(api: _Api) -> None:
    response = await api.client.post(
        "/schedules",
        json={"name": "x", "kind": "interval", "interval_seconds": 0},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# GET /schedules/{id}
# --------------------------------------------------------------------------- #


async def test_get_one_schedule(api: _Api) -> None:
    created = await _create_schedule(api.client)
    response = await api.client.get(f"/schedules/{created['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "nightly"


async def test_get_unknown_schedule_is_404(api: _Api) -> None:
    response = await api.client.get(f"/schedules/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_malformed_schedule_id_is_422(api: _Api) -> None:
    response = await api.client.get("/schedules/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# PATCH /schedules/{id}
# --------------------------------------------------------------------------- #


async def test_update_renames_and_rearms(api: _Api) -> None:
    created = await _create_schedule(api.client)
    response = await api.client.patch(f"/schedules/{created['id']}", json={"name": "renamed"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "renamed"
    assert body["kind"] == "interval"
    assert body["next_run_at"] is not None


async def test_update_an_unknown_schedule_is_404(api: _Api) -> None:
    response = await api.client.patch(f"/schedules/{uuid.uuid4()}", json={"name": "x"})
    assert response.status_code == 404


async def test_update_rejects_an_empty_name(api: _Api) -> None:
    created = await _create_schedule(api.client)
    response = await api.client.patch(f"/schedules/{created['id']}", json={"name": ""})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# pause / resume / fire
# --------------------------------------------------------------------------- #


async def test_pause_then_resume(api: _Api) -> None:
    created = await _create_schedule(api.client)
    paused = await api.client.post(f"/schedules/{created['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = await api.client.post(f"/schedules/{created['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    assert resumed.json()["next_run_at"] is not None


async def test_fire_a_due_schedule_produces_a_task(api: _Api) -> None:
    created = await _create_schedule(
        api.client,
        kind="once",
        interval_seconds=None,
        run_at="2026-05-01T11:00:00+00:00",
        next_run_at="2026-05-01T11:00:00+00:00",
    )
    response = await api.client.post(f"/schedules/{created['id']}/fire")
    assert response.status_code == 200
    body = response.json()
    assert body["fired"] is True
    assert body["task_id"] is not None

    # A one-shot that fired is completed and no longer due.
    after = await api.client.get(f"/schedules/{created['id']}")
    assert after.json()["status"] == "completed"
    assert after.json()["run_count"] == 1


async def test_fire_a_paused_schedule_does_nothing(api: _Api) -> None:
    created = await _create_schedule(
        api.client,
        kind="once",
        interval_seconds=None,
        run_at="2026-05-01T11:00:00+00:00",
        next_run_at="2026-05-01T11:00:00+00:00",
    )
    await api.client.post(f"/schedules/{created['id']}/pause")
    response = await api.client.post(f"/schedules/{created['id']}/fire")
    assert response.status_code == 200
    assert response.json()["fired"] is False
    assert response.json()["task_id"] is None


async def test_fire_an_unknown_schedule_is_404(api: _Api) -> None:
    response = await api.client.post(f"/schedules/{uuid.uuid4()}/fire")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# DELETE /schedules/{id}
# --------------------------------------------------------------------------- #


async def test_delete_then_get_is_404(api: _Api) -> None:
    created = await _create_schedule(api.client)
    deleted = await api.client.delete(f"/schedules/{created['id']}")
    assert deleted.status_code == 204
    again = await api.client.get(f"/schedules/{created['id']}")
    assert again.status_code == 404


async def test_delete_an_unknown_schedule_is_404(api: _Api) -> None:
    response = await api.client.delete(f"/schedules/{uuid.uuid4()}")
    assert response.status_code == 404
