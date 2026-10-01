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

from app.config import Settings, get_settings
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
from app.observability.health import (
    HealthService,
    check_filesystem,
    check_ollama,
    check_postgresql,
    check_redis,
)


class Container:
    """Dependency-injection container.

    Attributes:
        settings: The application settings.
        engine: The SQLAlchemy async engine (built on demand).
        session_factory: The async session factory (built on demand).
        redis: The Redis client (built on demand).
        health: The health service with registered checks.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        engine: AsyncEngine | None = None,
        redis: RedisClient | None = None,
        health: HealthService | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._redis = redis
        self._health = health or HealthService()
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
    def health(self) -> HealthService:
        """Return the health service with the default checks registered."""
        return self._health

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

    async def shutdown(self) -> None:
        """Dispose of the engine and close the Redis client.

        This is called from the application lifespan shutdown hook.
        """
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
            lambda: check_filesystem([self.settings.workspace.resolved_root()]),
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
