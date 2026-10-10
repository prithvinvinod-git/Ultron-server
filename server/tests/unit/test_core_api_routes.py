"""Route tests for the Core REST surface (T051).

The managers, registries and machine are covered in detail by their own suites;
what these tests answer is the part only the assembled routes can: does the
router the app actually mounts serialise a domain object, does a domain error
become its declared status, and does a malformed id stop at the boundary instead
of reaching a repository.

The container is stubbed at the same seam the T021 auth tests use
(:class:`_StubContainer`), and ``require_principal`` is overridden with a fixed
principal, so no test needs a live database or a real token. The doubles raise
the *real* ``UltronError`` subclasses, so the error mapping asserted here is the
production one and not a test-only shape.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.agents.base import Agent
from app.agents.registry import AgentRegistry
from app.api.dependencies import require_principal
from app.config import reload_settings
from app.core.errors import AgentNotFoundError, TaskNotFoundError
from app.database.models import Agent as AgentRow, Task, TaskStep
from app.database.models.enums import (
    AgentStatus,
    PermissionLevel,
    TaskPriority,
    TaskStatus,
)
from app.main import create_app
from app.security.authentication import Principal
from app.tools.base import Reversibility, Tool
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Test doubles: a spawnable agent type, a registered tool, and stub managers.
# --------------------------------------------------------------------------- #


class EchoAgent(Agent):
    agent_type = "mock.echo"
    description = "A mock agent for route tests."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return dict(context)


class EchoTool(Tool):
    name = "mock.echo"
    description = "Return the arguments unchanged."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return dict(arguments)


_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _task(goal: str = "do a thing", **overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "goal": goal,
        "status": TaskStatus.PENDING,
        "priority": TaskPriority.NORMAL,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return Task(**fields)


def _step(task_id: uuid.UUID, position: int, name: str = "step") -> TaskStep:
    return TaskStep(
        id=uuid.uuid4(),
        task_id=task_id,
        position=position,
        name=name,
        status="pending",
        attempt=0,
        depends_on=[],
    )


class FakeTaskManager:
    """A task manager double that raises the real not-found error."""

    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, Task] = {}
        self.steps: dict[uuid.UUID, list[TaskStep]] = {}

    async def create(self, goal: str, **kwargs: Any) -> Task:
        task = _task(goal=goal, **{k: v for k, v in kwargs.items() if k in {"priority"}})
        self.tasks[task.id] = task
        return task

    async def get(self, task_id: Any) -> Task:
        identifier = uuid.UUID(str(task_id))
        task = self.tasks.get(identifier)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    async def list_all(self, *, limit: int | None = None, offset: int | None = None) -> list[Task]:
        return list(self.tasks.values())

    async def list_by_status(
        self, status: TaskStatus, *, limit: int | None = None, offset: int | None = None
    ) -> list[Task]:
        return [t for t in self.tasks.values() if TaskStatus(t.status) is status]

    async def list_for_project(self, project_id: Any, *, limit: int | None = None) -> list[Task]:
        return list(self.tasks.values())

    async def list_steps(self, task_id: Any, *, limit: int | None = None) -> list[TaskStep]:
        task = await self.get(task_id)
        return self.steps.get(task.id, [])

    async def add_step(
        self,
        task_id: Any,
        *,
        name: str,
        description: str | None = None,
        position: int | None = None,
        depends_on: list[str] | None = None,
        input_payload: Mapping[str, Any] | None = None,
    ) -> TaskStep:
        task = await self.get(task_id)
        appended = self.steps.setdefault(task.id, [])
        step = _step(task.id, position if position is not None else len(appended), name)
        appended.append(step)
        return step

    async def transition(self, task_id: Any, target: TaskStatus, **kwargs: Any) -> Task:
        task = await self.get(task_id)
        task.status = target
        return task

    async def retry(self, task_id: Any, **kwargs: Any) -> Task:
        task = await self.get(task_id)
        task.status = TaskStatus.QUEUED
        task.retry_count += 1
        return task

    async def delete(self, task_id: Any) -> None:
        task = await self.get(task_id)
        self.tasks.pop(task.id, None)


class FakeAgentManager:
    """An agent manager double that raises the real not-found error."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self.agents: dict[uuid.UUID, AgentRow] = {}

    async def create(self, agent_type: str, *, name: str, **kwargs: Any) -> AgentRow:
        agent_cls = self._registry.lookup(agent_type)
        agent = AgentRow(
            id=uuid.uuid4(),
            agent_type=agent_type,
            name=name,
            description=agent_cls.description,
            status=AgentStatus.CREATED,
            tools=[],
            permissions=[],
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.agents[agent.id] = agent
        return agent

    async def inspect(self, agent_id: Any) -> AgentRow:
        identifier = uuid.UUID(str(agent_id))
        agent = self.agents.get(identifier)
        if agent is None:
            raise AgentNotFoundError(str(agent_id))
        return agent

    async def list_all(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[AgentRow]:
        return list(self.agents.values())

    async def list_by_status(
        self, status: AgentStatus, *, limit: int | None = None, offset: int | None = None
    ) -> list[AgentRow]:
        return [a for a in self.agents.values() if AgentStatus(a.status) is status]

    async def cancel(self, agent_id: Any, *, reason: str = "cancelled") -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.status = AgentStatus.CANCELLED
        return agent

    async def pause(self, agent_id: Any) -> AgentRow:
        return await self._move(agent_id, AgentStatus.WAITING)

    async def resume(self, agent_id: Any) -> AgentRow:
        return await self._move(agent_id, AgentStatus.RUNNING)

    async def complete(self, agent_id: Any) -> AgentRow:
        return await self._move(agent_id, AgentStatus.COMPLETED)

    async def fail(self, agent_id: Any, *, error: str) -> AgentRow:
        return await self._move(agent_id, AgentStatus.FAILED)

    async def destroy(self, agent_id: Any) -> None:
        agent = await self.inspect(agent_id)
        self.agents.pop(agent.id, None)

    async def assign_task(self, agent_id: Any, task_id: Any) -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.task_id = uuid.UUID(str(task_id))
        return agent

    async def assign_model(self, agent_id: Any, model: str | None) -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.model = model
        return agent

    async def assign_tools(self, agent_id: Any, tools: list[str]) -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.tools = list(tools)
        return agent

    async def assign_permissions(self, agent_id: Any, permissions: list[str]) -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.permissions = list(permissions)
        return agent

    async def _move(self, agent_id: Any, target: AgentStatus) -> AgentRow:
        agent = await self.inspect(agent_id)
        agent.status = target
        return agent


# --------------------------------------------------------------------------- #
# Stub container and app fixture.
# --------------------------------------------------------------------------- #


class _StubContainer:
    """The slice of the container the Core routes reach for."""

    def __init__(self, agents: AgentRegistry, tools: ToolRegistry) -> None:
        self._agents = agents
        self._tools = tools
        self.task_manager = FakeTaskManager()
        self.agent_manager = FakeAgentManager(agents)

    @property
    def agents(self) -> AgentRegistry:
        return self._agents

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[object]:
        yield object()

    def get_task_manager(self, session: Any) -> FakeTaskManager:
        return self.task_manager

    def get_agent_manager(self, session: Any) -> FakeAgentManager:
        return self.agent_manager


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=uuid.uuid4(),
        username="tester",
        is_superuser=True,
    )


class _Api:
    """The assembled app plus the doubles behind it, for one test."""

    def __init__(self, client: TestClient, container: _StubContainer) -> None:
        self.client = client
        self.container = container


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Api]:
    """The real app, with the container and the principal seam stubbed out.

    The app is built by the real factory so the routers under test are the ones
    the deployment mounts; only the two boundaries a test cannot provide
    (a live database behind the managers, a real token behind the principal)
    are replaced.
    """
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()

    agents = AgentRegistry()
    agents.register(EchoAgent)
    tools = ToolRegistry()
    tools.register(EchoTool())
    container = _StubContainer(agents, tools)

    app = create_app()
    app.state.container = container
    app.dependency_overrides[require_principal] = _principal
    # Deliberately no ``with``: entering the context runs the lifespan, which
    # would ping PostgreSQL and Redis. These tests are about the routing and
    # serialisation layers, neither of which needs a live dependency.
    client = TestClient(app, raise_server_exceptions=False)
    yield _Api(client, container)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# /tasks
