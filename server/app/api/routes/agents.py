"""Agent routes (T051, spec §7).

The type inventory, the live agent rows, and the five lifecycle operations §7
names. Two decisions are worth stating, because each closes a hole the obvious
alternative opens.

**The type inventory is its own endpoint.** ``GET /agents/types`` reads the
T043 registry and returns what this deployment can spawn. Without it a client
learns the type list only by trying ``POST`` and reading a 404, and an operator
cannot answer "what agents does this box have" without shell access.

**No status is writable by name.** A single ``status`` field would expose the
whole T042 machine as an arbitrary string, including the moves that carry
consequences (admission against the concurrency ceiling, the terminal events).
Instead each operation §7 actually names gets its own route — cancel, pause,
resume, complete, fail — so the allowed moves are the route list, and a route
can be guarded or audited on its own.

**The routes delegate, they do not decide.** Every status write goes through
``AgentManager.transition`` and its machine; no route compares a status to a
literal. A route that "helpfully" allowed a transition the machine refused would
be a second, untested copy of the rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Auth, Container, DbSession
from app.database.models.enums import AgentStatus

router = APIRouter(prefix="/agents", tags=["agents"])

#: Page ceiling, matching the repository's own ``MAX_LIMIT``.
MAX_PAGE = 1000


class AgentCreate(BaseModel):
    """The caller-supplied half of an agent; ``description`` comes from the type.

    ``status`` is absent on purpose: an agent is born ``CREATED`` (the manager
    decides), and the lifecycle is not something a client sets directly.
    """

    model_config = ConfigDict(extra="forbid")

    agent_type: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    task_id: uuid.UUID | None = None
    model: Annotated[str | None, Field(max_length=255)] = None
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    memory_namespace: Annotated[str | None, Field(max_length=128)] = None
    project_id: uuid.UUID | None = None
    parent_agent_id: uuid.UUID | None = None


class AgentRead(BaseModel):
    """One agent row, carrying all twelve §6 attributes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_type: str
    name: str
    description: str | None = None
    status: AgentStatus
    task_id: uuid.UUID | None = None
    model: str | None = None
    tools: list[str]
    permissions: list[str]
    memory_namespace: str | None = None
    project_id: uuid.UUID | None = None
    parent_agent_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AgentTypeRead(BaseModel):
    """One spawnable type: its §6 name and the description declared on the class."""

    agent_type: str
    description: str


class CancelRequest(BaseModel):
    """Optional cancellation reason, carried into the published event."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, Field(min_length=1, max_length=255)] = "cancelled by request"


class FailRequest(BaseModel):
    """A failure must say what failed (the manager refuses an empty message)."""

    model_config = ConfigDict(extra="forbid")

    error: Annotated[str, Field(min_length=1, max_length=2048)]


class AssignTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID


class AssignModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Annotated[str | None, Field(max_length=255)] = None


class AssignToolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[str]


class AssignPermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[str]


@router.get("", response_model=list[AgentRead], summary="List agents")
async def list_agents(
    container: Container,
    session: DbSession,
    principal: Auth,
    agent_status: Annotated[AgentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AgentRead]:
    """Return agents, newest first, optionally filtered to one status."""
    manager = container.get_agent_manager(session)
    if agent_status is not None:
        agents = await manager.list_by_status(agent_status, limit=limit, offset=offset)
    else:
        agents = await manager.list_all(limit=limit, offset=offset)
    return [AgentRead.model_validate(agent) for agent in agents]


@router.post(
    "",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent",
)
async def create_agent(
    payload: AgentCreate,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Spawn a registered agent type in ``CREATED``; an unknown type is a 404."""
    manager = container.get_agent_manager(session)
    agent = await manager.create(
        payload.agent_type,
        name=payload.name,
        task_id=payload.task_id,
        model=payload.model,
        tools=payload.tools,
        permissions=payload.permissions,
        memory_namespace=payload.memory_namespace,
        project_id=payload.project_id,
        parent_agent_id=payload.parent_agent_id,
    )
    return AgentRead.model_validate(agent)


