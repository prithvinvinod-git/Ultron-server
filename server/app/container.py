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

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.agents.computer.gateway import NodeGateway
from app.agents.manager import AgentManager
from app.agents.registry import AgentRegistry
from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator
from app.core.planner import Planner
from app.core.router import IntentRouter
from app.database.models.enums import PermissionLevel
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
    NodeRepository,
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
from app.devices.manager import DeviceManager, DeviceStore, NullDeviceStore
from app.devices.mqtt.client import PahoMqttClient, build_mqtt_client
from app.devices.mqtt.transport import MqttDeviceBridge
from app.devices.store import SqlDeviceStore
from app.events.bus import EventBus
from app.events.handlers import PersistEvents
from app.memory import (
    EmbeddingProvider,
    EpisodicMemory,
    LongTermMemory,
    MemoryManager,
    MemoryRecorder,
    ProjectMemory,
    SemanticMemory,
    ShortTermMemory,
    create_embedding_provider,
)
from app.models.base import ModelProvider
from app.models.router import ModelRouter
from app.notifications import NotificationService, build_notification_service
from app.notifications.dispatch import NotificationDispatch, build_schedule_notifier
from app.observability.health import (
    HealthService,
    check_filesystem,
    check_ollama,
    check_postgresql,
    check_redis,
)
from app.observability.load_shed import LoadShedder
from app.observability.logging import get_logger
from app.observability.monitoring import MetricsSubscriber
from app.observability.resources import ResourceMonitor
from app.orb.presenter import OrbPresenter
from app.scheduler.automation import EventAutomation
from app.scheduler.manager import ScheduleManager
from app.scheduler.planning import build_task_factory
from app.tasks.executor import StepRunner, TaskExecutor
from app.tasks.manager import TaskManager
from app.tools.bootstrap import build_tool_registry
from app.tools.browser import BrowserSessionManager
from app.tools.filesystem.sandbox import FilesystemSandbox
from app.tools.registry import ToolRegistry
from app.voice.manager import VoiceManager
from app.workspaces.manager import SubprocessGitRunner

