"""The Agent ABC and lifecycle state machine (T042).

Spec §40 draws the line this module is careful not to cross: AGENT is a
*goal-oriented worker* — not a model (reasoning), not a tool (capability),
not a task (unit of work), not the core (orchestration). §6 fixes what every
agent has, in that order:

    agent_id, agent_type, name, description, status, task_id, model,
    tools, permissions, memory_namespace, created_at, updated_at

and §6 fixes its lifecycle:

    CREATED → INITIALIZING → READY → RUNNING → WAITING → VERIFYING → COMPLETED

with the failure states FAILED, CANCELLED and TIMEOUT. `AgentStatus` in
`enums.py` already holds that exact vocabulary (ten members, verbatim from
§6, lines 459-494); like `TaskStatus` deferring its transitions to T038, the
enum only fixes the words — **this module is the authority on which moves are
legal.** It is pure: a transition table plus the functions over it, no
persistence and no events. `AgentManager` (T044) writes and publishes the
`AGENT_*` events (§19), and drivers ask *here* first, exactly as the executor
routes `TASK_*` through T038.

The transition table, and the spec each rule answers:

1. **One door from CREATED.** The only forward path is `CREATED →
   INITIALIZING`; creation may still bail to FAILED or CANCELLED without
   initializing, but nothing jumps straight to READY or RUNNING — an agent
   that is "ready" has been built, and §6's sequence is load-bearing.

2. **Ready is an arrival, not a state you start from.** `READY → RUNNING`
   once, and `READY → CANCELLED` (an idle agent may be cancelled).

3. **RUNNING holds nearly everything** — the natural steps (→ WAITING, →
   VERIFYING, → COMPLETED), the failures (→ FAILED), and the interruptions
   (→ CANCELLED, → TIMEOUT). §6's `WAITING` is also §7's *pause* and §19's
   `AGENT_PAUSED`: the lifecycle vocabulary has no PAUSED member, so pausing
   a running agent is recorded as WAITING — it is literally what a paused
   agent is doing — and **WAITING leaves only to RUNNING, the terminal set,
   or CANCELLED/TIMEOUT**. A paused agent resumes through RUNNING (it was
   mid-flight), never silently through READY.

4. **VERIFYING is the last door** (§17's "never assume success", applied to
   agent output): → COMPLETED when the output verifies, → FAILED when it
   does not, and the process can still be cancelled or timed out while
   verifying. There is deliberately no `VERIFYING → RUNNING`: a failed
   verification is a failed attempt, and retrying is the manager spawning a
   fresh agent — history is not rewritten (§66.16).

5. **Terminal states are absolute.** COMPLETED, FAILED, CANCELLED and TIMEOUT
   have **no outgoing edges**. An agent is a one-shot worker; §66.11's retry
   for background agents is the *task* retrying and a new agent admitting it,
   exactly as a retried task is a new admission through the queue. There is
   no `FAILED → READY`: a row that ended and then un-ended would be a
   falsehood with a timestamp.

6. **No self-transitions.** `X → X` is never legal, for the same reason as
   T038's rule 7: a status write that changes nothing is not a transition,
   and "did anything actually happen?" must be answerable in tests and audit
   logs.

**The ABC** mirrors `BaseTool` (T034): the kind of agent is a *declaration*,
not computed state. `agent_type` and `description` are ClassVar declarations
required on every concrete agent (the registry's key and the agent router's
description, §59.21 — and §40 forbids hard-coding the types into the core,
so the declaration must exist *on the agent*). The remaining ten §6
attributes are instance state: identity, the current assignment (task, model,
tools, permissions, memory namespace) and lifecycle bookkeeping
(status, timestamps). Definition-time guards refuse, at import, a concrete
agent that ships undeclared or with a non-coroutine ``run``.

Deliberately absent: persistence (T044 owns the ``agents`` row), event
publication (`AGENT_*` is the manager's job, like TASK_* is the executor's),
permission logic (§66.7: agents declare, the permission engine decides), and
an OpenAI-CLI-style implementation detail (T042 is the contract those
concrete agents — §8's coding agent through §59.22's inventory — implement,
not one of them).
"""

from __future__ import annotations

import inspect
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar, Final

from app.core.errors import ConflictError
from app.database.models import AgentStatus

__all__ = [
    "ACTIVE_STATUSES",
    "AGENT_TERMINAL_STATUSES",
    "LEGAL_AGENT_TRANSITIONS",
    "Agent",
    "agent_can_transition",
    "agent_is_terminal",
    "ensure_legal_agent_transition",
]