@router.get("/types", response_model=list[AgentTypeRead], summary="List agent types")
async def list_agent_types(container: Container, principal: Auth) -> list[AgentTypeRead]:
    """Return every agent type this deployment can spawn, sorted by name.

    Declared before ``/{agent_id}`` so ``types`` is never read as an id.
    """
    return [
        AgentTypeRead(agent_type=agent_cls.agent_type, description=agent_cls.description)
        for agent_cls in container.agents.list()
    ]


@router.get("/{agent_id}", response_model=AgentRead, summary="Inspect one agent")
async def get_agent(
    agent_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Return one agent's row, or ``404``/``AGENT_NOT_FOUND``."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.inspect(agent_id))


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Destroy a finished agent",
)
async def destroy_agent(
    agent_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> None:
    """Destroy a terminal agent; a live agent must be cancelled first (409)."""
    manager = container.get_agent_manager(session)
    await manager.destroy(agent_id)


@router.post("/{agent_id}/cancel", response_model=AgentRead, summary="Cancel an agent")
async def cancel_agent(
    agent_id: uuid.UUID,
    payload: CancelRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Cancel a live agent from any non-terminal state."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.cancel(agent_id, reason=payload.reason))


@router.post("/{agent_id}/pause", response_model=AgentRead, summary="Pause an agent")
async def pause_agent(
    agent_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Pause a ``RUNNING`` agent into ``WAITING``."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.pause(agent_id))


@router.post("/{agent_id}/resume", response_model=AgentRead, summary="Resume an agent")
async def resume_agent(
    agent_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Return a paused agent to ``RUNNING``."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.resume(agent_id))


@router.post("/{agent_id}/complete", response_model=AgentRead, summary="Complete an agent")
async def complete_agent(
    agent_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Finish a ``VERIFYING`` agent into ``COMPLETED``."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.complete(agent_id))


@router.post("/{agent_id}/fail", response_model=AgentRead, summary="Fail an agent")
async def fail_agent(
    agent_id: uuid.UUID,
    payload: FailRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Fail an agent, recording the reason in the ``AGENT_FAILED`` event."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.fail(agent_id, error=payload.error))


@router.post("/{agent_id}/task", response_model=AgentRead, summary="Assign a task")
async def assign_task(
    agent_id: uuid.UUID,
    payload: AssignTaskRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Point an agent at a task; reassignment to a different task is a 409."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.assign_task(agent_id, payload.task_id))


@router.post("/{agent_id}/model", response_model=AgentRead, summary="Assign a model")
async def assign_model(
    agent_id: uuid.UUID,
    payload: AssignModelRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Set the agent's model capability by name; ``None`` clears it."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.assign_model(agent_id, payload.model))


@router.post("/{agent_id}/tools", response_model=AgentRead, summary="Assign tools")
async def assign_tools(
    agent_id: uuid.UUID,
    payload: AssignToolsRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Replace the agent's tool set; names are validated, not resolved."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.assign_tools(agent_id, payload.tools))


@router.post(
    "/{agent_id}/permissions",
    response_model=AgentRead,
    summary="Assign permissions",
)
async def assign_permissions(
    agent_id: uuid.UUID,
    payload: AssignPermissionsRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> AgentRead:
    """Replace the agent's permission list."""
    manager = container.get_agent_manager(session)
    return AgentRead.model_validate(await manager.assign_permissions(agent_id, payload.permissions))


__all__ = [
    "AgentCreate",
    "AgentRead",
    "AgentTypeRead",
    "AssignModelRequest",
    "AssignPermissionsRequest",
    "AssignTaskRequest",
    "AssignToolsRequest",
    "CancelRequest",
    "FailRequest",
    "router",
]
