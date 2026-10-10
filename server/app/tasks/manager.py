"""The task manager (T039): §18's task record as a persistent, validated API.

The spec (section 18) lists twelve fields — `task_id`, `goal`, `status`,
`priority`, `created_at`, `started_at`, `completed_at`, `agent_id`,
`parent_task_id`, `steps`, `result`, `error` — and says only that the system
must keep them. This module is where those fields are *written*, and each one
has a rule worth stating:

*   **`task_id`** — the row's UUID, minted by the model on insert;
    `Task.task_id` is the spec's name for the same identifier.
*   **`goal`** — `create()` refuses an empty one: a task nobody can act on is
    not a record of work, it is a placeholder.
*   **`status`** — `transition()` only. Every status write asks the T038 state
    machine (`ensure_legal_transition`) *before* anything is mutated, then
    applies the side effects below. `retry()` is the same door with §66.11's
    retry bound checked first.
*   **`priority`** — `create()` and `reprioritise()`; reprioritising a
    terminal task is refused (`ensure_legal_priority`) because a finished
    task's priority is the record of what was scheduled (§66.16).
*   **`created_at`** — set by the database on insert (`TimestampMixin`).
*   **`started_at`** — stamped when a task first reaches RUNNING and **never
    rewritten**: a requeue after a crashed worker (RUNNING → QUEUED → RUNNING,
    §18 restart survival) must not lose when work actually began, or every
    duration report becomes fiction. `Task.duration_seconds` therefore means
    *first* start → finish.
*   **`completed_at`** — stamped on entry to **every** terminal state,
    CANCELLED included: "when did this stop" is answerable for cancellations
    too. Cleared again when a finished attempt is retried or replanned, so a
    live row never carries a stale finish time.
*   **`agent_id`** — deliberately **not written here**. The spec's field is
    exposed read-only as `Task.current_agent_id`; assignment is agent-side
    work (T044), because an agent may be paused, resumed and outlive any
    single assignment (the model's docstring carries the full argument).
*   **`parent_task_id`** — set once, at `create()`, and the parent must
    exist (404 otherwise). Reparenting is deliberately absent: moving a
    subtask between parents would silently rewrite a graph's meaning, and
    graph semantics belong to T040.
*   **`steps`** — `add_step()` / `list_steps()`, appending positions and
    requiring sibling ids in `depends_on` to be parseable UUIDs (existence is
    not required: steps may be declared before the siblings they name).
*   **`result` / `error`** — payload rules enforced by `transition()`:
    COMPLETED may carry a result (and a `verification`), FAILED **must**
    carry a non-empty error (truncated to the repository's 4000-char bound),
    and every other target refuses both — a payload meant for a different
    outcome is a caller bug that deserves a 422, not a silent write nobody
    meant.

**Cooperation with the state machine (T038), and what is bypassed.**
`TaskRepository`'s `start`/`complete`/`fail`/`increment_retry` helpers
predate the machine: `start()` admits PENDING → RUNNING directly, which the
machine's rule 1 makes illegal, and their own tests pin that older contract.
The manager therefore **never calls them** — every status change goes through
`transition()`/`retry()`, which ask the machine first. One door, one
authority; the repository keeps its historical helpers for the tests that
pin them. T041's executor must route lifecycle writes through this manager.

**Retry (§66.11).** `retry()` refuses once `retry_count >= max_retries`
(same bound, same refusal as the repository's), counts the attempt, and
re-enters the lifecycle via FAILED → QUEUED — the straight retry, because a
retry re-runs the same plan. FAILED → PENDING (a *replan*) is reachable
through `transition()` for the planner (T048); the bound counts retries, not
plans.

**Leaving a finished attempt.** FAILED → QUEUED/PENDING clears `error`,
`result`, `verification` and `completed_at`: a fresh attempt must not carry
the last one's corpse. The history of *why* it failed lives in the
`TASK_FAILED` event (published by T041) and in `tool_executions` rows, not
in a row that is live again.

**Commit discipline.** Like the repositories, the manager flushes and never
commits or closes — the caller owns the unit of work (`session_scope`).

**Validation boundary.** Ids are parsed here, so a malformed UUID is a 422
with the offending value rather than a 500 from a driver bind (and never a
misleading 404, which would send the caller looking for a row instead of at
its own input). Enums must be enum members: a bare string is refused with
the vocabulary in `details`, the same rule `ToolExecutionRepository.
record_decision` sets for permission levels. Datetimes must be
timezone-aware — PostgreSQL `timestamptz` rejects naive values, and a
driver's IntegrityError is a poor answer to a client mistake. `result`
payloads must be JSON-serialisable at this boundary rather than at the
driver's.

**Deliberately absent.** Events (§19's TASK_CREATED/STARTED/COMPLETED/FAILED
belong to T041's executor — this module has no bus, by design, mirroring
`app/security/permissions.py`); scheduling beyond storing `scheduled_for`
(the scheduler polls `TaskRepository.list_scheduled_due`); agent assignment
(T044); graph semantics (T040); and priority-*rank* dispatch ordering (Phase
2's TaskQueue — `ORDER BY priority` on a StrEnum sorts the string
alphabetically, as the model documents, which is why `list_ready()` orders
by creation time and leaves rank to the queue).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidInputError, TaskNotFoundError
from app.database.models import (
    Task,
    TaskPriority,
    TaskStatus,
    TaskStep,
    VerificationOutcome,
)
from app.database.repositories import TaskRepository, TaskStepRepository
from app.tasks.state import (
    TERMINAL_STATUSES,
    ensure_legal_priority,
    ensure_legal_transition,
    is_terminal,
)

__all__ = ["TaskManager"]

#: One error message must fit the `tasks.error` column's budget — the same
#: bound `TaskRepository.fail` applies, so a manager-written failure and a
#: repository-written one are indistinguishable in the row.
_ERROR_LIMIT = 4000


class TaskManager:
    """Persistent task CRUD whose status writes all consult the T038 machine.

    Constructed from a session, like the repositories: the manager owns
    neither commit nor close (see the module docstring for the field rules).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind to ``session`` and the two repositories it feeds."""
        self._session = session
        self._tasks = TaskRepository(session)
        self._steps = TaskStepRepository(session)

    # ------------------------------------------------------------------ #
    # Create / read
    # ------------------------------------------------------------------ #

    async def create(
        self,
        goal: str,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        parent_task_id: str | uuid.UUID | None = None,
        project_id: str | uuid.UUID | None = None,
        conversation_id: str | uuid.UUID | None = None,
        scheduled_for: datetime | None = None,
        max_retries: int = 3,
    ) -> Task:
        """Create a task in ``PENDING`` and return the flushed row.

        Everything a caller can get wrong is refused before the insert: an
        empty goal, a non-enum priority, a negative ``max_retries`` (retries
        are counted up to this bound, so a negative one would refuse the very
        first), a naive ``scheduled_for``, and a ``parent_task_id`` that
        names no task. The optional ``project_id``/``conversation_id`` are
        parsed but not existence-checked: those columns carry no foreign key
        by design (they are denormalising indexes, not ownership edges).
        """
        if not isinstance(goal, str) or not goal.strip():
            raise InvalidInputError(
                "a task needs a non-empty goal",
                details={"goal": str(goal)[:100]},
            )
        stored_priority = _ensure_priority(priority)
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise InvalidInputError(
                "max_retries must be a non-negative whole number",
                details={"max_retries": str(max_retries)},
            )
        schedule = (
            None if scheduled_for is None else _ensure_aware(scheduled_for, field="scheduled_for")
        )
        parent = (
            None
            if parent_task_id is None
            else _require_uuid(parent_task_id, field="parent_task_id")
        )
        project = None if project_id is None else _require_uuid(project_id, field="project_id")
        conversation = (
            None
            if conversation_id is None
            else _require_uuid(conversation_id, field="conversation_id")
        )
        if parent is not None and await self._tasks.get(parent) is None:
            raise TaskNotFoundError(str(parent))

        task = Task(
            goal=goal,
            status=TaskStatus.PENDING,
            priority=stored_priority,
            parent_task_id=parent,
            project_id=project,
            conversation_id=conversation,
            scheduled_for=schedule,
            max_retries=max_retries,
        )
        return await self._tasks.add(task)

    async def get(self, task_id: str | uuid.UUID) -> Task:
        """Return one task, or ``TaskNotFoundError`` (404, TASK_NOT_FOUND)."""
        task = await self._tasks.get(_require_uuid(task_id, field="task_id"))
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    async def get_with_children(self, task_id: str | uuid.UUID) -> Task:
        """Return a task with steps, agents and tool executions loaded.

        The relationships are ``lazy="raise_on_sql"`` on purpose, so any read
        that needs the §18 `steps` field (or `current_agent_id`) must say so
        up front rather than triggering a hidden query per access.
        """
        task = await self._tasks.get_with_children(_require_uuid(task_id, field="task_id"))
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    async def list_by_status(
        self,
        status: TaskStatus,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Task]:
        """Return tasks in one status, oldest first."""
        return await self._tasks.list_by_status(_ensure_status(status), limit=limit, offset=offset)

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Task]:
        """Return every task, newest first.

        The listing behind ``GET /tasks`` when the caller names no status; a
        filtered read is :meth:`list_by_status`, and the two stay separate so
        "all" is never a status value the enum happens to have.
        """
        return await self._tasks.list_all(limit=limit, offset=offset)

    async def list_for_project(
        self,
        project_id: str | uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Task]:
        """Return a project's tasks, newest first."""
        identifier = _require_uuid(project_id, field="project_id")
        return await self._tasks.list_for_project(identifier, limit=limit)

    async def list_children(
        self,
        task_id: str | uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Task]:
        """Return a task's direct children, oldest first.

        An unknown parent is a 404, not an empty list: a typo in an id
        answered with `[]` reads as "no children yet" and hides the mistake.
        """
        identifier = _require_uuid(task_id, field="task_id")
        if await self._tasks.get(identifier) is None:
            raise TaskNotFoundError(str(task_id))
        return await self._tasks.list_children(identifier, limit=limit)

    async def list_ready(self, *, limit: int | None = None) -> list[Task]:
        """Return QUEUED tasks awaiting admission — the claim view.

        Under the machine only ``QUEUED → RUNNING`` reaches the door (rule 1),
        so a worker choosing its next task must see queued work; this
        deliberately does *not* wrap `TaskRepository.list_claimable`, which
        returns PENDING rows (un-admitted) and orders them by the priority
        *string* — alphabetical, as the `tasks` model documents. Rank
        materialisation is Phase 2's TaskQueue job; until then creation order
        is the honest fallback.
        """
        return await self._tasks.list_by_status(TaskStatus.QUEUED, limit=limit)

    # ------------------------------------------------------------------ #
    # Steps (the §18 `steps` field)
    # ------------------------------------------------------------------ #

    async def add_step(
        self,
        task_id: str | uuid.UUID,
        *,
        name: str,
        description: str | None = None,
        position: int | None = None,
        depends_on: list[str] | None = None,
        input_payload: Mapping[str, Any] | None = None,
    ) -> TaskStep:
        """Append one step to a task, defaulting ``position`` to the end.

        ``depends_on`` entries must parse as UUIDs and are stored in
        canonical form, because the graph executor (T041) compares them
        against `str(step.id)` — a mixed-case id would otherwise look like a
        dependency on some other step. Existence of the named siblings is
        *not* checked: steps may be declared before the ones they depend on.
        """
        identifier = _require_uuid(task_id, field="task_id")
        if await self._tasks.get(identifier) is None:
            raise TaskNotFoundError(str(task_id))
        if not isinstance(name, str) or not name.strip():
            raise InvalidInputError(
                "a step needs a non-empty name", details={"name": str(name)[:100]}
            )
        if description is not None and not isinstance(description, str):
            raise InvalidInputError(
                "step description must be text",
                details={"description": type(description).__name__},
            )
        if position is not None and (isinstance(position, bool) or not isinstance(position, int)):
            raise InvalidInputError(
                "step position must be a whole number",
                details={"position": str(position)},
            )
        dependencies = None
        if depends_on is not None:
            if not isinstance(depends_on, list):
                raise InvalidInputError(
                    "depends_on must be a list of step ids",
                    details={"depends_on": type(depends_on).__name__},
                )
            dependencies = [
                str(_require_uuid(dep, field=f"depends_on[{index}]"))
                for index, dep in enumerate(depends_on)
            ]
        return await self._steps.add_step(
            task_id=identifier,
            name=name,
            description=description,
            position=position,
            depends_on=dependencies,
            input_payload=_ensure_json(input_payload, field="input_payload"),
        )

    async def list_steps(
        self,
        task_id: str | uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[TaskStep]:
        """Return a task's steps in execution order, or 404 for an unknown task."""
        identifier = _require_uuid(task_id, field="task_id")
        if await self._tasks.get(identifier) is None:
            raise TaskNotFoundError(str(task_id))
        return await self._steps.list_for_task(identifier, limit=limit)

    # ------------------------------------------------------------------ #
    # Update: priority and status
    # ------------------------------------------------------------------ #

    async def reprioritise(self, task_id: str | uuid.UUID, priority: TaskPriority) -> Task:
        """Reorder live work in either direction; refuse a finished task.

        A boost and a demotion are equally ordinary for a scheduler, so the
        machine constrains only *which task* may change (live, not terminal),
        never the direction.
        """
        task = await self._fetch(task_id)
        target = _ensure_priority(priority)
        ensure_legal_priority(task.status, target, task=str(task.id))
        task.priority = target
        await self._session.flush()
        return task

    async def transition(
        self,
        task_id: str | uuid.UUID,
        target: TaskStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        verification: VerificationOutcome | None = None,
        now: datetime | None = None,
    ) -> Task:
        """Move a task to ``target``, applying that outcome's field rules.

        Order matters and is deliberate: the request shape is validated
        first (422 — a payload for the wrong outcome is wrong whatever the
        state says), then the machine decides legality (409, naming every
        legal target), and only then is anything mutated. ``now`` defaults to
        the current aware time and exists so tests can pin timestamps.
        """
        task = await self._fetch(task_id)
        return await self._apply(
            task,
            target,
            result=result,
            error=error,
            verification=verification,
            now=now,
        )

    async def retry(
        self,
        task_id: str | uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> Task:
        """Count an attempt and requeue a FAILED task (§66.11's retry bound).

        The refusal order answers the two questions a caller can get wrong
        separately: an illegal source status (409 from the machine — a
        COMPLETED task does not retry) before an exhausted bound (409 with
        ``retry_count``/``max_retries``), and both before any mutation. The
        attempt is counted *after* the transition is written, so a failed
        write cannot leave a half-counted attempt behind in the session.
        """
        task = await self._fetch(task_id)
        ensure_legal_transition(task.status, TaskStatus.QUEUED, task=str(task.id))
        if task.retry_count >= task.max_retries:
            raise ConflictError(
                "the task has exhausted its retries",
                details={
                    "task_id": str(task.id),
                    "retry_count": task.retry_count,
                    "max_retries": task.max_retries,
                },
            )
        moved = await self._apply(task, TaskStatus.QUEUED, now=now)
        moved.retry_count = moved.retry_count + 1
        await self._session.flush()
        return moved

    async def delete(self, task_id: str | uuid.UUID) -> None:
        """Delete a finished task; live work must be cancelled first.

        Refusing to delete a live task is not tidiness: the row is the
        worker's unit of work, and removing it mid-flight would leave the
        worker completing an object nothing can read. Cancel (which the
        machine allows from every live state) ends the attempt first.
        """
        task = await self._fetch(task_id)
        if not is_terminal(task.status):
            raise ConflictError(
                "a live task must be cancelled before it can be deleted",
                details={"task_id": str(task.id), "status": task.status.value},
            )
        await self._tasks.delete(task.id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _fetch(self, task_id: str | uuid.UUID) -> Task:
        """Return the task or raise the 404 every mutating method shares."""
        task = await self._tasks.get(_require_uuid(task_id, field="task_id"))
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    async def _apply(
        self,
        task: Task,
        target: TaskStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        verification: VerificationOutcome | None = None,
        now: datetime | None = None,
    ) -> Task:
        """Validate payload, consult the machine, mutate, flush.

        Split from :meth:`transition` so `retry()` can reuse the same single
        write path with its own precondition order.
        """
        status = _ensure_status(target)
        stamp = datetime.now(UTC) if now is None else _ensure_aware(now, field="now")
        stored_result, stored_error, stored_verification = _payload_for(
            status,
            result=result,
            error=error,
            verification=verification,
        )
        ensure_legal_transition(task.status, status, task=str(task.id))

        if task.status in TERMINAL_STATUSES and status not in TERMINAL_STATUSES:
            # Leaving a finished attempt: the old outcome no longer describes
            # this run (see the module docstring — history lives in events).
            task.error = None
            task.result = None
            task.verification = None
            task.completed_at = None

        if status is TaskStatus.RUNNING:
            task.started_at = task.started_at or stamp  # first start wins
        elif status is TaskStatus.COMPLETED:
            task.completed_at = stamp
            task.result = stored_result
            task.verification = stored_verification
        elif status is TaskStatus.FAILED:
            task.completed_at = stamp
            task.error = stored_error  # non-None: guaranteed by _payload_for
        elif status is TaskStatus.CANCELLED:
            task.completed_at = stamp

        task.status = status
        await self._session.flush()
        return task


# -------------------------------------------------------------------------- #
# Pure argument guards (stateless, so they stay module-level like state.py's)
# -------------------------------------------------------------------------- #


def _require_uuid(value: str | uuid.UUID, *, field: str) -> uuid.UUID:
    """Parse ``value`` as a UUID or refuse it as the client's mistake (422).

    Not 404: a malformed id is input that never reached the data, and
    answering "not found" would send the caller hunting for a row instead of
    at their own typo — the reasoning `UuidRepository._coerce` states, with
    the HTTP class decided here instead of left as a raw ValueError.
    """
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidInputError(
            f"{field} must be a UUID",
            details={field: str(value)[:100]},
        ) from error


def _ensure_aware(value: datetime, *, field: str) -> datetime:
    """Return ``value`` only if it is a timezone-aware datetime (422)."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidInputError(
            f"{field} must be a timezone-aware datetime",
            details={field: str(value)[:100]},
        )
    return value


def _ensure_status(value: TaskStatus) -> TaskStatus:
    """Return ``value`` only if it is a ``TaskStatus`` member (422).

    A bare ``"queued"`` is refused even though the StrEnum would compare
    equal to it: the vocabulary must be stated, the precedent
    `ToolExecutionRepository.record_decision` sets for permission levels.
    """
    if not isinstance(value, TaskStatus):
        raise InvalidInputError(
            "status must be a TaskStatus",
            details={
                "status": str(value)[:100],
                "expected": [member.value for member in TaskStatus],
            },
        )
    return value


def _ensure_priority(value: TaskPriority) -> TaskPriority:
    """Return ``value`` only if it is a ``TaskPriority`` member (422)."""
    if not isinstance(value, TaskPriority):
        raise InvalidInputError(
            "priority must be a TaskPriority",
            details={
                "priority": str(value)[:100],
                "expected": [member.value for member in TaskPriority],
            },
        )
    return value


def _ensure_json(value: Mapping[str, Any] | None, *, field: str) -> dict[str, Any] | None:
    """Validate ``value`` and return a detached copy of what will be stored.

    The copy is made by a JSON round-trip, not ``dict(value)``: a shallow
    copy still shares nested structures, so a caller who kept the dict and
    mutates a nested list afterwards would rewrite a result already recorded
    (test-pinned). The round-trip also *is* the serialisability check, so an
    unserialisable ``result`` fails as a 422 naming the field rather than as
    an opaque driver error halfway through a flush that has already written
    the status change.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidInputError(
            f"{field} must be a JSON object",
            details={field: type(value).__name__},
        )
    try:
        frozen: dict[str, Any] = json.loads(json.dumps(dict(value)))
    except (TypeError, ValueError) as error:
        raise InvalidInputError(
            f"{field} must be JSON-serialisable",
            details={field: type(error).__name__},
        ) from error
    return frozen


def _payload_for(
    status: TaskStatus,
    *,
    result: Mapping[str, Any] | None,
    error: str | None,
    verification: VerificationOutcome | None,
) -> tuple[dict[str, Any] | None, str | None, VerificationOutcome | None]:
    """Decide which outcome fields ``status`` may carry; return them stored.

    Raises 422 before the state machine is consulted: a request that means
    two different things at once is wrong whichever answer the state gives.
    """
    if status is TaskStatus.FAILED:
        if result is not None or verification is not None:
            raise InvalidInputError(
                "a failure records why it failed, not a result",
                details={"target": status.value},
            )
        if not isinstance(error, str) or not error.strip():
            raise InvalidInputError(
                "a failure must record a non-empty error",
                details={"target": status.value},
            )
        return None, error[:_ERROR_LIMIT], None
    if status is TaskStatus.COMPLETED:
        if error is not None:
            raise InvalidInputError(
                "a completion carries a result, not an error; use FAILED",
                details={"target": status.value},
            )
        verification_outcome = None
        if verification is not None:
            if not isinstance(verification, VerificationOutcome):
                raise InvalidInputError(
                    "verification must be a VerificationOutcome",
                    details={
                        "verification": str(verification)[:100],
                        "expected": [member.value for member in VerificationOutcome],
                    },
                )
            verification_outcome = verification
        return _ensure_json(result, field="result"), None, verification_outcome
    if result is not None or error is not None or verification is not None:
        raise InvalidInputError(
            "result, error and verification apply only to COMPLETED or FAILED",
            details={"target": status.value},
        )
    return None, None, None