#: Every legal move, keyed by where the agent is now. The authority
#: `AgentStatus` defers to; read it as "may go to". See the module docstring
#: for why each set is exactly this size.
LEGAL_AGENT_TRANSITIONS: Final[dict[AgentStatus, frozenset[AgentStatus]]] = {
    AgentStatus.CREATED: frozenset(
        {AgentStatus.INITIALIZING, AgentStatus.FAILED, AgentStatus.CANCELLED}
    ),
    AgentStatus.INITIALIZING: frozenset(
        {AgentStatus.READY, AgentStatus.FAILED, AgentStatus.CANCELLED, AgentStatus.TIMEOUT}
    ),
    AgentStatus.READY: frozenset({AgentStatus.RUNNING, AgentStatus.CANCELLED}),
    AgentStatus.RUNNING: frozenset(
        {
            AgentStatus.WAITING,
            AgentStatus.VERIFYING,
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
            AgentStatus.TIMEOUT,
        }
    ),
    AgentStatus.WAITING: frozenset(
        {AgentStatus.RUNNING, AgentStatus.FAILED, AgentStatus.CANCELLED, AgentStatus.TIMEOUT}
    ),
    AgentStatus.VERIFYING: frozenset(
        {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED, AgentStatus.TIMEOUT}
    ),
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
    AgentStatus.CANCELLED: frozenset(),
    AgentStatus.TIMEOUT: frozenset(),
}

#: The five mid-life statuses — INITIALIZING, READY, RUNNING, WAITING,
#: VERIFYING — exactly the `Agent.is_active` set on the row model. CREATED is
#: neither active nor terminal: it is the state before the lifecycle ran.
ACTIVE_STATUSES: Final[frozenset[AgentStatus]] = frozenset(
    {
        AgentStatus.INITIALIZING,
        AgentStatus.READY,
        AgentStatus.RUNNING,
        AgentStatus.WAITING,
        AgentStatus.VERIFYING,
    }
)

#: Finished, for every caller's `is_finished` test: nothing is running and
#: nothing can restart (§6's failure states, `Agent.is_terminal` on the row).
AGENT_TERMINAL_STATUSES: Final[frozenset[AgentStatus]] = frozenset(
    {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED, AgentStatus.TIMEOUT}
)


def agent_is_terminal(status: AgentStatus) -> bool:
    """Whether the attempt is over — COMPLETED, FAILED, CANCELLED, TIMEOUT."""
    return status in AGENT_TERMINAL_STATUSES


def agent_can_transition(current: AgentStatus, target: AgentStatus) -> bool:
    """Whether `current → target` is legal. Pure; never raises (the table has
    an entry for every one of the ten statuses)."""
    return target in LEGAL_AGENT_TRANSITIONS[current]


def ensure_legal_agent_transition(
    current: AgentStatus,
    target: AgentStatus,
    *,
    agent: str | None = None,
) -> AgentStatus:
    """Return `target` if the move is legal, else `ConflictError` (409).

    The error names where the agent is, where it was asked to go, and every
    place it *may* go — a refusal the caller can act on without re-reading
    this module.
    """
    current = AgentStatus(current)
    target = AgentStatus(target)
    if not agent_can_transition(current, target):
        details: dict[str, str | list[str]] = {
            "current": current.value,
            "target": target.value,
            "legal_targets": sorted(t.value for t in LEGAL_AGENT_TRANSITIONS[current]),
        }
        if agent is not None:
            details["agent"] = agent
        raise ConflictError(
            f"agent cannot move from {current.value} to {target.value}",
            details=details,
        )
    return target


