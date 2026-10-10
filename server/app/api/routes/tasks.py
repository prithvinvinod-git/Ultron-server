"""Task routes (T051, spec §18/§29).

The REST surface over the task aggregate. Six resources' worth of intent, and
nothing else:

* ``GET /tasks`` -- the collection, optionally filtered by status or project;
* ``POST /tasks`` -- materialise a requested goal as a PENDING task;
* ``GET /tasks/{id}`` -- one task;
* ``POST /tasks/{task_id}/cancel`` / ``/retry`` / ``/priority`` — the three
  state writes §18 exposes (a cancel, a §66.11 retry, a reprioritise);
* the ``/tasks/{id}/steps`` pair, because §18 lists ``steps`` as a task field
  and the graph executor (T041) reads them.

**The routes are thin on purpose.** Every one of them parses input, calls one
method on :class:`~app.tasks.manager.TaskManager`, and serialises the result.
No route reads a repository, writes a column, or commits: the status machine
(T038) owns legality, the manager owns the field rules, and the request's
``session_scope`` owns the transaction. A route that committed would make the
request span two units of work and let a later failure leave a half-written
task behind.

**Domain errors become status codes by type, not by hand.** ``TaskNotFoundError``
is a 404, ``ConflictError`` from an illegal transition is a 409, a malformed id
is a 422 — all installed by ``app.main.add_exception_handlers``. Nothing here
inspects an exception to pick a code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Auth, Container, DbSession
from app.database.models.enums import (
    StepStatus,
    TaskPriority,
    TaskStatus,
    VerificationOutcome,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: Ceiling on a page, matching the repository's own ``MAX_LIMIT``. A larger
#: request is refused by validation rather than silently clamped, so a client
#: cannot believe it read everything when it did not.
MAX_PAGE = 1000


class TaskCreate(BaseModel):
    """The caller-supplied half of a task; everything else is derived.

    ``extra="forbid"`` so a mistyped field is a 422 instead of a silently
    ignored argument. ``status`` is deliberately absent: a task is always born
    ``PENDING`` (the manager decides), and accepting a status here would let a
    client create a task directly into a terminal state.
    """

    model_config = ConfigDict(extra="forbid")

    goal: Annotated[str, Field(min_length=1, max_length=8192)]
    priority: TaskPriority = TaskPriority.NORMAL
    parent_task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    scheduled_for: datetime | None = None
    max_retries: Annotated[int, Field(ge=0, le=20)] = 3


class StepCreate(BaseModel):
    """One step of a task's graph (§18's ``steps`` field)."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: str | None = None
    position: Annotated[int, Field(ge=0)] | None = None
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    input_payload: dict[str, Any] | None = None


class PriorityUpdate(BaseModel):
    """A new priority for an existing task."""

    model_config = ConfigDict(extra="forbid")

    priority: TaskPriority


class TaskRead(BaseModel):
    """A task as it crosses the wire.

    ``from_attributes`` lets an ORM row validate directly. Enum columns are
    typed as the enum, so a row loaded with a plain lowercase string (the ORM
    does not reconstruct members on load) still serialises as the documented
    value from one place.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal: str
    status: TaskStatus
    priority: TaskPriority
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    verification: VerificationOutcome | None = None
    retry_count: int
    max_retries: int
    scheduled_for: datetime | None = None


class TaskStepRead(BaseModel):
    """One step, with the graph edge list and the attempt outcome."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    position: int
    name: str
    description: str | None = None
    status: StepStatus
    attempt: int
    depends_on: list[str]
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None
    error: str | None = None


@router.get("", response_model=list[TaskRead], summary="List tasks")
async def list_tasks(
    container: Container,
    session: DbSession,
    principal: Auth,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    project_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TaskRead]:
    """Return tasks, newest first, optionally filtered.

    ``status`` narrows to one state and ``project_id`` to one project; with
    neither, every task is listed. A status filter wins when both are given,
    because it is the more selective and the one a worker queues on.
    """
    manager = container.get_task_manager(session)
    if task_status is not None:
        tasks = await manager.list_by_status(task_status, limit=limit, offset=offset)
    elif project_id is not None:
        tasks = await manager.list_for_project(project_id, limit=limit)
    else:
        tasks = await manager.list_all(limit=limit, offset=offset)
    return [TaskRead.model_validate(task) for task in tasks]


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)
async def create_task(
    payload: TaskCreate,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskRead:
    """Create a task in ``PENDING`` and return it."""
    manager = container.get_task_manager(session)
    task = await manager.create(
        payload.goal,
        priority=payload.priority,
        parent_task_id=payload.parent_task_id,
        project_id=payload.project_id,
        conversation_id=payload.conversation_id,
        scheduled_for=payload.scheduled_for,
        max_retries=payload.max_retries,
    )
    return TaskRead.model_validate(task)


@router.get("/{task_id}", response_model=TaskRead, summary="Get one task")
async def get_task(
    task_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskRead:
    """Return one task, or ``404``/``TASK_NOT_FOUND``."""
    manager = container.get_task_manager(session)
    return TaskRead.model_validate(await manager.get(task_id))


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a finished task",
)
async def delete_task(
    task_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> None:
    """Delete a finished task; a live task must be cancelled first (409)."""
    manager = container.get_task_manager(session)
    await manager.delete(task_id)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskRead,
    summary="Cancel a task",
)
async def cancel_task(
    task_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskRead:
    """Move a live task to ``CANCELLED``; a terminal task is a 409."""
    manager = container.get_task_manager(session)
    return TaskRead.model_validate(await manager.transition(task_id, TaskStatus.CANCELLED))


@router.post(
    "/{task_id}/retry",
    response_model=TaskRead,
    summary="Retry a failed task",
)
async def retry_task(
    task_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskRead:
    """Requeue a FAILED task, counting the attempt (§66.11's bound)."""
    manager = container.get_task_manager(session)
    return TaskRead.model_validate(await manager.retry(task_id))


@router.post(
    "/{task_id}/priority",
    response_model=TaskRead,
    summary="Reprioritise a task",
)
async def reprioritise_task(
    task_id: uuid.UUID,
    payload: PriorityUpdate,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskRead:
    """Change a live task's priority; a finished task is a 409."""
    manager = container.get_task_manager(session)
    return TaskRead.model_validate(await manager.reprioritise(task_id, payload.priority))


@router.get(
    "/{task_id}/steps",
    response_model=list[TaskStepRead],
    summary="List a task's steps",
)
async def list_steps(
    task_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> list[TaskStepRead]:
    """Return a task's steps in execution order, or 404 for an unknown task."""
    manager = container.get_task_manager(session)
    steps = await manager.list_steps(task_id)
    return [TaskStepRead.model_validate(step) for step in steps]


@router.post(
    "/{task_id}/steps",
    response_model=TaskStepRead,
    status_code=status.HTTP_201_CREATED,
    summary="Append a step to a task",
)
async def add_step(
    task_id: uuid.UUID,
    payload: StepCreate,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> TaskStepRead:
    """Append one step; ``position`` defaults to the end of the graph."""
    manager = container.get_task_manager(session)
    step = await manager.add_step(
        task_id,
        name=payload.name,
        description=payload.description,
        position=payload.position,
        depends_on=[str(dependency) for dependency in payload.depends_on],
        input_payload=payload.input_payload,
    )
    return TaskStepRead.model_validate(step)


__all__ = [
    "StepCreate",
    "TaskCreate",
    "TaskRead",
    "TaskStepRead",
    "router",
]
