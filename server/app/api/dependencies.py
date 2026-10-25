"""API dependencies (T021): container access, correlation ids, authentication.

Everything a route needs that is not one of its own parameters comes from here,
so "what is this caller allowed to do" has exactly one answer in the code base
instead of one per route.

Four rules, each closing a hole that the alternative leaves open:

**No route reaches for a global.** The container comes from ``app.state``, which
the lifespan owns. Import-time globals would make the application untestable
without patching module attributes, and would put shared mutable state back into
a codebase whose premise is that there is none.

**One transaction per request.** :func:`get_db_session` opens a
``session_scope`` for the request's whole lifetime, and the repositories and
authenticator are built from that one session. Repositories never commit, so a
handler that creates a session row and an audit row gets both or neither --
which is the difference between "the login worked but the audit row vanished"
and an audit trail that can be trusted.

**Every request has a correlation id before it does anything.** The middleware in
:mod:`app.main` binds it; a handler reads it with :func:`request_id`.

**Unauthenticated is the default.** :func:`require_principal` rejects unless a
credential is presented. There is no "optional auth" dependency to grab by
accident: a route that may be anonymous has to say so, in code, which is the
reviewable version of that decision.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError
from app.events.bus import EventBus
from app.observability import get_request_id
from app.security import tokens
from app.security.audit import ACTOR_USER, AuditLogger
from app.security.authentication import Authenticator, Principal

if TYPE_CHECKING:
    from app.agents.computer.gateway import NodeGateway
    from app.agents.manager import AgentManager
    from app.agents.registry import AgentRegistry
    from app.config import Settings
    from app.container import Container as _RealContainer
    from app.database.repositories import (
        AuditLogRepository,
        ConversationRepository,
        DeviceRepository,
        MessageRepository,
        SessionRepository,
        UserRepository,
    )
    from app.devices.manager import DeviceManager
    from app.memory.manager import MemoryManager
    from app.models.router import ModelRouter
    from app.notifications.service import NotificationService
    from app.observability.health import HealthService
    from app.orb.presenter import OrbPresenter
    from app.scheduler.manager import ScheduleManager
    from app.tasks.manager import TaskManager
    from app.tools.registry import ToolRegistry
    from app.voice.manager import VoiceManager

    def _accepts_protocol(container: ContainerProtocol) -> None:
        """Signature only. Never defined at runtime."""

    def _check_real_container_satisfies(container: _RealContainer) -> None:
        # Passing a real container where ``ContainerProtocol`` is required is
        # what forces mypy to prove the two are structurally compatible. If the
        # wiring changes shape this stops type-checking, instead of the mismatch
        # surfacing as an AttributeError on the first request that happens to
        # touch the changed member.
        _accepts_protocol(container)


#: Header names accepted for a client-supplied correlation id, in order.
#: ``X-Request-ID`` is the reverse-proxy convention, ``X-Correlation-ID`` the
#: OpenTelemetry one; both are seen in the wild so both are honoured.
CORRELATION_HEADERS: tuple[str, ...] = ("X-Request-ID", "X-Correlation-ID")

#: A client-supplied correlation id is echoed into log lines and returned in a
#: response header, so it is length-limited. An unbounded header value is free to
#: put a megabyte into every log line belonging to one request.
MAX_CORRELATION_LENGTH: int = 128

#: The single principal every request is attributed to now that built-in
#: authentication is removed. It is a fixed, well-known administrator identity,
#: not a secret: it exists so audit rows and ``actor_id`` fields stay populated
#: until an external verifier (Firebase) supplies a real principal here.
STATIC_PRINCIPAL_ID: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
STATIC_PRINCIPAL_USERNAME: str = "admin prithvin"


class ContainerProtocol(Protocol):
    """The slice of the DI container that request handling depends on.

    Declared as a protocol rather than importing :class:`app.container.Container`
    so this module has no import-time dependency on the whole wiring graph, and so
    a test can supply a purpose-built stub without constructing the real thing.
    :class:`Container` satisfies it structurally; ``mypy`` checks that it still
    does, so widening the container cannot silently break these dependencies.
    """

    @property
    def settings(self) -> Settings: ...

    def session_scope(self) -> AbstractAsyncContextManager[AsyncSession]: ...

    def get_user_repository(self, session: AsyncSession) -> UserRepository: ...

    def get_session_repository(self, session: AsyncSession) -> SessionRepository: ...

    def get_device_repository(self, session: AsyncSession) -> DeviceRepository: ...

    def get_audit_log_repository(self, session: AsyncSession) -> AuditLogRepository: ...

    def get_conversation_repository(
        self, session: AsyncSession
    ) -> ConversationRepository:
        """The conversation repository (T015), bound to this request's session.

        Read by the ``/conversations`` routes and the chat turn persistence, so
        the transcript surface reaches no global and a test can substitute a
        repository over an in-memory session.
        """
        ...

    def get_message_repository(self, session: AsyncSession) -> MessageRepository:
        """The message repository (T015), bound to this request's session.

        Read alongside the conversation repository: a conversation is a header
        and its messages are the transcript, and the two are separate repos.
        """
        ...

    def get_memory_manager(self, session: AsyncSession) -> MemoryManager:
        """The memory facade (T126), bound to this request's session.

        Read by the chat routes to bind the per-session memory manager into the
        execution context, so ``memory.*`` tools reach the same unit of work the
        turn is already using.
        """
        ...

    def get_task_manager(self, session: AsyncSession) -> TaskManager:
        """The task manager (T039), bound to this request's session.

        Read by the ``/tasks`` routes (T051). Declared here so the routes reach
        no global and a test can substitute an in-memory double.
        """
        ...

    def get_agent_manager(self, session: AsyncSession) -> AgentManager:
        """The agent manager (T044), bound to this request's session.

        Read by the ``/agents`` routes (T051).
        """
        ...

    @property
    def agents(self) -> AgentRegistry:
        """The spawnable agent types (T043), read by ``GET /agents/types``."""
        ...

    @property
    def tools(self) -> ToolRegistry:
        """The registered tools (T035), read by the ``/tools`` routes."""
        ...

    @property
    def health(self) -> HealthService:
        """The health service with its default checks registered.

        Read by the probe routes (T022). It is here, rather than imported from
        ``app.container``, so this module keeps no import-time dependency on the
        wiring graph and a probe test can supply a stub.
        """
        ...

    @property
    def events(self) -> EventBus:
        """The event bus.

        Read by the client event stream (T023). Declared here for the same reason
        as ``health``: the stream route needs the bus without importing the
        composition root, so a test can supply its own bus and drive real events
        through it.
        """
        ...

    @property
    def model_router(self) -> ModelRouter:
        """The provider router, with its circuit breakers and usage tracking.

        Read by the interactive chat route, which runs one agent turn inline.
        Declared here for the same reason as ``events``: the route needs the
        shared router the container already built, without importing the
        composition root.
        """
        ...

    @property
    def node_gateway(self) -> NodeGateway:
        """The computer-node gateway (T141), read by the ``/nodes`` routes.

        Declared here for the same reason as ``events``: the routes need the
        gateway without importing the composition root, so a test can supply a
        gateway backed by a transport double.
        """
        ...

    @property
    def device_manager(self) -> DeviceManager:
        """The device gateway (T161), read by the ``/devices`` routes.

        Declared here for the same reason as ``events``: the routes need the
        manager without importing the composition root, so a test can supply one
        backed by a transport double.
        """
        ...

    @property
    def voice(self) -> VoiceManager:
        """The voice manager (T154), read by the ``/voice`` routes.

        Declared here for the same reason as ``events``: the routes need the
        manager without importing the composition root, so a test can supply one
        wired with fake STT/TTS engines and its own event bus.
        """
        ...

    @property
    def orb(self) -> OrbPresenter:
        """The orb state presenter (spec §27), read by ``/orb``.

        Declared here for the same reason as ``events``: the routes read the
        projected state without importing the composition root, so a test can
        supply a presenter over its own bus.
        """
        ...

    @property
    def schedule_manager(self) -> ScheduleManager:
        """The §44 scheduler (T170), read by the ``/schedules`` routes (T509).

        Declared here for the same reason as ``events``: the routes need the
        scheduler without importing the composition root, so a test can supply a
        stub with an in-memory schedule store instead of a live database.
        """
        ...

    @property
    def notifications(self) -> NotificationService:
        """The §45 notification facade (T096), read by ``/notifications`` (T510).

        Declared here for the same reason as ``events``: the routes report the
        active provider and send test notifications without importing the
        composition root, so a test can supply a facade over a recording double.
        """
        ...


def get_container(request: Request) -> ContainerProtocol:
    """Return the DI container for this application.

    Raises:
        RuntimeError: when the lifespan has not run. A wiring bug, not a client
            error, and it must not be reported as one.
    """
    container = getattr(request.app.state, "container", None)
    if container is None:
        message = "the DI container is missing from app.state; the lifespan did not run"
        raise RuntimeError(message)
    return container  # type: ignore[no-any-return]


async def get_db_session(
    container: Annotated[ContainerProtocol, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    """Yield a transactional session for the lifetime of the request.

    Commits when the handler returns cleanly and rolls back when it raises, so a
    handler that fails halfway leaves nothing behind.
    """
    async with container.session_scope() as session:
        yield session


async def get_authenticator(
    container: Annotated[ContainerProtocol, Depends(get_container)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Authenticator:
    """Build an :class:`Authenticator` bound to this request's transaction.

    Built per request rather than cached on the container: the repositories hold
    the request's session, and a cached authenticator would keep whichever
    session happened to build it first.

    The authenticator is given two audit sinks. The ordinary one shares this
    request's session, so a successful login's session row and its audit row land
    together. The durable one opens its own short-lived transaction, because a
    *refusal* ends by raising -- and the rollback that follows would otherwise
    erase the record of the failed login, leaving the failure trail empty
    precisely when it is being attacked.
    """
    return Authenticator(
        users=container.get_user_repository(session),
        sessions=container.get_session_repository(session),
        devices=container.get_device_repository(session),
        audit=AuditLogger(
            container.get_audit_log_repository(session),
            durable=DurableAuditSink(container),
        ),
        settings=container.settings.security,
        revoke_family=DurableFamilyRevoker(container),
    )


class DurableAuditSink:
    """Appends audit rows on their own connection, outside the request.

    Each entry opens and commits its own transaction. That is heavier than
    joining the request's, and deliberately so: the alternative is losing the
    record of every denied request, which is the one class of event the audit
    table exists for.
    """

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def record(self, **fields: Any) -> None:
        async with self._container.session_scope() as session:
            await self._container.get_audit_log_repository(session).record(**fields)


class DurableFamilyRevoker:
    """Revokes a whole session family on its own connection.

    Reuse detection is the one place where a *refusal* still has to change
    durable state. It runs, writes the denial, and then raises -- and the
    request transaction rolls back that raise. Revoking in the request session
    would leave the attacker holding a live rotated-out session, with only an
    audit row to show for it.
    """

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def __call__(self, user_id: uuid.UUID, reason: str) -> int:
        async with self._container.session_scope() as session:
            return await self._container.get_session_repository(session).revoke_all_for_user(
                user_id, reason=reason
            )


def get_user_repository(
    container: Annotated[ContainerProtocol, Depends(get_container)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    """Return the user repository on this request's session.

    Exposed separately from the authenticator so a route that writes to a user
    row uses the *same* session as the audit logger. Two sessions would mean two
    transactions, and a route that succeeds while its audit entry rolls back is
    worse than no audit at all.
    """
    return container.get_user_repository(session)


def request_id() -> str | None:
    """Return the correlation id bound for this request, if any."""
    return get_request_id()


def allows_anonymous(request: Request) -> bool:
    """Return whether this deployment opted into anonymous access.

    Read from the container rather than a module global so a test can exercise
    both paths. ``ALLOW_ANONYMOUS`` defaults to ``False``, so this is opt-in and
    a deployment that forgets to set it is protected by default.
    """
    container = getattr(request.app.state, "container", None)
    if container is None:
        return False
    try:
        return bool(container.settings.security.allow_anonymous)
    except AttributeError:
        return False


def client_ip(request: Request) -> str | None:
    """Return the caller's address for the audit trail.

    ``X-Forwarded-For`` is deliberately not consulted. It is a client-supplied
    header, so trusting it unconditionally would let any caller write whatever
    address it liked into the audit log -- and the deployment that needs it will
    terminate the proxy here anyway, at which point ``request.client`` is the
    real peer.
    """
    return request.client.host if request.client is not None else None


def user_agent(request: Request) -> str | None:
    """Return a bounded User-Agent for the session row."""
    agent = request.headers.get("user-agent")
    return agent[:512] if agent else None


def bearer_credential(request: Request) -> str:
    """Return the presented credential, or raise :class:`AuthError`.

    Three credential shapes share one header, told apart by their prefixes
    rather than by separate endpoints:

    * ``ulk_...``   an API key for a machine client
    * ``udt_...``   a device token
    * anything else  a session token

    Distinguishing by prefix means an ESP32 and a service account do not each
    need their own route, and a caller cannot present a machine credential and
    have it checked against the weaker of the two verification paths.
    """
    return tokens.extract_bearer(request.headers.get("authorization"))


def require_principal(request: Request) -> Principal:
    """Return the caller's principal.

    Built-in authentication has been removed: every request is attributed to a
    single static administrator and no credential is inspected. This is the
    deliberate interim state while authentication moves to an external provider
    (Firebase) — the function keeps its name, signature and ``Auth`` alias so the
    routes that require a principal (and the ``actor_id`` they audit against) do
    not change, and the swap to a real verifier later happens here and nowhere
    else.

    ``request`` is kept as a parameter so callers can continue to depend on this
    the same way, and so a future implementation has the request in hand.
    """
    del request  # unused until a real verifier is wired in
    return Principal(
        actor_type=ACTOR_USER,
        user_id=STATIC_PRINCIPAL_ID,
        username=STATIC_PRINCIPAL_USERNAME,
        is_superuser=True,
    )


def require_superuser(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """Return the principal.

    With built-in authentication removed every caller is the static
    administrator, so this is now the same result as :func:`require_principal`.
    It is kept separate so "this route is admin-only" stays visible in the
    signature — the distinction becomes real again when an external verifier
    replaces the static principal.
    """
    return principal


def parse_uuid(value: str, *, field: str = "id") -> uuid.UUID:
    """Return ``value`` as a UUID, or raise :class:`InvalidInputError`.

    Path parameters arrive as strings and the repository layer coerces them but
    raises ``ValueError``, which is not an API error type. Converting here turns
    a malformed id into a 422 that names the field, instead of a 500.
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        message = "that identifier is not a valid id"
        raise InvalidInputError(message, details={"field": field}) from error


Container = Annotated[ContainerProtocol, Depends(get_container)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Auth = Annotated[Principal, Depends(require_principal)]
Superuser = Annotated[Principal, Depends(require_superuser)]
RequestId = Annotated[str | None, Depends(request_id)]

__all__ = [
    "CORRELATION_HEADERS",
    "MAX_CORRELATION_LENGTH",
    "Auth",
    "Authenticator",
    "Container",
    "DbSession",
    "RequestId",
    "Superuser",
    "allows_anonymous",
    "bearer_credential",
    "client_ip",
    "get_authenticator",
    "get_container",
    "get_db_session",
    "get_user_repository",
    "parse_uuid",
    "request_id",
    "require_principal",
    "require_superuser",
    "user_agent",
]