_LOGGER = get_logger(__name__)


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
        sandbox: FilesystemSandbox | None = None,
        git_runner: SubprocessGitRunner | None = None,
        notifications: NotificationService | None = None,
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
        self._sandbox = sandbox
        self._git_runner = git_runner
        self._notifications = notifications
        self._browser_sessions: BrowserSessionManager | None = None
        self._node_gateway: NodeGateway | None = None
        self._device_manager: DeviceManager | None = None
        self._device_store: DeviceStore | None = None
        self._mqtt_client: PahoMqttClient | None = None
        self._mqtt_task: asyncio.Task[None] | None = None
        self._model_router: ModelRouter | None = None
        self._voice: VoiceManager | None = None
        self._embedding_provider: EmbeddingProvider | None = None
        self._memory_recorder: MemoryRecorder | None = None
        self._schedule_manager: ScheduleManager | None = None
        self._event_automation: EventAutomation | None = None
        self._notification_dispatch: NotificationDispatch | None = None
        self._metrics_subscriber: MetricsSubscriber | None = None
        self._orb: OrbPresenter | None = None
        self._resource_monitor: ResourceMonitor | None = None
        self._load_shedder: LoadShedder | None = None
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
    def sandbox(self) -> FilesystemSandbox:
        """Return the filesystem boundary every path-touching tool shares (T090).

        Built from ``WorkspaceSettings`` so §59.4's resolve-before-trust rule has
        a single, container-wide source of allowed roots.
        """
        if self._sandbox is None:
            self._sandbox = FilesystemSandbox(self.settings.workspaces)
        return self._sandbox

    @property
    def git_runner(self) -> SubprocessGitRunner:
        """Return the git runner the git tools share (T092)."""
        if self._git_runner is None:
            self._git_runner = SubprocessGitRunner()
        return self._git_runner

    @property
    def notifications(self) -> NotificationService:
        """Return the notification facade (T096, spec §45)."""
        if self._notifications is None:
            self._notifications = build_notification_service(self.settings.notifications)
        return self._notifications

    @property
    def schedule_manager(self) -> ScheduleManager:
        """Return the §44 scheduler, building it on first access (T170).

        The durable job store is the ``schedules`` table, reached through the
        shared ``session_scope``; the task a firing produces is built by
        :func:`~app.scheduler.planning.build_task_factory` so ``TASK_CREATED``
        travels the executor's normal path; a failed firing is reported through
        §45 via :func:`~app.notifications.dispatch.build_schedule_notifier`. This
        is cheap to build and holds no I/O until a job is due, so it is safe when
        ``SCHEDULER_ENABLED`` is off — the monitor simply never starts.
        """
        if self._schedule_manager is None:
            self._schedule_manager = ScheduleManager(
                settings=self.settings.scheduler,
                session_scope=lambda: session_scope(self.session_factory),
                create_task=build_task_factory(self.events),
                bus=self.events,
                notify=build_schedule_notifier(self.notifications),
            )
        return self._schedule_manager

    @property
    def event_automation(self) -> EventAutomation:
        """Return the event-triggered automation (T171, spec §44)."""
        if self._event_automation is None:
            self._event_automation = EventAutomation(
                scheduler=self.schedule_manager,
                bus=self.events,
            )
        return self._event_automation

    @property
    def notification_dispatch(self) -> NotificationDispatch:
        """Return the Core→provider notification subscriber (T173, spec §45)."""
        if self._notification_dispatch is None:
            self._notification_dispatch = NotificationDispatch(
                service=self.notifications,
                bus=self.events,
            )
        return self._notification_dispatch

    @property
    def orb(self) -> OrbPresenter:
        """Return the orb state presenter (spec §27/§59.13).

        Cheap to build: it holds no I/O until :meth:`OrbPresenter.start`
        subscribes to the bus, so a deployment with no orb renderer pays one
        subscriber and no extra work. Always started, because the console's
        in-page orb exists even when the desktop shell is not running.
        """
        if self._orb is None:
            self._orb = OrbPresenter(self.events)
        return self._orb

    @property
    def metrics_subscriber(self) -> MetricsSubscriber:
        """Return the bus→metrics bridge (T181/T183, spec A§32).

        Cheap to build: it holds no I/O until :meth:`start` subscribes to the
        bus, so a deployment that never enables metrics pays nothing for it.
        """
        if self._metrics_subscriber is None:
            self._metrics_subscriber = MetricsSubscriber(self.events)
        return self._metrics_subscriber

    @property
    def resource_monitor(self) -> ResourceMonitor:
        """Return the host resource monitor (T182, spec A§32).

        Holds no I/O until :meth:`start`; safe to build when resource monitoring
        is disabled (it simply never starts a poll loop).
        """
        if self._resource_monitor is None:
            self._resource_monitor = ResourceMonitor(
                settings=self.settings.observability,
                bus=self.events,
            )
        return self._resource_monitor

    @property
    def load_shedder(self) -> LoadShedder:
        """Return the load-shedding coordinator (§59.25, T271)."""
        if self._load_shedder is None:
            self._load_shedder = LoadShedder(
                bus=self.events,
                settings=self.settings.observability,
                model_provider=self.model_router.get("ollama"),
                schedule_manager=self.schedule_manager,
                max_concurrent_override=self.settings.agents.max_concurrency,
            )
        return self._load_shedder

    @property
    def browser_sessions(self) -> BrowserSessionManager:
        """Return the per-agent browser session manager (T111, spec §11).

        Built from ``BrowserSettings``; the Playwright runtime is created lazily
        by the manager on first use, so this is cheap and safe when the browser
        subsystem is disabled (``BROWSER_ENABLED=false``).
        """
        if self._browser_sessions is None:
            self._browser_sessions = BrowserSessionManager(self.settings.browser)
        return self._browser_sessions

    @property
    def node_gateway(self) -> NodeGateway:
        """Return the computer-node gateway (T141, spec §13/§64).

        Built from ``ComputerNodeSettings``; it holds no I/O of its own until a
        node registers, so this is cheap and safe when the computer-node
        subsystem is disabled (``COMPUTER_NODES_ENABLED=false``). The gateway is
        what the ``computer.*`` tools (T143) dispatch through.
        """
        if self._node_gateway is None:
            self._node_gateway = NodeGateway(
                settings=self.settings.computer_nodes,
                bus=self.events,
                require_auth=False,  # Allow node registration without pre-issued credentials (dev mode)
            )
        return self._node_gateway

    @property
    def device_store(self) -> DeviceStore:
        """Return the durable view of the device layer (T165, spec §26).

        Built on first access around the shared ``session_scope``, so device
        writes join the same unit of work as every other table. The store is
        stateless, so building it early costs nothing; a device only touches the
        database when it registers, heartbeats, goes offline or reports an event.
        """
        if self._device_store is None:
            self._device_store = SqlDeviceStore(
                session_scope=lambda: session_scope(self.session_factory)
            )
        return self._device_store

    @property
    def device_manager(self) -> DeviceManager:
        """Return the device gateway (T161, spec §26).

        Built from ``DeviceSettings``; it holds no I/O of its own until a device
        registers, so this is cheap and safe when the device subsystem is
        disabled (``DEVICES_ENABLED=false``). Devices publish to the same event
        bus as the rest of Core, so ``WAKE_DETECTED`` from a board reaches the
        voice path and ``DEVICE_CONNECTED``/``DEVICE_DISCONNECTED`` the
        presence-driven UI (§19) without extra wiring. When the subsystem is on,
        the manager persists through :attr:`device_store` (T165); when it is off
        it keeps the no-op store, so a disabled subsystem never opens a session.
        """
        if self._device_manager is None:
            self._device_manager = DeviceManager(
                settings=self.settings.devices,
                bus=self.events,
                store=self.device_store if self.settings.devices.enabled else NullDeviceStore(),
            )
        return self._device_manager

    @property
    def tools(self) -> ToolRegistry:
        """Return the registered tools (T035/T097).

        Populated with every default tool family (filesystem, git, terminal,
        python, web, system) at first access; the browser family joins the
        inventory only when ``BROWSER_ENABLED`` is set (T112). Each tool carries
        its declared permission level, so §16 can enforce it. A deployment may
        inject its own registry to override the inventory.
        """
        if self._tools is None:
            self._tools = build_tool_registry(
                self.settings,
                sandbox=self.sandbox,
                git_runner=self.git_runner,
                browser=self.browser_sessions,
                computer=self.node_gateway,
            )
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

            if self.settings.providers.openrouter_configured:
                from app.models.openrouter import OpenRouterProvider

                providers.append(OpenRouterProvider(settings=self.settings.providers))

            # Local free-LLM router: loopback transport, OpenAI wire format. Its
            # upstream keys stay in the router; ULTRON only holds the gateway key.
            if self.settings.providers.freellmapi_configured:
                from app.models.freellmapi import FreeLLMProvider

                providers.append(FreeLLMProvider(settings=self.settings.providers))

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

    @property
    def voice(self) -> VoiceManager:
        """Return the voice manager, building it on first access (T154, §25).

        Voice is another front door onto the same agents and tools (§59.21), so
        the manager is wired with the *shared* model router and tool registry
        through :func:`~app.voice.responder.build_agent_responder`, not with a
        provider of its own. The STT and TTS engines come from their factories,
        which fall back honestly to a null engine (raising a typed 503) when a
        selected engine is missing or unconfigured (§64.11).

        Built lazily because engines are cheap until first use (§25): an
        unconfigured voice stack is discovered at the first utterance, where it
        can be reported, not at process start where it would take the server down.
        """
        if self._voice is None:
            from app.voice.manager import build_voice_manager
            from app.voice.responder import build_agent_responder
            from app.voice.stt import build_stt_engine, create_stt_engine
            from app.voice.tts import build_tts_engine, create_tts_engine

            settings = self.settings.voice
            stt = create_stt_engine(settings)
            tts = create_tts_engine(settings)
            responder = build_agent_responder(
                model_router=self.model_router,
                tools=self.tools,
                events=self.events,
                session_scope=self.session_scope,
                get_audit_log_repository=self.get_audit_log_repository,
                default_permission_level=PermissionLevel(
                    str(self.settings.agent_default_permission_level)
                ),
            )
            self._voice = build_voice_manager(
                settings,
                self.events,
                stt=stt,
                tts=tts,
                responder=responder,
                # A session may name the provider it wants, so the manager can
                # build any engine ULTRON knows about on demand (§25.1).
                stt_factory=lambda name: build_stt_engine(settings, name),
                tts_factory=lambda name: build_tts_engine(settings, name),
            )
        return self._voice

    @property
    def embedding_provider(self) -> EmbeddingProvider:
        """Return the embedding provider for semantic memory (T127).

        Built lazily so a deployment with no provider (``EMBEDDING_PROVIDER=none``)
        still constructs the container, and so the provider is shared by every
        session's memory rather than rebuilt per request. It never connects at
        construction -- the first :meth:`embed` is what talks to the service.
        """
        if self._embedding_provider is None:
            ollama = self.settings.ollama
            providers = self.settings.providers
            auth = (
                f"Bearer {ollama.api_key.get_secret_value()}" if ollama.api_key_configured else None
            )
            self._embedding_provider = create_embedding_provider(
                self.settings.embeddings,
                ollama_base_url=ollama.url,
                ollama_auth_header=auth,
                openai_base_url=providers.openai_base_url,
                openai_api_key=providers.openai_api_key.get_secret_value(),
            )
        return self._embedding_provider

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
        await self._start_memory_recorder()
        await self._start_node_gateway()
        await self._start_device_manager()
        await self._start_scheduler()
        await self._start_observability()
        await self.orb.start()

    async def _start_observability(self) -> None:
        """Start the metrics subscriber, resource monitor and load shedder (T181/T182/T183/T271).

        The metrics subscriber is always started: it only reads the bus and
        writes in-process counters, so it costs one task and no I/O. The
        resource monitor is started only when enabled and only when the host can
        actually be sampled; both conditions are enforced inside
        :meth:`ResourceMonitor.start`, which is a no-op otherwise.
        The load shedder is started only when ``load_shed_enabled`` is true.
        """
        await self.metrics_subscriber.start()
        await self.resource_monitor.start()
        if self.settings.observability.load_shed_enabled:
            await self.load_shedder.start()

    async def _start_scheduler(self) -> None:
        """Start the §44 scheduler, event automation and §45 dispatch (T170-T173).

        No-op when ``SCHEDULER_ENABLED`` is off, so a deployment that schedules
        nothing starts no poll loop and no bus subscriber. The event automation is
        started before the poll loop so an event that arrives during startup is not
        missed; the notification dispatch is independent of both.
        """
        if not self.settings.scheduler.enabled:
            return
        await self.event_automation.start()
        await self.notification_dispatch.start()
        self.schedule_manager.start()

    async def _start_node_gateway(self) -> None:
        """Start the computer-node heartbeat monitor when the subsystem is on (T141).

        No-op when ``COMPUTER_NODES_ENABLED`` is off, so a deployment that hosts
        no nodes never starts the monitor. Only started if the gateway was built
        (it is lazy), so this never forces the object graph into existence.
        """
        if not self.settings.computer_nodes.enabled:
            return
        await self.node_gateway.start()

    async def _start_device_manager(self) -> None:
        """Start the device heartbeat monitor when the subsystem is on (T161).

        No-op when ``DEVICES_ENABLED`` is off, so a deployment that hosts no
        devices never starts the monitor. Only started if the manager was built
        (it is lazy), so this never forces the object graph into existence.
        """
        if not self.settings.devices.enabled:
            return
        await self.device_manager.start()
        if self.settings.devices.mqtt_enabled:
            await self._start_mqtt_bridge()

    async def _start_mqtt_bridge(self) -> None:
        """Start the optional MQTT device bridge when enabled (T163, spec §26).

        The MQTT transport is the second way a device reaches the manager, behind
        the ``iot`` extra. When ``MQTT_ENABLED`` is on but ``paho-mqtt`` is not
        installed, the bridge is skipped with a reason rather than failing
        startup — the WebSocket transport still serves devices (§64.11).
        """
        client = build_mqtt_client(self.settings.devices)
        if client is None:
            _LOGGER.warning(
                "MQTT is enabled but the paho-mqtt client is unavailable; "
                "the device WebSocket transport remains active",
                extra={"event": "device.mqtt.unavailable"},
            )
            return
        await client.start()
        self._mqtt_client = client
        bridge = MqttDeviceBridge(self.device_manager, client)
        self._mqtt_task = asyncio.create_task(bridge.run(), name="device-mqtt-bridge")
        _LOGGER.info(
            "device MQTT bridge started",
            extra={"event": "device.mqtt.started", "topic": bridge.subscription_topic},
        )

    async def _start_memory_recorder(self) -> None:
        """Attach the episodic-memory subscriber when memory is enabled (T128).

        No-op when ``MEMORY_ENABLED`` is off, so a deployment that wants no
        memory pays nothing for the worker. The recorder writes through the same
        ``session_scope`` the persistence subscriber uses, so an episode is
        committed like any other unit of work.
        """
        if not self.settings.memory.enabled:
            return
        self._memory_recorder = MemoryRecorder(
            self.events,
            session_scope=lambda: session_scope(self.session_factory),
            default_namespace=self.settings.memory.default_namespace,
            retention_days=self.settings.memory.episodic_retention_days,
        )
        await self._memory_recorder.start()

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
        if self._schedule_manager is not None:
            await self._schedule_manager.stop()
            self._schedule_manager = None
        if self._event_automation is not None:
            await self._event_automation.stop()
            self._event_automation = None
        if self._notification_dispatch is not None:
            await self._notification_dispatch.stop()
            self._notification_dispatch = None
        if self._memory_recorder is not None:
            await self._memory_recorder.stop()
            self._memory_recorder = None
        if self._persist is not None:
            await self._persist.stop()
            self._persist = None
        if self._node_gateway is not None:
            await self._node_gateway.stop()
            self._node_gateway = None
        if self._mqtt_task is not None:
            self._mqtt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mqtt_task
            self._mqtt_task = None
        if self._mqtt_client is not None:
            await self._mqtt_client.close()
            self._mqtt_client = None
        if self._device_manager is not None:
            await self._device_manager.stop()
            self._device_manager = None
        if self._resource_monitor is not None:
            await self._resource_monitor.stop()
            self._resource_monitor = None
        if self._load_shedder is not None:
            await self._load_shedder.stop()
            self._load_shedder = None
        if self._metrics_subscriber is not None:
            await self._metrics_subscriber.stop()
            self._metrics_subscriber = None
        if self._orb is not None:
            await self._orb.stop()
            self._orb = None
        if self._events is not None:
            self._events.close()
        if self._browser_sessions is not None:
            await self._browser_sessions.shutdown()
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

    def get_node_repository(self, session: AsyncSession) -> NodeRepository:
        return NodeRepository(session)

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

    def get_memory_manager(self, session: AsyncSession) -> MemoryManager:
        """Return the memory facade (T126) bound to the session's unit of work.

        Built from the session's memory repository so every layer shares one
        transaction, and from the container's shared Redis client and embedding
        provider so short-term caching and semantic embedding are instance-wide
        rather than per-request. Like the repositories it never commits — the
        caller owns the unit of work. ``MEMORY_ENABLED=false`` does not change
        construction; it decides whether a caller consults memory at all.
        """
        memory = self.settings.memory
        repository = self.get_memory_repository(session)
        return MemoryManager(
            short_term=ShortTermMemory(
                repository,
                self.redis,
                max_messages=memory.short_term_max_messages,
                ttl=memory.short_term_ttl,
                default_namespace=memory.default_namespace,
            ),
            long_term=LongTermMemory(repository, default_namespace=memory.default_namespace),
            episodic=EpisodicMemory(
                repository,
                retention_days=memory.episodic_retention_days,
                default_namespace=memory.default_namespace,
            ),
            semantic=SemanticMemory(
                repository,
                self.embedding_provider,
                min_similarity=memory.min_similarity,
                enabled=memory.semantic_enabled,
                default_namespace=memory.default_namespace,
            ),
            project=ProjectMemory(repository),
            default_namespace=memory.default_namespace,
            retrieval_top_k=memory.retrieval_top_k,
            min_similarity=memory.min_similarity,
        )

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
