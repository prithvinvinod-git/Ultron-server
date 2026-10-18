"""Schedule routes (T509, spec §44/§29).

The scheduler (T170) already stores and fires schedule rows; §44's "persist
important scheduled jobs" is worth nothing over HTTP until a caller can see and
manage them. This is that surface, and nothing more:

* ``GET /schedules`` -- the collection, optionally only the active ones;
* ``POST /schedules`` -- create and arm a schedule;
* ``GET /schedules/{id}`` -- one schedule;
* ``PATCH /schedules/{id}`` -- edit its definition;
* ``DELETE /schedules/{id}`` -- remove it;
* ``POST /schedules/{id}/pause`` / ``/resume`` -- the two state writes §44 needs;
* ``POST /schedules/{id}/fire`` -- run a due schedule now.

**Reads use the request's transaction; writes go through the manager.** The two
GETs read through :class:`~app.database.repositories.schedules.ScheduleRepository`
inside the request's ``DbSession`` (which owns and commits the transaction), so a
list is one consistent read with no second connection. Everything that changes a
schedule, or fires one, goes through
:class:`~app.scheduler.manager.ScheduleManager`, because *it* owns the rules that
make a firing correct: the row lock that stops two workers firing the same
schedule, the trigger recomputation on resume, and the task it builds. A route
that reimplemented any of that would be a second, drifting scheduler.

**Timing is validated before it is stored.** ``ScheduleManager.create``/``update``
parse the kind's fields with the real calendar, so an unparseable cron expression
is a 4xx now rather than a silent failure discovered by the poll loop later.

**Domain errors become status codes by type.** An unknown id is a
``NotFoundError`` (404), a malformed body or timing field is an
``InvalidInputError``/``ScheduleError`` (422/503) — all mapped by
``app.main.add_exception_handlers``. Nothing here inspects an exception to pick a
code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Auth, Container, DbSession
from app.core.errors import NotFoundError
from app.database.models import ScheduleKind, ScheduleStatus
from app.database.repositories.schedules import ScheduleRepository

router = APIRouter(prefix="/schedules", tags=["schedules"])

#: Ceiling on a page. A larger request is refused by validation rather than
#: silently clamped, so a client cannot believe it read everything when it did not.
MAX_PAGE = 1000


class ScheduleCreate(BaseModel):
    """A schedule to create; the manager derives status and first fire time.

    ``extra="forbid"`` so a mistyped field is a 422 instead of a silently ignored
    argument. ``status`` is deliberately absent: a schedule is always born
    ``ACTIVE`` (the manager decides), and accepting a status here would let a
    client create a schedule already paused or completed.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    kind: ScheduleKind = ScheduleKind.INTERVAL
    payload: dict[str, Any] = Field(default_factory=dict)
    cron_expression: Annotated[str | None, Field(max_length=128)] = None
    interval_seconds: Annotated[int | None, Field(ge=1)] = None
    run_at: datetime | None = None
    project_id: uuid.UUID | None = None
    next_run_at: datetime | None = None


class ScheduleUpdate(BaseModel):
    """A partial edit; an omitted field is left unchanged.

    The kind is intentionally not editable — replacing an ``INTERVAL`` schedule
    with a ``CRON`` one is a different job, and a delete-and-create says that more
    clearly than a PATCH that changes what the row *is*.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    payload: dict[str, Any] | None = None
    cron_expression: Annotated[str | None, Field(max_length=128)] = None
    interval_seconds: Annotated[int | None, Field(ge=1)] = None
    run_at: datetime | None = None


class ScheduleRead(BaseModel):
    """A schedule as it crosses the wire.

    ``from_attributes`` lets an ORM row validate directly. Enum columns are typed
    as the enum, so a row loaded with a plain lowercase string still serialises as
    the documented value from one place.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: ScheduleKind
    status: ScheduleStatus
    cron_expression: str | None = None
    interval_seconds: int | None = None
    run_at: datetime | None = None
    payload: dict[str, Any]
    project_id: uuid.UUID | None = None
    created_by_id: uuid.UUID | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_task_id: uuid.UUID | None = None
    run_count: int
    failure_count: int
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class FireResult(BaseModel):
    """The outcome of ``POST /schedules/{id}/fire``.

    ``fired`` is False when the schedule is paused, was already advanced by
    another worker, or failed to produce a task — all of which the manager
    reports as "no task" rather than an error, because none of them is a caller
    mistake. ``task_id`` is present only when a task was produced.
    """

    schedule_id: uuid.UUID
    fired: bool
    task_id: uuid.UUID | None = None


