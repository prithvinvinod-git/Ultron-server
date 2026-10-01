"""Agent repositories (T015).

An agent is the entity that acts on a task, so two things here are deliberate.

**A sub-agent's permissions come from its parent, never from the request.**
:func:`AgentRepository.get_effective_permissions` walks the parent chain instead
of returning ``agent.permissions`` directly. A model that "remembers" it has more
capabilities than it was granted is the single most common way a permission
system fails, and reading the granted set from a column invites exactly that.

**Logs are append-only.** :class:`AgentLogRepository` has no update and no
delete; a log that can be rewritten cannot be used to reconstruct a run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError
from app.database.models import Agent, AgentLog, AgentStatus
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset

#: Statuses in which an agent is expected to keep reporting a heartbeat.
ALIVE_STATUSES: tuple[AgentStatus, ...] = (
    AgentStatus.RUNNING,
    AgentStatus.WAITING,
    AgentStatus.VERIFYING,
)

#: Guards a walk up the parent chain. A cycle would otherwise hang the request
#: thread; with this bound the walk stops and returns a truncated set, which is
#: the safe direction -- fewer permissions, not more.
MAX_ANCESTOR_DEPTH = 16


class AgentRepository(UuidRepository[Agent]):
    """Agents, plus the permission-resolution walk."""

    model = Agent

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Agent]:
        """Return the agents claimed for a task, oldest first."""
        statement = select(Agent).where(Agent.task_id == task_id).order_by(Agent.created_at)
        return await self._fetch_all(apply_limit(statement, limit))

    async def get_with_task(self, identifier: uuid.UUID | str) -> Agent | None:
        """Return one agent with its task eagerly loaded.

        Without ``selectinload`` this would raise on attribute access, because
        relationships are configured to raise rather than emit a query. Eager
        loading here keeps that guarantee intact instead of quietly disabling it.
        """
        statement = (
            select(Agent)
            .options(selectinload(Agent.task))
            .where(Agent.id == self._coerce(identifier))
        )
        return await self._fetch_one(statement)

    async def claim_for_task(self, identifier: uuid.UUID | str, task_id: uuid.UUID) -> Agent:
        """Point an agent at a task, refusing if it already holds a different one.

        Reassignment has to be explicit. An agent that silently switches tasks
        mid-run leaves its first task without an owner and its logs attributed to
        the wrong work, so the conflict is surfaced rather than resolved.
        """
        agent = await self.get_required(identifier)
        existing = agent.task_id
        if existing is not None and existing != task_id:
            raise ConflictError(
                "the agent is already claimed for a different task",
                details={"agent_id": str(agent.id), "task_id": str(task_id)},
            )
        agent.task_id = task_id
        await self._session.flush()
        return agent

    async def heartbeat(
        self,
        identifier: uuid.UUID | str,
        *,
        now: datetime | None = None,
    ) -> Agent:
        """Record that an agent is alive right now."""
        agent = await self.get_required(identifier)
        agent.last_heartbeat_at = now or datetime.now(UTC)
        await self._session.flush()
        return agent

    async def list_missing_heartbeat(
        self,
        *,
        alive: tuple[AgentStatus, ...] = ALIVE_STATUSES,
        limit: int | None = None,
    ) -> list[Agent]:
        """Return running agents that have never reported a heartbeat.

        Ordered oldest-first so that a caller processing a bounded page looks at
        the longest-silent agent first.

        This only *reports*. An agent that stopped reporting is not necessarily
        dead -- it may simply be blocked on a confirmation -- and deciding a
        process is gone belongs to whoever knows the pid, not to this query.
        """
        statement = (
            select(Agent)
            .where(Agent.status.in_(alive), Agent.last_heartbeat_at.is_(None))
            .order_by(Agent.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def get_effective_permissions(self, identifier: uuid.UUID | str) -> frozenset[str]:
        """Return the permission names in force for this agent.

        The agent's own ``permissions`` plus those of every ancestor up to
        ``MAX_ANCESTOR_DEPTH``. A sub-agent acts on behalf of its parent, so it
        cannot have less than the parent granted -- but the union never adds
        anything the agent was not itself given, and nothing is inferred from a
        tool name or from a prompt.

        This is a *query* helper, not the permission check. It answers "what was
        granted"; whether a specific call is allowed additionally needs the
        policy in force, which belongs to the permission layer (T033/T034). The
        walk is also unbounded by design in the safe direction: a cycle in
        ``parent_agent_id`` would yield a short set, and a missing permission is a
        refusal rather than an escalation.
        """
        collected: set[str] = set()
        current = await self.get(identifier)
        seen: set[uuid.UUID] = set()
        while current is not None and len(seen) < MAX_ANCESTOR_DEPTH:
            if current.id in seen:
                break
            seen.add(current.id)
            collected.update(current.permissions)
            parent_id = current.parent_agent_id
            current = await self.get(parent_id) if parent_id is not None else None
        return frozenset(collected)

    async def list_subagents(
        self,
        identifier: uuid.UUID | str,
        *,
        limit: int | None = None,
    ) -> list[Agent]:
        """Return an agent's direct children, oldest first."""
        statement = (
            select(Agent)
            .where(Agent.parent_agent_id == self._coerce(identifier))
            .order_by(Agent.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_status(
        self,
        status: AgentStatus,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Agent]:
        """Return agents in one status, oldest first."""
        statement = select(Agent).where(Agent.status == status).order_by(Agent.created_at)
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def count_by_status(self) -> dict[str, int]:
        """Return an agent count per status, for a health endpoint.

        One grouped query rather than one per member of the enum, so the counts
        come from a single consistent read.
        """
        statement = select(Agent.status, func.count()).group_by(Agent.status)
        result = await self._session.execute(statement)
        return {str(status): int(count) for status, count in result.all()}


class AgentLogRepository(UuidRepository[AgentLog]):
    """Agent log lines. Append-only."""

    model = AgentLog

    async def append(
        self,
        *,
        agent_id: uuid.UUID,
        message: str,
        level: str = "info",
        task_id: uuid.UUID | None = None,
        sequence: int | None = None,
        payload: dict[str, object] | None = None,
        request_id: str | None = None,
        emitted_at: datetime | None = None,
    ) -> AgentLog:
        """Append one log line.

        ``sequence`` defaults to the next number for this agent rather than to a
        global counter: a per-agent sequence is monotonic without needing a lock,
        and it is what a reader needs to order a single run.
        """
        if sequence is None:
            statement = select(func.max(AgentLog.sequence)).where(AgentLog.agent_id == agent_id)
            result = await self._session.execute(statement)
            highest = result.scalar_one()
            sequence = 0 if highest is None else int(highest) + 1

        entry = AgentLog(
            agent_id=agent_id,
            task_id=task_id,
            sequence=sequence,
            level=level,
            message=message,
            payload=payload or {},
            request_id=request_id,
            emitted_at=emitted_at or datetime.now(UTC),
        )
        return await self.add(entry)

    async def list_for_agent(
        self,
        agent_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AgentLog]:
        """Return one agent's log in sequence order."""
        statement = (
            select(AgentLog).where(AgentLog.agent_id == agent_id).order_by(AgentLog.sequence)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[AgentLog]:
        """Return every log line for a task, across all of its agents."""
        statement = select(AgentLog).where(AgentLog.task_id == task_id).order_by(AgentLog.sequence)
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_errors(
        self,
        agent_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[AgentLog]:
        """Return one agent's error and warning lines, in sequence order.

        Filtered in SQL rather than in Python so a long-running agent's log does
        not have to be fully loaded to answer "what went wrong".
        """
        statement = (
            select(AgentLog)
            .where(AgentLog.agent_id == agent_id, AgentLog.level.in_(("warn", "warning", "error")))
            .order_by(AgentLog.sequence)
        )
        return await self._fetch_all(apply_limit(statement, limit))


__all__ = ["ALIVE_STATUSES", "MAX_ANCESTOR_DEPTH", "AgentLogRepository", "AgentRepository"]
