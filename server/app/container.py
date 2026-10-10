"""Dependency-injection composition root (T019).

This module builds the objects that make up the application and wires them
together. Nothing here connects at import time: construction is lazy and
requires explicit calls to :meth:`Container.create_engine`, etc., or the
async lifespan methods.

The container holds:

- Application settings (from :mod:`app.config`)
- Async SQLAlchemy engine and session factory
- Redis client
- Health service (with the four available checks registered)
- Event bus (T023), which backs the client event stream
- Repository factory methods

It does not hold any repository instances because they are bound to a
session, which is short-lived. Instead, it provides helpers to create
repository instances given a session.

The container is intended to be a singleton created at startup and held
by the application lifespan (see :mod:`app.main`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.agents.manager import AgentManager
from app.agents.registry import AgentRegistry
from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator
from app.core.planner import Planner
from app.core.router import IntentRouter
from app.database.redis_client import RedisClient, create_redis_client
from app.database.repositories import (
    AgentLogRepository,
    AgentRepository,
    AuditLogRepository,
    ConversationRepository,
    DeviceEventRepository,
    DeviceRepository,
    EventRepository,
    MemoryRepository,
    MessageRepository,
    ModelUsageRepository,
    ProjectRepository,
    Repository,
    ScheduleRepository,
    SessionRepository,
    TaskRepository,
    TaskStepRepository,
    ToolExecutionRepository,
    UserRepository,
    UuidRepository,
)
from app.database.session import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_engine,
    create_session_factory,
    dispose,
    ping,
    read_session_scope,
    session_scope,
)
from app.events.bus import EventBus
from app.events.handlers import PersistEvents
from app.models.base import ModelProvider
from app.models.router import ModelRouter
from app.observability.health import (
    HealthService,
    check_filesystem,
    check_ollama,
    check_postgresql,
    check_redis,
)
from app.tasks.executor import StepRunner, TaskExecutor
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry


class Container:
    """Dependency-injection container.

    Attributes:
        settings: The application settings.
        engine: The SQLAlchemy async engine (built on demand).
        session_factory: The async session factory (built on demand).
        redis: The Redis client (built on demand).
        health: The health service with registered checks.
        events: The in-process event bus backing the client stream (T023).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        engine: AsyncEngine | None = None,
        redis: RedisClient | None = None,
        health: HealthService | None = None,
        events: EventBus | None = None,
        agents: AgentRegistry | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._redis = redis
        self._events = events
        self._persist: PersistEvents | None = None
        self._health = health or HealthService()
        self._agents = agents
        self._tools = tools
        self._model_router: ModelRouter | None = None
        self._register_default_checks()

    # --------------------------------------------------------------------- #
    # Properties
    # --------------------------------------------------------------------- #
    @property
    def settings(self) -> Settings:
        """Return the application settings, loading them if necessary."""
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def engine(self) -> AsyncEngine:
        """Return the SQLAlchemy async engine, building it on first access."""
        if self._engine is None:
            self._engine = create_engine(self.settings.database)
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the async session factory, building it on first access."""
        if self._session_factory is None:
            self._session_factory = create_session_factory(self.engine)
        return self._session_factory

    @property
    def redis(self) -> RedisClient:
        """Return the Redis client, building it on first access."""
        if self._redis is None:
            self._redis = create_redis_client(self.settings.redis)
        return self._redis

    @property
    def agents(self) -> AgentRegistry:
        """Return the spawnable agent types (T043).

        Empty by default: §40 keeps the concrete kinds out of the core, so the
        deployment registers them at startup (T045+). The property builds the
        registry on first access so a test can inject a populated one without
        constructing the whole graph.
        """
        if self._agents is None:
            self._agents = AgentRegistry()
        return self._agents

    @property
    def tools(self) -> ToolRegistry:
        """Return the tool instances (T035).

        Empty by default, for the same §59.22 reason: the registry is the
        inventory a deployment fills, not a catalogue the core invents.
        """
        if self._tools is None:
            self._tools = ToolRegistry()
        return self._tools

    @property
    def health(self) -> HealthService:
        """Return the health service with the default checks registered."""
        return self._health

    @property
    def events(self) -> EventBus:
        """Return the event bus, building it from settings on first access.

        Built lazily for the same reason the engine is: constructing it here
        would read settings at construction time, and the container is created
        before tests have finished applying their environment.
        """
        if self._events is None:
            config = self.settings.events
            self._events = EventBus(
                queue_size=config.queue_size,
                replay_size=config.replay_size,
                max_subscribers=config.max_subscribers,
            )
        return self._events

    @property
    def model_router(self) -> ModelRouter:
        """Return the model router, building it with configured providers on first access.

        The router is created with the container's session factory so it can
        record usage to the database when ``MODEL_USAGE_TRACKING`` is enabled.
        """
        if self._model_router is None:
            router_settings = self.settings.model_router
            providers: list[ModelProvider] = []

            # Local provider (Ollama) — always registered, availability checked lazily
            from app.models.ollama import OllamaProvider

            providers.append(OllamaProvider(settings=self.settings.ollama))

            # Cloud providers — registered only when configured (lazy key check)
            if self.settings.providers.openai_configured:
                from app.models.openai import OpenAIProvider

                providers.append(OpenAIProvider(settings=self.settings.providers))

            if self.settings.providers.gemini_configured:
                from app.models.gemini import GeminiProvider

                providers.append(GeminiProvider(settings=self.settings.providers))

            if self.settings.providers.anthropic_configured:
                from app.models.anthropic import AnthropicProvider

                providers.append(AnthropicProvider(settings=self.settings.providers))

            self._model_router = ModelRouter(
                providers,
                settings=router_settings,
                bus=self.events,
                health_ttl=float(router_settings.health_refresh),
                circuit_threshold=3,
                circuit_cooldown=30.0,
                max_in_flight=None,
                resource_guard=None,
                session_factory=self.session_factory,
            )

        return self._model_router

    # --------------------------------------------------------------------- #
    # Lifespan
    # --------------------------------------------------------------------- #
    async def ping_database(self) -> None:
        """Ping the database to verify connectivity.

        Raises:
            DatabaseError: If the database is unreachable.
        """
        await ping(self.engine)

    async def ping_redis(self) -> None:
        """Ping Redis to verify connectivity.

        Raises:
            RedisError: If Redis is unreachable.
        """
        await self.redis.ping()

    async def startup(self) -> None:
        """Run startup checks: ping database and Redis.

        This is called from the application lifespan startup hook.
        """
        await self.ping_database()
        await self.ping_redis()
        await self._start_persist_handler()

    async def _start_persist_handler(self) -> None:
        """Attach the durable event subscriber when persistence is enabled.

        No-op when ``events_persist`` is off, so a deployment that keeps only
        the live bus (or none) pays nothing for a table it never fills.
        """
        if not self.settings.observability.events_persist:
            return
        self._persist = PersistEvents(
            self.events,
            session_scope=lambda: session_scope(self.session_factory),
        )
        await self._persist.start()

    async def shutdown(self) -> None:
        """Dispose of the engine and close the Redis client.

        This is called from the application lifespan shutdown hook. The event bus
        is closed first so that any open ``/events`` stream wakes up and ends
        instead of hanging until its client gives up.
        """
        if self._persist is not None:
            await self._persist.stop()
            self._persist = None
        if self._events is not None:
            self._events.close()
        await self.redis.aclose()
        await dispose(self.engine)

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Async context manager that matches FastAPI's lifespan protocol.

        Usage:
            @app.on_event("startup")
            async def startup() -> None:
                await container.startup()

            @app.on_event("shutdown")
            async def shutdown() -> None:
                await container.shutdown()

        Or with FastAPI 0.95+:
            @app.router.lifespan_context
            async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
                async with container.lifespan():
                    yield
        """
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()

    # --------------------------------------------------------------------- #
    # Session scopes
    # --------------------------------------------------------------------- #
    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a transactional session.

        Commits on success, rolls back on any failure.
        """
        async with session_scope(self.session_factory) as session:
            yield session

    @asynccontextmanager
    async def read_session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a read-only session that is always rolled back."""
        async with read_session_scope(self.session_factory) as session:
            yield session

    # --------------------------------------------------------------------- #
    # Health check registration
    # --------------------------------------------------------------------- #
    def _register_default_checks(self) -> None:
        """Register the four checks that are available today.

        The other two checks (agent_runtime, event_bus) will be registered
        by their respective subsystems when they are implemented.
        """
        # PostgreSQL check
        self.health.register(
            "postgresql",
            lambda: check_postgresql(self.engine),
        )
        # Redis check
        self.health.register(
            "redis",
            lambda: check_redis(self.redis),
        )
        # Ollama check
        self.health.register(
            "ollama",
            lambda: check_ollama(self.settings.ollama),
        )
        # Filesystem check
        self.health.register(
            "filesystem",
            lambda: check_filesystem([self.settings.workspaces.resolved_root()]),
        )

    # --------------------------------------------------------------------- #
    # Repository factories
    # --------------------------------------------------------------------- #
    def get_repository(
        self,
        repo_type: type[Repository],
        session: AsyncSession,
    ) -> Repository:
        """Return a repository instance of the given type bound to the session.

        Args:
            repo_type: A subclass of :class:`~app.database.repositories.base.Repository`
                or :class:`~app.database.repositories.base.UuidRepository`.
            session: An async SQLAlchemy session.

        Returns:
            An instance of the requested repository type.
        """
        return repo_type(session)

    # Convenience methods for each repository type (optional but handy)
    def get_agent_log_repository(self, session: AsyncSession) -> AgentLogRepository:
        return AgentLogRepository(session)

    def get_agent_repository(self, session: AsyncSession) -> AgentRepository:
        return AgentRepository(session)

    def get_audit_log_repository(self, session: AsyncSession) -> AuditLogRepository:
        return AuditLogRepository(session)

    def get_conversation_repository(self, session: AsyncSession) -> ConversationRepository:
        return ConversationRepository(session)

    def get_device_event_repository(self, session: AsyncSession) -> DeviceEventRepository:
        return DeviceEventRepository(session)

    def get_device_repository(self, session: AsyncSession) -> DeviceRepository:
        return DeviceRepository(session)

    def get_event_repository(self, session: AsyncSession) -> EventRepository:
        return EventRepository(session)

    def get_memory_repository(self, session: AsyncSession) -> MemoryRepository:
        return MemoryRepository(session)

    def get_message_repository(self, session: AsyncSession) -> MessageRepository:
        return MessageRepository(session)

    def get_model_usage_repository(self, session: AsyncSession) -> ModelUsageRepository:
        return ModelUsageRepository(session)

    def get_project_repository(self, session: AsyncSession) -> ProjectRepository:
        return ProjectRepository(session)

    def get_schedule_repository(self, session: AsyncSession) -> ScheduleRepository:
        return ScheduleRepository(session)

    def get_session_repository(self, session: AsyncSession) -> SessionRepository:
        return SessionRepository(session)

    def get_task_repository(self, session: AsyncSession) -> TaskRepository:
        return TaskRepository(session)

    def get_task_step_repository(self, session: AsyncSession) -> TaskStepRepository:
        return TaskStepRepository(session)

    def get_tool_execution_repository(self, session: AsyncSession) -> ToolExecutionRepository:
        return ToolExecutionRepository(session)

    def get_user_repository(self, session: AsyncSession) -> UserRepository:
        return UserRepository(session)

    def get_uuid_repository(self, session: AsyncSession) -> UuidRepository:
        return UuidRepository(session)

    # --------------------------------------------------------------------- #
    # Service factories (session-bound, like the repositories above)
    # --------------------------------------------------------------------- #
    def get_task_manager(self, session: AsyncSession) -> TaskManager:
        """Return the task manager (T039) bound to the session's unit of work.

        A manager rather than a repository because every status write must
        consult the T038 state machine first; the repositories stay the
        query layer underneath it. Like them it never commits — the caller
        owns the unit of work.
        """
        return TaskManager(session)

    def get_agent_manager(self, session: AsyncSession) -> AgentManager:
        """Return the agent manager (T044) bound to the session's unit of work.

        Built from the container's registry, so the manager can only spawn
        types this deployment declared (T043), and the shared bus, so its
        ``AGENT_*`` events reach the same subscribers as everything else. Like
        the repositories it never commits — the caller owns the unit of work.
        """
        return AgentManager(session, agents=self.agents, events=self.events)

    def get_task_executor(
        self,
        session: AsyncSession,
        *,
        run_step: StepRunner,
    ) -> TaskExecutor:
        """Return the task executor (T041) bound to the session's unit of work.

        The `run_step` runner is injected rather than built here: performing a
        step is the agent runtime's job (T042+), and the executor stays pure
        orchestration. The bus is the container's shared one, so the executor's
        `TASK_*` events reach the same subscribers as everything else.
        """
        return TaskExecutor(session, events=self.events, run_step=run_step)

    def get_orchestrator(
        self,
        session: AsyncSession,
        *,
        router: IntentRouter,
        planner: Planner,
        agents: AgentRegistry,
    ) -> Orchestrator:
        """Return the Core orchestrator (T049) bound to the session's unit of work.

        The router, planner and agent registry are passed in rather than built
        here, for the same reason `get_task_executor` takes its `run_step`: the
        routing rules (T047), planning strategies (T048) and agent types (T043)
        are declarations the running app owns — the container wires the pieces,
        it does not invent policy (§40 keeps the kinds on the agents). The bus
        is the container's shared one, so the plan, task and agent events reach
        the same subscribers as everything else.
        """
        return Orchestrator(
            session,
            router=router,
            planner=planner,
            agents=agents,
            events=self.events,
        )