@router.get("", response_model=list[ScheduleRead], summary="List schedules")
async def list_schedules(
    session: DbSession,
    principal: Auth,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> list[ScheduleRead]:
    """Return schedules, soonest next fire first, optionally only active ones.

    The administration view wants the paused and completed rows too, so the
    default is every row; ``active_only=true`` narrows to what the scheduler will
    actually fire.
    """
    repository = ScheduleRepository(session)
    if active_only:
        schedules = await repository.list_active(limit=limit)
    else:
        schedules = await repository.list_all(limit=limit)
    return [ScheduleRead.model_validate(schedule) for schedule in schedules]


@router.post(
    "",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule",
)
async def create_schedule(
    payload: ScheduleCreate,
    container: Container,
    principal: Auth,
) -> ScheduleRead:
    """Create and arm a schedule, computing its first fire time from its kind."""
    schedule = await container.schedule_manager.create(
        name=payload.name,
        kind=payload.kind,
        payload=payload.payload,
        cron_expression=payload.cron_expression,
        interval_seconds=payload.interval_seconds,
        run_at=payload.run_at,
        project_id=payload.project_id,
        created_by_id=principal.user_id,
        next_run_at=payload.next_run_at,
    )
    return ScheduleRead.model_validate(schedule)


@router.get("/{schedule_id}", response_model=ScheduleRead, summary="Get one schedule")
async def get_schedule(
    schedule_id: uuid.UUID,
    session: DbSession,
    principal: Auth,
) -> ScheduleRead:
    """Return one schedule, or ``404`` for an unknown id."""
    schedule = await ScheduleRepository(session).get_required(schedule_id)
    return ScheduleRead.model_validate(schedule)


@router.patch("/{schedule_id}", response_model=ScheduleRead, summary="Edit a schedule")
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    container: Container,
    principal: Auth,
) -> ScheduleRead:
    """Edit a schedule's definition, re-arming it if it is active."""
    schedule = await container.schedule_manager.update(
        schedule_id,
        name=payload.name,
        payload=payload.payload,
        cron_expression=payload.cron_expression,
        interval_seconds=payload.interval_seconds,
        run_at=payload.run_at,
    )
    return ScheduleRead.model_validate(schedule)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
async def delete_schedule(
    schedule_id: uuid.UUID,
    container: Container,
    principal: Auth,
) -> None:
    """Delete a schedule, or ``404`` when no such row exists."""
    deleted = await container.schedule_manager.delete(schedule_id)
    if not deleted:
        raise NotFoundError("schedules", str(schedule_id))


@router.post("/{schedule_id}/pause", response_model=ScheduleRead, summary="Pause a schedule")
async def pause_schedule(
    schedule_id: uuid.UUID,
    container: Container,
    principal: Auth,
) -> ScheduleRead:
    """Pause a schedule; it keeps its definition but stops being due."""
    schedule = await container.schedule_manager.pause(schedule_id)
    return ScheduleRead.model_validate(schedule)


@router.post("/{schedule_id}/resume", response_model=ScheduleRead, summary="Resume a schedule")
async def resume_schedule(
    schedule_id: uuid.UUID,
    container: Container,
    principal: Auth,
) -> ScheduleRead:
    """Resume a paused schedule and recompute its next fire time."""
    schedule = await container.schedule_manager.resume(schedule_id)
    return ScheduleRead.model_validate(schedule)


@router.post("/{schedule_id}/fire", response_model=FireResult, summary="Fire a schedule now")
async def fire_schedule(
    schedule_id: uuid.UUID,
    container: Container,
    principal: Auth,
) -> FireResult:
    """Run a due schedule now; ``fired=false`` when it is not due or not active."""
    task = await container.schedule_manager.fire(schedule_id)
    return FireResult(
        schedule_id=schedule_id,
        fired=task is not None,
        task_id=task.id if task is not None else None,
    )


__all__ = [
    "FireResult",
    "ScheduleCreate",
    "ScheduleRead",
    "ScheduleUpdate",
    "router",
]
