"""Phase 11 — scheduler + automation + notifications (T174, §44/§45).

Covers the on-time/due polling, event-triggered automation (T171),
persistent workflows (T172), notification dispatch (T173), and the phase
gate: ``SCHEDULE_TRIGGERED`` fires and produces a real task. No external
services, uses SQLite and in-memory doubles.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import DatabaseSettings
from app.config.settings import SchedulerSettings
from app.core.errors import NotFoundError
from app.database.session import Base, create_engine, create_session_factory, dispose
from app.database.models import Schedule, ScheduleKind, ScheduleStatus, Task
from app.database.models.enums import TaskPriority
from app.database.repositories.schedules import ScheduleRepository
from app.database.repositories.tasks import TaskRepository
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.notifications.base import NotificationLevel, NotificationResult
from app.notifications.dispatch import NOTIFY_LEVELS, NotificationDispatch, build_schedule_notifier
from app.notifications.service import NotificationService
from app.scheduler.automation import EventAutomation, matches
from app.scheduler.manager import ScheduleManager

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A private in-memory database with the production schema."""
    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the test database."""
    return create_session_factory(engine)


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database, rolled back after each test."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@dataclass
class NotifierCalls:
    calls: list[tuple[Schedule, BaseException]]

    def __call__(self, schedule: Schedule, exc: BaseException) -> Awaitable[None]:
        self.calls.append((schedule, exc))

        async def _noop() -> None:
            return None

        return _noop()


class RecordingBus(EventBus):
    sent: list[EventEnvelope]

    def __init__(self) -> None:
        super().__init__()
        self.sent = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> EventEnvelope:
        envelope = await super().publish(event_type, payload, **kwargs)
        self.sent.append(envelope)
        return envelope


class CapturingNotifierService:
    results: list[tuple[NotificationResult, str, str, NotificationLevel, str | None, dict[str, Any] | None]]

    def __init__(self) -> None:
        self.results = []

    async def notify(
        self,
        title: str,
        message: str,
        *,
        level: NotificationLevel = NotificationLevel.INFO,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationResult:
        result = NotificationResult(
            provider="test",
            delivered=True,
            detail="ok",
        )
        self.results.append((result, title, message, level, source, metadata))
        return result


@pytest_asyncio.fixture(name="recording_bus")
async def recording_bus_fixture() -> RecordingBus:
    return RecordingBus()


async def _task_factory(session: AsyncSession, schedule: Schedule) -> Task:
    repo = TaskRepository(session)
    task = Task(
        goal=str(schedule.payload.get("goal") or schedule.name),
        priority=TaskPriority.NORMAL,
        project_id=schedule.project_id,
    )
    await repo.add(task)
    return task


class AsyncList(list[Any]):
    async def append(self, item: Any) -> None:
        super().append(item)


def make_scope(session: AsyncSession) -> Callable[[], AsyncIterator[AsyncSession]]:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session
    return scope


@pytest.mark.asyncio
async def test_manager_run_due_fires_once(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    created: AsyncList[Task] = AsyncList()

    async def factory(session: AsyncSession, schedule: Schedule) -> Task:
        task = await _task_factory(session, schedule)
        await created.append(task)
        return task

    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=factory,
        bus=recording_bus,
        now=lambda: datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )

    repo = ScheduleRepository(db)
    schedule = Schedule(
        name="greet",
        kind=ScheduleKind.INTERVAL,
        status=ScheduleStatus.ACTIVE,
        interval_seconds=60,
        next_run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        payload={"goal": "greet"},
    )
    await repo.add(schedule)

    fired = await manager.run_due()
    assert fired and created
    assert EventType.SCHEDULE_TRIGGERED.value in {e.event_type for e in recording_bus.sent}
    assert created[0].goal == "greet"


@pytest.mark.asyncio
async def test_manager_fire_requires_due_and_skips_not_due(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
    )

    repo = ScheduleRepository(db)
    future = Schedule(
        name="future",
        kind=ScheduleKind.INTERVAL,
        status=ScheduleStatus.ACTIVE,
        interval_seconds=60,
        next_run_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        payload={"goal": "later"},
    )
    await repo.add(future)

    result = await manager.fire(future.id, now=datetime(2026, 1, 1, 11, 0, tzinfo=UTC))
    assert result is None


@pytest.mark.asyncio
async def test_manager_trigger_event_ignores_require_due(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
    )

    repo = ScheduleRepository(db)
    event = Schedule(
        name="on-something",
        kind=ScheduleKind.EVENT,
        status=ScheduleStatus.ACTIVE,
        payload={"event": "TASK_FAILED", "match": {}, "goal": "react"},
    )
    await repo.add(event)

    task = await manager.trigger_event(event.id, now=datetime(2026, 1, 1, 11, 0, tzinfo=UTC))
    assert task is not None


@pytest.mark.asyncio
async def test_manager_advance_once_completes_and_event_records_run(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
        now=lambda: datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )

    repo = ScheduleRepository(db)
    once = Schedule(
        name="once",
        kind=ScheduleKind.ONCE,
        status=ScheduleStatus.ACTIVE,
        run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        next_run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        payload={"goal": "once"},
    )
    await repo.add(once)

    await manager.fire(once.id)
    row = await repo.get_required(once.id)
    assert ScheduleStatus(row.status) is ScheduleStatus.COMPLETED
    assert row.next_run_at is None

    event = Schedule(
        name="e",
        kind=ScheduleKind.EVENT,
        status=ScheduleStatus.ACTIVE,
        payload={"event": "X", "goal": "e"},
    )
    await repo.add(event)

    await manager.trigger_event(event.id)
    row = await repo.get_required(event.id)
    assert row.run_count == 1
    assert ScheduleStatus(row.status) is ScheduleStatus.ACTIVE
    assert row.next_run_at is None


@pytest.mark.asyncio
async def test_manager_deleted_between_list_and_fire_is_skipped(
    db: AsyncSession,
) -> None:
    deleted = []

    async def factory(session: AsyncSession, schedule: Schedule) -> Task:
        deleted.append(schedule.id)
        return await _task_factory(session, schedule)

    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=factory,
    )

    repo = ScheduleRepository(db)
    sid = await repo.add(
        Schedule(
            name="d",
            kind=ScheduleKind.INTERVAL,
            status=ScheduleStatus.ACTIVE,
            interval_seconds=60,
            next_run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            payload={"goal": "d"},
        )
    )
    await repo.delete(sid.id)

    fired = await manager.run_due(now=datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    assert fired == []
    assert deleted == []


@pytest.mark.asyncio
async def test_manager_get_for_update_not_found_raises_notfound(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
    )

    with pytest.raises(NotFoundError):
        await manager.fire("00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_manager_failure_is_recorded_and_notified(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    calls = NotifierCalls([])

    async def factory(session: AsyncSession, schedule: Schedule) -> Task:  # noqa: ARG001
        raise RuntimeError("boom")

    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=factory,
        bus=recording_bus,
        notify=calls,
    )

    repo = ScheduleRepository(db)
    sid = await repo.add(
        Schedule(
            name="bad",
            kind=ScheduleKind.INTERVAL,
            status=ScheduleStatus.ACTIVE,
            interval_seconds=60,
            next_run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            payload={"goal": "bad"},
        )
    )

    result = await manager.fire(sid.id, now=datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    assert result is None
    assert calls.calls
    row = await repo.get_required(sid.id)
    assert row.failure_count == 1
    assert "boom" in (row.last_error or "")


@pytest.mark.asyncio
async def test_automation_matches_cases_and_subset() -> None:
    s = Schedule(
        name="n",
        kind=ScheduleKind.EVENT,
        status=ScheduleStatus.ACTIVE,
        payload={"event": "device.connected", "match": {"device_id": "d1"}},
    )
    env = EventEnvelope(
        id=1,
        event_type="DEVICE.CONNECTED",
        payload={"device_id": "d1", "at": 1},
    )
    assert matches(s, env)

    env2 = EventEnvelope(id=2, event_type="DEVICE.CONNECTED", payload={"device_id": "d2"})
    assert not matches(s, env2)

    s2 = Schedule(
        name="n2",
        kind=ScheduleKind.EVENT,
        status=ScheduleStatus.ACTIVE,
        payload={"event": "X", "match": {"a": 1, "b": 2}},
    )
    env3 = EventEnvelope(id=3, event_type="x", payload={"a": 1})
    assert not matches(s2, env3)

    env4 = EventEnvelope(id=4, event_type="x", payload={"a": 1, "b": 2, "c": 3})
    assert matches(s2, env4)


@pytest.mark.asyncio
async def test_automation_ignores_lagged_and_triggers(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    fired: list[str] = []

    async def triggerer(sid: Any) -> Task | None:
        fired.append(str(sid))
        session = db
        repo = TaskRepository(session)
        task = Task(goal="react")
        return await repo.add(task)

    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
    )
    manager.trigger_event = triggerer  # type: ignore[assignment]

    repo = ScheduleRepository(db)
    sid = await repo.add(
        Schedule(
            name="auto",
            kind=ScheduleKind.EVENT,
            status=ScheduleStatus.ACTIVE,
            payload={"event": "TASK_FAILED", "match": {}, "goal": "react"},
        )
    )

    auto = EventAutomation(scheduler=manager, bus=recording_bus)
    await auto.refresh()
    env = EventEnvelope(id=1, event_type="TASK_FAILED", payload={})
    count = await auto.handle(env)
    assert count == 1
    assert fired == [str(sid.id)]

    lag = EventEnvelope(id=2, event_type="stream.lagged", payload={"lagged": 1})
    assert await auto.handle(lag) == 0


@pytest.mark.asyncio
async def test_automation_refresh_filters_only_event_kind(
    db: AsyncSession,
    recording_bus: RecordingBus,
) -> None:
    scope = make_scope(db)

    manager = ScheduleManager(
        settings=SchedulerSettings(enabled=True),
        session_scope=scope,
        create_task=_task_factory,
        bus=recording_bus,
    )

    repo = ScheduleRepository(db)
    await repo.add(
        Schedule(
            name="ev",
            kind=ScheduleKind.EVENT,
            status=ScheduleStatus.ACTIVE,
            payload={"event": "X", "goal": "x"},
        )
    )
    await repo.add(
        Schedule(
            name="cron",
            kind=ScheduleKind.CRON,
            status=ScheduleStatus.ACTIVE,
            cron_expression="* * * * *",
            payload={"goal": "c"},
        )
    )

    auto = EventAutomation(scheduler=manager, bus=recording_bus)
    await auto.refresh()
    assert len(auto._schedules) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_notification_levels_and_dispatch_and_notifier(
    recording_bus: RecordingBus,
    db: AsyncSession,
) -> None:
    service = CapturingNotifierService()
    dispatch = NotificationDispatch(service=service, bus=recording_bus)
    await dispatch.start()
    env = EventEnvelope(id=1, event_type="TASK_FAILED", payload={"error": "bad"})
    assert await dispatch.handle(env)
    assert service.results
    assert NOTIFY_LEVELS[EventType.TASK_FAILED] is NotificationLevel.WARNING
    notifier = build_schedule_notifier(service)
    repo = ScheduleRepository(db)
    s = Schedule(
        name="s",
        kind=ScheduleKind.INTERVAL,
        status=ScheduleStatus.ACTIVE,
        interval_seconds=1,
    )
    await repo.add(s)
    await notifier(s, RuntimeError("x"))
    assert service.results[-1][4] == "ultron.scheduler"
    await dispatch.stop()
    unknown = EventEnvelope(id=99, event_type="unknown.thing", payload={})
    assert not await dispatch.handle(unknown)