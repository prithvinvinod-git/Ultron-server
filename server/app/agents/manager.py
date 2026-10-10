"""The agent manager (T044): §7's agent runtime as a persistent, validated API.

§7 lists the capabilities the runtime must have — create, destroy, pause,
resume, cancel, inspect, assign tasks/models/tools/permissions, monitor
health, retrieve status — and demands that many agents run concurrently without
one blocking the server. This module is where those capabilities are
*written*, on top of the pure state machine (T042), the type registry (T043),
the ``agents`` row (§6, T015) and the event bus (§19). It is the agent analog
of `TaskManager` (T039), and it keeps the same two rules that make that module
work: **every status write asks the machine first**, and **the manager owns
neither commit nor close** (flush only; the caller owns the unit of work).

The status vocabulary is `AgentStatus`, the ten members §6 enumerates — but
the *moves* are not the manager's to decide. `transition()` and every §7 door
below it (``pause``/``resume``/``cancel``/``complete``/``fail``) resolve
through `ensure_legal_agent_transition`, so a pause is only WAITING from a
RUNNING agent, a resume targets RUNNING (the WAITING return, and equally READY's
legal admission), a terminal agent never changes, and nothing self-transitions:
the same single authority T038's rule 7 gives tasks. The refusal is a 409 naming
``current``, ``target`` and every ``legal_targets``, with the agent.

**Events (§19, no invented names).** The catalog fixes six ``AGENT_*`` names:
``AGENT_CREATED``, ``AGENT_STARTED``, ``AGENT_PAUSED``, ``AGENT_COMPLETED``,
``AGENT_FAILED``, ``AGENT_STOPPED``. The manager publishes them, persist=True
(they are the durable record of an agent's life), with the identifiers the bus
takes natively and a status payload. The mapping is the machine's own doors,
never guessed: RUNNING → AGENT_STARTED, WAITING → AGENT_PAUSED, COMPLETED →
AGENT_COMPLETED, FAILED → AGENT_FAILED, CANCELLED/TIMEOUT → AGENT_STOPPED.
CREATED, INITIALIZING and READY have *no* §19 name and publish nothing: the
catalog is the boundary, and init/ready are wiring, not milestones worth a
stream event. A bus is optional (constructor argument, like the task executor's
``events``), so a service without streams still gets a complete manager
without a fake.

**Concurrency limit (§7, §66's load-shedding).** §7 demands parallelism; §66
(directed at an 8 GB host) demands shedding in a stated order, including
"reduce concurrent agent fan-out". The manager enforces a configurable ceiling
on the *active* set — exactly `ACTIVE_STATUSES` from T042, the machine's
mid-life states that `Agent.is_active` mirrors — at the moment an agent would
**become** active: CREATED → INITIALIZING, INITIALIZING → READY (stays
active), READY → RUNNING, WAITING → RUNNING. Transitions that do not grow the
active set (a resume from WAITING) pass without another query. At the ceiling,
the refusal is a 409 naming ``active``, ``limit`` and the requested status —
observable, per §59.24, never silent. ``max_concurrent=None`` (the default)
means no ceiling: limit is capacity policy, not a rule §7 fixes a number for.

**Status coercion at the load boundary.** The machine coerces, so a row
hydrated from the ``String`` status column exits `transition()` correctly;
`:meth:`get`/`inspect`` return the raw row, because a read is not a write and
the vocabulary is applied the moment the row is acted on (the executor's rule
for task rows).

**Boundaries, deliberately.** Like §59.6's tools and §7 itself, the manager
validates *names* (a tool/permission name is a contract string), not wiring:
it does not consult the tool registry or the permission engine, because §66.7
says tools declare and the engine decides, and a ``permissions``-list check
here would be a second policy. It does not run the agent's ``run`` (that is
§66's orchestrator, T049, and T045's mock agents). ``updated_at`` is the row
model's ``onupdate``, not a value this module stamps. The task side is
read-only: ``task_id`` is validated to exist (the FK is exercised at the API,
not assumed away), and assignability conflicts surface as 409, never a silent
repoint (the repository's rule, kept).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import (
    ACTIVE_STATUSES,
    agent_is_terminal,
    ensure_legal_agent_transition,
)
from app.agents.registry import AgentRegistry
from app.core.errors import (
    AgentNotFoundError,
    ConflictError,
    InvalidInputError,
    TaskNotFoundError,
)
from app.database.models import Agent as AgentRow, AgentStatus
from app.database.repositories import AgentRepository, TaskRepository
from app.events.bus import EventBus
from app.events.types import EventType

__all__ = ["AgentManager"]

#: The §19 event for each status that has one. CREATED/INITIALIZING/READY have
#: no catalog name and publish nothing — see the module docstring.
_AGENT_EVENTS: Final[dict[AgentStatus, EventType]] = {
    AgentStatus.RUNNING: EventType.AGENT_STARTED,
    AgentStatus.WAITING: EventType.AGENT_PAUSED,
    AgentStatus.COMPLETED: EventType.AGENT_COMPLETED,
    AgentStatus.FAILED: EventType.AGENT_FAILED,
    AgentStatus.CANCELLED: EventType.AGENT_STOPPED,
    AgentStatus.TIMEOUT: EventType.AGENT_STOPPED,
}


class AgentManager:
    """Persistent agent CRUD whose status writes all consult the T042 machine.

    Constructed from a session, the spawnable types (T043), and an optional
    bus. The manager owns neither commit nor close, like the repositories and
    `TaskManager`.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        agents: AgentRegistry,
        events: EventBus | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        """Bind to ``session``, the registry that supplies the types, and a bus.

        ``max_concurrent`` is a ceiling on the number of *active* agents (the
        machine's `ACTIVE_STATUSES`); ``None`` means no ceiling.
        """
        if max_concurrent is not None and (
            isinstance(max_concurrent, bool)
            or not isinstance(max_concurrent, int)
            or max_concurrent < 1
        ):
            raise ValueError("max_concurrent must be a positive whole number or None")
        self._session = session
        self._registry = agents
        self._events = events
        self._max_concurrent = max_concurrent
        self._agents = AgentRepository(session)
        self._tasks = TaskRepository(session)

    # ------------------------------------------------------------------ #
    # Create / read (§7: create agents, inspect, retrieve status)
    # ------------------------------------------------------------------ #

    async def create(
        self,
        agent_type: str,
        *,
        name: str,
        task_id: str | uuid.UUID | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        permissions: list[str] | None = None,
        memory_namespace: str | None = None,
        project_id: str | uuid.UUID | None = None,
        parent_agent_id: str | uuid.UUID | None = None,
    ) -> AgentRow:
        """Create one agent in ``CREATED`` and announce it (`AGENT_CREATED`).

        ``agent_type`` must be a registered type (404 otherwise — the registry
        is the only source of "what agents exist"). The row's ``description``
        comes from the type's own declaration (§6/§40: the kind is declared on
        the agent, never taken from the request). A `task_id`/`parent_agent_id`
        that names nothing is a 404, not a silently dangling pointer; ids that
        do not parse are 422. ``tools``/``permissions`` are copied, so a caller
        mutating its own list after ``create`` cannot rewrite the row. The
        instance starts CREATED — the state before the lifecycle runs, exactly
        where §6 begins — so a concurrency ceiling is checked at *admission*
        (see ``transition``), not here.
        """
        agent_cls = self._registry.lookup(agent_type)
        stored_name = _ensure_name(name)
        stored_model = _ensure_optional_text(model, field="model")
        stored_namespace = _ensure_optional_text(memory_namespace, field="memory_namespace")
        tools_list = _ensure_string_list(tools, field="tools")
        permissions_list = _ensure_string_list(permissions, field="permissions")
        stored_task = None if task_id is None else _require_uuid(task_id, field="task_id")
        stored_project = (
            None if project_id is None else _require_uuid(project_id, field="project_id")
        )
        stored_parent = (
            None
            if parent_agent_id is None
            else _require_uuid(parent_agent_id, field="parent_agent_id")
        )
        if stored_parent is not None and await self._agents.get(stored_parent) is None:
            raise AgentNotFoundError(str(stored_parent))
        if stored_task is not None and await self._tasks.get(stored_task) is None:
            raise TaskNotFoundError(str(stored_task))

        row = AgentRow(
            agent_type=agent_type,
            name=stored_name,
            description=agent_cls.description,
            status=AgentStatus.CREATED,
            task_id=stored_task,
            model=stored_model,
            tools=tools_list,
            permissions=permissions_list,
            memory_namespace=stored_namespace,
            project_id=stored_project,
            parent_agent_id=stored_parent,
        )
        created = await self._agents.add(row)
        await self._publish(
            EventType.AGENT_CREATED,
            {
                "agent_id": str(created.id),
                "agent_type": created.agent_type,
                "name": created.name,
                "status": AgentStatus(created.status).value,
            },
            created,
        )
        return created

    async def get(self, agent_id: str | uuid.UUID) -> AgentRow:
        """Return one agent, or ``AgentNotFoundError`` (404, AGENT_NOT_FOUND)."""
        return await self._fetch(agent_id)

    async def inspect(self, agent_id: str | uuid.UUID) -> AgentRow:
        """§7's inspect: the full ``agents`` row of one agent.

        The row exposes `Agent.is_active`/`Agent.is_terminal` and the twelve §6
        attributes; any status write derived from it must go through
        ``transition`` on return. Identical to :meth:`get` under the hood — the
        two names exist because §7 lists "inspect agents" and "retrieve status"
        as the same read.
        """
        return await self._fetch(agent_id)

    async def list_by_status(
        self,
        status: AgentStatus,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AgentRow]:
        """Return agents in one status, oldest first (the status view)."""
        return await self._agents.list_by_status(_ensure_status(status), limit=limit, offset=offset)

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AgentRow]:
        """Return every agent, newest first (the unfiltered inventory)."""
        return await self._agents.list_all(limit=limit, offset=offset)

    async def count_active(self) -> int:
        """How many agents are mid-lifecycle right now (the active set)."""
        counts = await self._agents.count_by_status()
        return sum(int(counts.get(status.value, 0)) for status in ACTIVE_STATUSES)

    # ------------------------------------------------------------------ #
    # The one status door, and §7's named lifecycle
    # ------------------------------------------------------------------ #

    async def transition(
        self,
        agent_id: str | uuid.UUID,
        target: AgentStatus,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> AgentRow:
        """Move an agent to ``target``, publishing that target's §19 event.

        Order is deliberate and mirrors `TaskManager.transition`: the request
        shape is validated first (422), the machine decides legality (409
        naming every legal target), the concurrency ceiling is checked when the
        move would grow the active set (409 naming ``active``/``limit``), and
        only then is the row mutated. ``details`` merges into the published
        event's payload (a cancellation reason, a failure message), never into
        the row.
        """
        agent = await self._fetch(agent_id)
        status = _ensure_status(target)
        ensure_legal_agent_transition(AgentStatus(agent.status), status, agent=str(agent.id))
        await self._admit_if_becoming_active(agent, status)
        agent.status = status
        await self._session.flush()
        await self._publish_for(agent, status, details=details)
        return agent

    async def pause(self, agent_id: str | uuid.UUID) -> AgentRow:
        """Pause a RUNNING agent: §7's pause is §6's WAITING (T042, rule 3).

        The machine allows WAITING only from RUNNING, so pausing a READY or an
        already-terminal agent is a 409 with the legal targets, not a
        best-effort no-op.
        """
        return await self.transition(agent_id, AgentStatus.WAITING)

    async def resume(self, agent_id: str | uuid.UUID) -> AgentRow:
        """Move an agent to RUNNING, the target §7's resume names.

        WAITING → RUNNING is the return a paused agent makes, and the door
        keeps the machine's single authority: RUNNING is *also* READY's
        admission move, so a not-yet-paused READY agent moving here is the
        machine's legal edge, not a silent shortcut. A resume from a status
        with no RUNNING edge (CREATED, a terminal) is the usual 409. A paused
        agent was mid-flight, so it resumes through RUNNING, never through
        READY. The move stays *inside* the active set, so the concurrency
        ceiling is not consulted.
        """
        return await self.transition(agent_id, AgentStatus.RUNNING)

    async def cancel(
        self,
        agent_id: str | uuid.UUID,
        *,
        reason: str = "cancelled by request",
    ) -> AgentRow:
        """Cancel from any live state (each §6 status's CANCELLED move)."""
        return await self.transition(
            agent_id,
            AgentStatus.CANCELLED,
            details={"reason": reason},
        )

    async def complete(self, agent_id: str | uuid.UUID) -> AgentRow:
        """Finish a VERIFYING agent into COMPLETED (the last door, T042 rule 4)."""
        return await self.transition(agent_id, AgentStatus.COMPLETED)

    async def fail(self, agent_id: str | uuid.UUID, *, error: str) -> AgentRow:
        """Move an agent to FAILED, recording why in the `AGENT_FAILED` event.

        The row carries no error column (§6 lists twelve fields and no error);
        the event is the record of the failure, the same argument that keeps
        task history out of a live row. A blank error is refused (422) — a
        failure must say what failed.
        """
        if not isinstance(error, str) or not error.strip():
            raise InvalidInputError(
                "a failure must record a non-empty message",
                details={"error": str(error)[:100]},
            )
        return await self.transition(
            agent_id,
            AgentStatus.FAILED,
            details={"message": error},
        )

    async def destroy(self, agent_id: str | uuid.UUID) -> None:
        """Destroy a finished agent; live work must be cancelled first.

        The same rule TaskManager.delete keeps: the row is the worker's unit of
        work, and deleting it mid-flight would leave the run completing an
        object nothing can read. Cancel (legal from every live state) ends the
        attempt first. A destroyed agent emits no event: §19 has no destroy
        name, and the terminal event was already published when it stopped.
        """
        agent = await self._fetch(agent_id)
        if not agent_is_terminal(AgentStatus(agent.status)):
            raise ConflictError(
                "a live agent must be cancelled before it can be destroyed",
                details={
                    "agent_id": str(agent.id),
                    "status": AgentStatus(agent.status).value,
                },
            )
        await self._agents.delete(agent.id)

    # ------------------------------------------------------------------ #
    # Assignment (§7: assign tasks, models, tools, permissions)
    # ------------------------------------------------------------------ #

    async def assign_task(
        self,
        agent_id: str | uuid.UUID,
        task_id: str | uuid.UUID,
    ) -> AgentRow:
        """Point an agent at a task, refusing if it already holds a different one.

        Reassignment has to be explicit: an agent silently switching tasks
        leaves its first task without an owner and its logs attributed to the
        wrong work. A task that does not exist is a 404, never a dangling FK
        the driver rejects at flush time.
        """
        identifier = _require_uuid(agent_id, field="agent_id")
        task = _require_uuid(task_id, field="task_id")
        agent = await self._fetch(identifier)
        existing = agent.task_id
        if existing is not None and existing != task:
            raise ConflictError(
                "the agent is already claimed for a different task",
                details={"agent_id": str(agent.id), "task_id": str(task)},
            )
        if await self._tasks.get(task) is None:
            raise TaskNotFoundError(str(task_id))
        agent.task_id = task
        await self._session.flush()
        return agent

    async def assign_model(
        self,
        agent_id: str | uuid.UUID,
        model: str | None,
    ) -> AgentRow:
        """Assign the model capability by *name* (§20: providers are chosen by
        the model router, never wired here). `None` clears the assignment."""
        agent = await self._fetch(agent_id)
        agent.model = _ensure_optional_text(model, field="model")
        await self._session.flush()
        return agent

    async def assign_tools(
        self,
        agent_id: str | uuid.UUID,
        tools: list[str],
    ) -> AgentRow:
        """Replace the agent's tool set. Names are validated, not resolved:
        the tool registry and the permission engine decide at call time
        (§59.6/§66.7)."""
        agent = await self._fetch(agent_id)
        agent.tools = _ensure_string_list(tools, field="tools")
        await self._session.flush()
        return agent

    async def assign_permissions(
        self,
        agent_id: str | uuid.UUID,
        permissions: list[str],
    ) -> AgentRow:
        """Replace the agent's permission set. Name strings, not digits: a bare
        3 in a payload is unreadable (the row model's rule)."""
        agent = await self._fetch(agent_id)
        agent.permissions = _ensure_string_list(permissions, field="permissions")
        await self._session.flush()
        return agent

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _fetch(self, agent_id: str | uuid.UUID) -> AgentRow:
        """Return the row or raise the 404 every mutating method shares."""
        agent = await self._agents.get(_require_uuid(agent_id, field="agent_id"))
        if agent is None:
            raise AgentNotFoundError(str(agent_id))
        return agent

    async def _admit_if_becoming_active(self, agent: AgentRow, target: AgentStatus) -> None:
        """Refuse a move that would grow the active set past the ceiling.

        Called after the machine has blessed the move, so only capacity is
        left to question. A transition that stays inside the active set (a
        resume from WAITING, a skip from INITIALIZING to READY) consumes no
        new slot and never hits this.
        """
        if self._max_concurrent is None:
            return
        current = AgentStatus(agent.status)
        if current in ACTIVE_STATUSES:
            return
        active = await self.count_active()
        if active >= self._max_concurrent:
            raise ConflictError(
                "the concurrent-agent limit is reached",
                details={
                    "agent_id": str(agent.id),
                    "active": active,
                    "limit": self._max_concurrent,
                    "target": target.value,
                },
            )

    async def _publish_for(
        self,
        agent: AgentRow,
        status: AgentStatus,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Publish the §19 event for a status that reached a published state."""
        event = _AGENT_EVENTS.get(status)
        if event is None:
            return
        payload: dict[str, Any] = {
            "agent_id": str(agent.id),
            "agent_type": agent.agent_type,
            "status": status.value,
        }
        if details:
            payload.update(details)
        await self._publish(event, payload, agent)

    async def _publish(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        agent: AgentRow,
    ) -> None:
        """Publish one durable event naming the agent, if a bus is connected."""
        if self._events is None:
            return
        await self._events.publish(
            event_type,
            payload,
            task_id=None if agent.task_id is None else str(agent.task_id),
            agent_id=str(agent.id),
            persist=True,
        )


# -------------------------------------------------------------------------- #
# Pure argument guards (stateless, module-level — the TaskManager split)
# -------------------------------------------------------------------------- #


def _require_uuid(value: str | uuid.UUID, *, field: str) -> uuid.UUID:
    """Parse ``value`` as a UUID or refuse it as the client's mistake (422)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidInputError(
            f"{field} must be a UUID",
            details={field: str(value)[:100]},
        ) from error


def _ensure_status(value: AgentStatus) -> AgentStatus:
    """Return ``value`` only if it is an ``AgentStatus`` member (422).

    A bare ``"running"`` is refused even though the StrEnum would compare
    equal to it: the vocabulary must be stated — the precedent
    `TaskManager._ensure_status` sets, and the same reason the machine is
    asked to interpret *rows*, not request payloads.
    """
    if not isinstance(value, AgentStatus):
        raise InvalidInputError(
            "status must be an AgentStatus",
            details={
                "status": str(value)[:100],
                "expected": [member.value for member in AgentStatus],
            },
        )
    return value


def _ensure_name(value: str) -> str:
    """Return a non-empty name (422) — an agent without a name is not usable."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            "an agent needs a non-empty name",
            details={"name": str(value)[:100]},
        )
    return value


def _ensure_string_list(value: list[str] | None, *, field: str) -> list[str]:
    """Copy ``value`` as a list of non-empty name strings (422 otherwise).

    ``tools``/``permissions`` are contract strings, so each entry is validated
    here; wiring (does the tool exist, may this level be held) stays where it
    is decided. A copy guarantees later caller mutation cannot rewrite the row.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidInputError(
            f"{field} must be a list of names",
            details={field: type(value).__name__},
        )
    cleaned: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise InvalidInputError(
                f"{field}[{index}] must be a non-empty name",
                details={field: str(value)[:200]},
            )
        cleaned.append(entry)
    return cleaned


def _ensure_optional_text(value: str | None, *, field: str) -> str | None:
    """Return ``None`` or a non-empty string (422 otherwise)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"{field} must be text when set",
            details={field: str(value)[:100]},
        )
    return value