# --------------------------------------------------------------------------- #


def test_list_tasks_is_empty_by_default(api: _Api) -> None:
    response = api.client.get("/tasks")
    assert response.status_code == 200
    assert response.json() == []


def test_create_task_returns_201_and_pending(api: _Api) -> None:
    response = api.client.post("/tasks", json={"goal": "write the report"})
    assert response.status_code == 201
    body = response.json()
    assert body["goal"] == "write the report"
    assert body["status"] == "pending"
    assert body["priority"] == "normal"


def test_list_tasks_filters_by_status(api: _Api) -> None:
    api.container.task_manager.tasks[uuid.uuid4()] = _task(status=TaskStatus.PENDING)
    response = api.client.get("/tasks", params={"status": "pending"})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_unknown_task_is_task_not_found(api: _Api) -> None:
    response = api.client.get(f"/tasks/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_malformed_task_id_is_422(api: _Api) -> None:
    response = api.client.get("/tasks/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_create_task_rejects_unknown_field(api: _Api) -> None:
    response = api.client.post("/tasks", json={"goal": "x", "status": "completed"})
    assert response.status_code == 422


def test_cancel_task_moves_it_to_cancelled(api: _Api) -> None:
    created = api.client.post("/tasks", json={"goal": "cancel me"}).json()
    response = api.client.post(f"/tasks/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_retry_task_returns_it_queued(api: _Api) -> None:
    created = api.client.post("/tasks", json={"goal": "retry me"}).json()
    response = api.client.post(f"/tasks/{created['id']}/retry")
    assert response.status_code == 200
    assert response.json()["retry_count"] == 1


def test_delete_task_is_204(api: _Api) -> None:
    created = api.client.post("/tasks", json={"goal": "delete me"}).json()
    response = api.client.delete(f"/tasks/{created['id']}")
    assert response.status_code == 204


def test_add_and_list_steps(api: _Api) -> None:
    created = api.client.post("/tasks", json={"goal": "graph"}).json()
    step = api.client.post(
        f"/tasks/{created['id']}/steps",
        json={"name": "first", "position": 0},
    )
    assert step.status_code == 201
    assert step.json()["position"] == 0

    listed = api.client.get(f"/tasks/{created['id']}/steps")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["first"]


# --------------------------------------------------------------------------- #
# /agents
# --------------------------------------------------------------------------- #


def test_list_agent_types_returns_registered_types(api: _Api) -> None:
    response = api.client.get("/agents/types")
    assert response.status_code == 200
    assert response.json() == [{"agent_type": "mock.echo", "description": EchoAgent.description}]


def test_create_agent_returns_created(api: _Api) -> None:
    response = api.client.post("/agents", json={"agent_type": "mock.echo", "name": "worker"})
    assert response.status_code == 201
    body = response.json()
    assert body["agent_type"] == "mock.echo"
    assert body["status"] == "created"
    assert body["description"] == EchoAgent.description


def test_create_unknown_agent_type_is_404(api: _Api) -> None:
    response = api.client.post("/agents", json={"agent_type": "mock.missing", "name": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_get_unknown_agent_is_404(api: _Api) -> None:
    response = api.client.get(f"/agents/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_cancel_agent(api: _Api) -> None:
    created = api.client.post("/agents", json={"agent_type": "mock.echo", "name": "w"}).json()
    response = api.client.post(f"/agents/{created['id']}/cancel", json={"reason": "done"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_assign_tools_replaces_the_set(api: _Api) -> None:
    created = api.client.post("/agents", json={"agent_type": "mock.echo", "name": "w"}).json()
    response = api.client.post(f"/agents/{created['id']}/tools", json={"tools": ["mock.echo"]})
    assert response.status_code == 200
    assert response.json()["tools"] == ["mock.echo"]


# --------------------------------------------------------------------------- #
# /tools
# --------------------------------------------------------------------------- #


def test_list_tools_returns_the_descriptions(api: _Api) -> None:
    response = api.client.get("/tools")
    assert response.status_code == 200
    assert [tool["name"] for tool in response.json()] == ["mock.echo"]


def test_get_tool(api: _Api) -> None:
    response = api.client.get("/tools/mock.echo")
    assert response.status_code == 200
    assert response.json()["permission_level"] == "0"
    assert response.json()["reversibility"] == "reversible"


def test_unknown_tool_is_404(api: _Api) -> None:
    response = api.client.get("/tools/mock.nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TOOL_NOT_FOUND"


def test_validate_tool_arguments(api: _Api) -> None:
    response = api.client.post("/tools/mock.echo/validate", json={"arguments": {"text": "hi"}})
    assert response.status_code == 200
    assert response.json() == {"valid": True, "tool": "mock.echo", "arguments": {"text": "hi"}}


def test_validate_rejects_bad_arguments(api: _Api) -> None:
    response = api.client.post("/tools/mock.echo/validate", json={"arguments": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TOOL_SCHEMA_INVALID"