class Agent(ABC):
    """A goal-oriented worker (§40): declared identity, one `run`, a lifecycle."""

    # -- §6 declarations, mandatory on a concrete agent --------------------- #
    agent_type: ClassVar[str]
    """The registry key and router's selector (§59.21). Never hard-coded in
    the core (§40): concrete agents declare it here."""
    description: ClassVar[str]
    """What the agent does — the agent router's description of it (§59.21)."""

    # -- §6 instance state --------------------------------------------------- #
    agent_id: uuid.UUID
    name: str | None
    status: AgentStatus
    task_id: uuid.UUID | None
    model: str | None
    tools: list[str]
    permissions: list[str]
    memory_namespace: str | None
    created_at: datetime
    updated_at: datetime

    def __init__(
        self,
        *,
        agent_id: uuid.UUID | None = None,
        name: str | None = None,
        task_id: uuid.UUID | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        permissions: list[str] | None = None,
        memory_namespace: str | None = None,
    ) -> None:
        """A fresh, in-memory agent with all twelve §6 attributes.

        `agent_id` defaults to a fresh UUID (an id must exist before any
        persistence — the same reason the row model uses one); the manager
        (T044) is what assigns an id/hydrates one from a row. `status` starts
        at CREATED and `created_at`/`updated_at` at construction.
        """
        self.agent_id = agent_id or uuid.uuid4()
        self.name = name
        self.status = AgentStatus.CREATED
        self.task_id = task_id
        self.model = model
        self.tools = list(tools or [])
        self.permissions = list(permissions or [])
        self.memory_namespace = memory_namespace
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at

    # -- The worker ---------------------------------------------------------- #
    @abstractmethod
    async def run(self, context: Mapping[str, Any]) -> Any:
        """Perform the agent's goal on ``context`` — the task's payload.

        Raise a typed error from ``app/core/errors.py`` — `AgentError` (500)
        for a work failure — on failure; the caller (T044/T049) catches,
        records and reports. Returning normally means the work happened;
        calling :meth:`transition` is what records where the worker is on the
        way, and §17's verification is the agent's verifier, not a free claim
        in ``run``.
        """

    # -- Lifecycle ----------------------------------------------------------- #
    def transition(self, target: AgentStatus) -> AgentStatus:
        """Move the agent to `target` if legal, else raise 409.

        The machine is asked first (`ensure_legal_agent_transition`), exactly
        as task transitions are; `self.status` is coerced through
        ``AgentStatus`` first because a row hydrated from the string column
        carries a plain value here too. A refusal changes nothing.
        """
        target = ensure_legal_agent_transition(self.status, target, agent=str(self.agent_id))
        self.status = target
        self.updated_at = datetime.now(UTC)
        return target

    @property
    def is_active(self) -> bool:
        """True while the agent is mid-lifecycle — the §6 diagram's moving
        states, matching `Agent.is_active` on the row model."""
        return AgentStatus(self.status) in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        """True once the agent can no longer change state."""
        return agent_is_terminal(AgentStatus(self.status))

    def __repr__(self) -> str:
        return f"<Agent {self.name or self.agent_type} ({self.agent_type}) {self.status}>"

    # ------------------------------------------------------------------------ #
    # Definition-time guards
    # ------------------------------------------------------------------------ #
    #: Fields §6 requires every concrete agent to declare.
    _REQUIRED_DECLARATIONS: ClassVar[tuple[str, ...]] = ("agent_type", "description")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Fail at import when a concrete agent is mis-declared (§6, §40).

        Skipped for classes that remain abstract: an intermediate ABC is not
        registrable, so its declarations are not yet due. For everything else
        each required declaration must appear somewhere in the class's own
        lineage (``Agent`` itself declares none), so one family base may
        declare on behalf of its family — but no agent may ship *undeclared*.
        """
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return

        lineage = tuple(klass for klass in cls.mro() if klass is not Agent)
        missing = [
            field
            for field in cls._REQUIRED_DECLARATIONS
            if not any(field in klass.__dict__ for klass in lineage)
        ]
        if missing:
            raise TypeError(
                f"{cls.__name__} must declare {', '.join(missing)} "
                "(spec 6: every agent has an agent_type and a description; spec 40: "
                "the type is declared on the agent, never hard-coded in the core)"
            )

        agent_type = getattr(cls, "agent_type", None)
        if not isinstance(agent_type, str) or not agent_type.strip():
            raise TypeError(
                f"{cls.__name__}.agent_type must be a non-empty string "
                "(spec 6: the registry key and router selector)"
            )

        description = getattr(cls, "description", None)
        if not isinstance(description, str) or not description.strip():
            raise TypeError(
                f"{cls.__name__}.description must be a non-empty string "
                "(spec 6: what the agent router reads)"
            )

        if not inspect.iscoroutinefunction(getattr(cls, "run", None)):
            # A sync ``run`` would pass most type checkers (it returns Any)
            # and then explode on the first ``await`` in T044's runner.
            raise TypeError(
                f"{cls.__name__}.run must be an async function "
                "(spec 6: agents execute concurrently, so their work is awaited)"
            )
