"""Route tests for the authenticated event stream (T023, spec §19/§27).

These tests cover the HTTP surface: that the stream requires a credential, that
its headers survive a real deployment, and that a topic filter is validated
rather than silently ignored.

The stream body is driven two ways, both necessary. ``TestClient`` runs the
ASGI app to completion before it returns anything -- it buffers the whole
response -- so any request that *succeeds* hangs forever against a stream that
never ends; only the finite refusals (401, 422, 503) travel over HTTP. To see
actual frames, and to read the status and headers of an open stream,
:class:`TestStreamGenerator` and ``open_stream`` call the route function
directly and walk its async generator, which is the same object
``StreamingResponse`` would have wrapped.

What is worth pinning here is the set of things that are silently wrong in a way
a manual test would miss: a missing ``X-Accel-Buffering`` header (a stream that
works locally and hangs behind nginx), a filter that accepts a misspelled topic
(a client waiting forever for events that cannot exist), and an unauthenticated
stream (the whole point of the T023 authentication decision).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api.dependencies import CORRELATION_HEADERS, MAX_CORRELATION_LENGTH
from app.api.routes.auth import router as auth_router
from app.api.routes.events import SSE_CONTENT_TYPE, router as events_router, stream_events
from app.config import get_settings
from app.events.bus import (
    CONNECTED_EVENT_TYPE,
    LAG_EVENT_TYPE,
    EventBus,
)
from app.main import RequestContextMiddleware, add_exception_handlers
from app.security.authentication import Principal

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Container stub
# --------------------------------------------------------------------------- #


class StubContainer:
    """The slice of the container the stream route and its auth dependency reach.

    ``events`` is a *real* ``EventBus*, not a mock. The route subscribes, streams
    and unsubscribes against it, and a stub would assert only that the route
    called a stub -- which is not what makes this endpoint trustworthy.

    The repository factories and ``session_scope`` are here because the auth
    dependency pulls them in to resolve a token. They hand back the same doubles
    the auth route tests use and are never touched by the stream itself; leaving
    them off would fail in ``get_db_session`` before the route ran, which would
    say nothing about the stream.
    """

    def __init__(self, repositories: dict[str, Any], bus: EventBus, settings: Any) -> None:
        self._repositories = repositories
        self.events = bus
        self._settings = settings

    @property
    def settings(self) -> Any:
        return self._settings

    def session_scope(self) -> Any:
        """A scope that discards writes, like the real one rolled back."""

        class _Scope:
            async def __aenter__(self) -> Any:
                return object()

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Scope()

    def get_user_repository(self, session: Any = None) -> Any:
        return self._repositories["users"]

    def get_session_repository(self, session: Any = None) -> Any:
        return self._repositories["sessions"]

    def get_device_repository(self, session: Any = None) -> Any:
        return self._repositories["devices"]

    def get_audit_log_repository(self, session: Any = None) -> Any:
        return self._repositories["audit"]


class FakeRequest:
    """Enough of a ``Request`` for the route's two uses of it.

    ``is_disconnected`` is the only method the route calls, and the stream needs
    it to notice a vanished client on a quiet system where no event arrives to
    prompt the check.
    """

    def __init__(self, app: FastAPI, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.headers: dict[str, str] = headers or {}
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=None,
        username="alice",
        is_superuser=False,
    )


async def open_stream(
    app: FastAPI,
    *,
    headers: dict[str, str] | None = None,
    topics: list[str] | None = None,
    last_event_id: int | None = None,
) -> StreamingResponse:
    """Open the stream by driving the route function directly.

    ``TestClient`` cannot do this: its transport runs the ASGI app to
    completion and only then returns the buffered response, so a request that
    succeeds would block forever against a stream that never ends. The finite
    failures (401, 422, 503) still travel over HTTP; everything that opens the
    stream comes through here, against the same code the HTTP layer calls.

    Header names are passed lower-case because ``FakeRequest`` carries a plain
    dict where a real ``Request`` would carry case-insensitive headers.
    """
    request = FakeRequest(app, headers=headers)
    return await stream_events(
        cast(Request, request),
        _principal(),
        topics=topics,
        last_event_id=last_event_id,
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def bus() -> EventBus:
    return EventBus(queue_size=8, replay_size=8)


@pytest.fixture
def settings(security: Any) -> Any:
    """Real settings, with a heartbeat short enough to assert on.

    ``Settings.security`` and ``Settings.events`` are computed properties over
    flat fields, so ``model_copy`` must override the flat name. Updating the
    nested section would be silently discarded -- the footgun T022 recorded as
    C9. That is exactly what this fixture would fall into if written the obvious
    way.
    """
    return get_settings().model_copy(update={"security": security, "event_heartbeat_seconds": 0.05})


@pytest.fixture
def app(
    authenticator: Any,
    settings: Any,
    audit_sink: Any,
    bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """An app with the events router and the real middleware and handlers.

    The exception handlers are required, not incidental: a 401 raised by the auth
    dependency only becomes a 401 because ``add_exception_handlers`` is installed.
    """
    _service, users, sessions, devices = authenticator
    application = FastAPI()
    application.state.container = StubContainer(
        {
            "users": users,
            "sessions": sessions,
            "devices": devices,
            "audit": audit_sink,
        },
        bus,
        settings,
    )
    add_exception_handlers(application)
    application.add_middleware(
        RequestContextMiddleware,
        candidates=CORRELATION_HEADERS,
        max_length=MAX_CORRELATION_LENGTH,
    )
    application.include_router(auth_router)
    application.include_router(events_router)
    return application


@pytest.fixture
def token(client: TestClient, make_user: Any, authenticator: Any) -> str:
    """A real access token for a real account.

    Obtained through the ``/auth/login`` endpoint rather than by calling the
    authenticator directly, so the stream's auth path is the one production uses
    -- including the audit row a login writes. The authenticator fixture is
    requested so the user repository is the one the login route reads.
    """
    _service, users, _sessions, _devices = authenticator
    users.users.append(make_user())

    response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(app: FastAPI) -> Any:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


class TestAuthentication:
    def test_an_unauthenticated_stream_is_refused(self, client: TestClient) -> None:
        """The stream carries transcripts and device state; it is never open.

        This is the whole consequence of the T023 decision. A stream accepting
        anonymous connections would hand every event to anyone who found the URL,
        so the auth check runs before the subscription is created rather than after
        the first frame.
        """
        response = client.get("/events")
        assert response.status_code == 401
        assert "error" in response.json()

    def test_a_malformed_header_is_refused(self, client: TestClient) -> None:
        """Non-empty but unparseable must not be treated as absent."""
        response = client.get("/events", headers={"Authorization": "Token nonsense"})
        assert response.status_code == 401

    def test_an_unknown_token_is_refused(self, client: TestClient) -> None:
        response = client.get(
            "/events", headers={"Authorization": "Bearer ultron-does-not-know-this"}
        )
        assert response.status_code == 401

    def test_a_refused_stream_creates_no_subscription(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """Auth must run before the fan-out registers.

        Registering first and rejecting later would leave a subscription behind
        that keeps receiving events for a caller who never got a stream -- an
        unbounded leak reachable by anyone who can make a bad request.
        """
        assert client.get("/events").status_code == 401
        assert app.state.container.events.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_an_authenticated_stream_opens(self, app: FastAPI) -> None:
        """The happy path, asserted on status and content type only.

        Over HTTP this would hang: ``TestClient`` buffers to completion (see
        ``open_stream``), and this response never completes. That a credential
        is what earns the 200 is proven by the refusals above; here the route's
        own output is under the microscope.
        """
        response = await open_stream(app)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(SSE_CONTENT_TYPE)
        await _body(response).aclose()


# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestStreamingHeaders:
    """Headers that decide whether a stream survives a real deployment.

    Driven through ``open_stream``: the headers are built by the route, which
    is the code under test, and ``TestClient`` cannot return them without first
    running the endless stream to completion.
    """

    async def test_buffering_is_disabled_for_reverse_proxies(self, app: FastAPI) -> None:
        """Without this, nginx holds every frame and the stream looks hung.

        This is the most common way an SSE deployment works locally and fails in
        production: the proxy is not broken, it is doing what it is configured to
        do, and the symptom is silence.
        """
        response = await open_stream(app)
        assert response.headers["x-accel-buffering"] == "no"
        await _body(response).aclose()

    async def test_transform_is_disabled(self, app: FastAPI) -> None:
        """A compressing proxy would have to buffer to build a compressed frame."""
        response = await open_stream(app)
        assert "no-transform" in response.headers["cache-control"]
        await _body(response).aclose()

    async def test_the_response_is_not_cacheable(self, app: FastAPI) -> None:
        response = await open_stream(app)
        assert "no-cache" in response.headers["cache-control"]
        await _body(response).aclose()

    async def test_the_connection_is_kept_alive(self, app: FastAPI) -> None:
        response = await open_stream(app)
        assert response.headers.get("connection") == "keep-alive"
        await _body(response).aclose()


# --------------------------------------------------------------------------- #
# Topic filtering
# --------------------------------------------------------------------------- #


class TestTopicFilter:
    def test_an_unknown_topic_is_a_422(self, client: TestClient, auth: dict[str, str]) -> None:
        """Reject rather than silently drop.

        A client asking for ``agentz`` should get an error, not an open stream that
        never receives anything -- the second failure is indistinguishable from an
        idle system, which is precisely what this codebase is trying to avoid.
        """
        response = client.get("/events", headers=auth, params={"topics": "agentz"})
        assert response.status_code == 422
        assert "agentz" in response.text

    @pytest.mark.asyncio
    async def test_a_known_topic_is_accepted(self, app: FastAPI) -> None:
        response = await open_stream(app, topics=["agent"])
        assert response.status_code == 200
        await _body(response).aclose()

    @pytest.mark.asyncio
    async def test_the_wildcard_is_accepted(self, app: FastAPI) -> None:
        response = await open_stream(app, topics=["*"])
        assert response.status_code == 200
        await _body(response).aclose()

    @pytest.mark.asyncio
    async def test_several_topics_are_accepted(self, app: FastAPI) -> None:
        response = await open_stream(app, topics=["agent", "task"])
        assert response.status_code == 200
        await _body(response).aclose()

    def test_the_error_names_the_known_topics(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        """The client cannot guess the vocabulary; the error is where it learns it."""
        response = client.get("/events", headers=auth, params={"topics": "nonsense"})
        assert "agent" in response.text
        assert "task" in response.text

    def test_an_unknown_topic_creates_no_subscription(
        self, client: TestClient, auth: dict[str, str], app: FastAPI
    ) -> None:
        """Validation happens before the fan-out registers."""
        client.get("/events", headers=auth, params={"topics": "agentz"})
        assert app.state.container.events.subscriber_count == 0


# --------------------------------------------------------------------------- #
# Capacity
# --------------------------------------------------------------------------- #


class TestCapacity:
    def test_a_full_bus_refuses_with_503(self, app: FastAPI, token: str) -> None:
        """Refusing one client beats degrading every already-connected client.

        The alternative -- accepting the subscriber into a queue it will never
        drain -- means the new client's slowness costs the existing ones their
        events.
        """
        full = EventBus(max_subscribers=1)
        full.subscribe()
        app.state.container.events = full

        response = TestClient(app).get("/events", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 503
        assert full.subscriber_count == 1


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #


class TestResume:
    @pytest.mark.asyncio
    async def test_last_event_id_header_is_accepted(self, app: FastAPI) -> None:
        """The standard SSE resume header, honoured without a query parameter."""
        response = await open_stream(app, headers={"last-event-id": "5"})
        assert response.status_code == 200
        await _body(response).aclose()

    @pytest.mark.asyncio
    async def test_the_query_parameter_is_accepted(self, app: FastAPI) -> None:
        """For the fetch() client, which sets headers but rebuilds URLs less often."""
        response = await open_stream(app, last_event_id=5)
        assert response.status_code == 200
        await _body(response).aclose()

    @pytest.mark.asyncio
    async def test_a_non_numeric_header_is_ignored_rather_than_fatal(self, app: FastAPI) -> None:
        """A corrupt resume value should cost a client its backlog, not its stream."""
        response = await open_stream(app, headers={"last-event-id": "not-a-number"})
        assert response.status_code == 200
        await _body(response).aclose()


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #


async def _frames(
    response: Any,
    count: int,
    *,
    between: Any = None,
) -> list[bytes]:
    """Read ``count`` frames off a ``StreamingResponse`` body.

    ``between`` runs before each read after the first, which is where a test
    publishes an event to reach the generator's wait.
    """
    iterator = response.body_iterator
    collected: list[bytes] = []
    for index in range(count):
        if index and between is not None:
            await between()
        collected.append(await iterator.__anext__())
    return collected


def _body(response: Any) -> AsyncGenerator[bytes, None]:
    """The async generator behind a ``StreamingResponse``, typed for walking."""
    return cast(AsyncGenerator[bytes, None], response.body_iterator)


@pytest.mark.asyncio
class TestStreamGenerator:
    """Drive the route function directly; this is where the frames are visible."""

    async def _open(self, app: FastAPI, **kw: Any) -> Any:
        return await open_stream(
            app,
            headers=kw.pop("headers", {}),
            topics=kw.pop("topics", None),
            last_event_id=kw.pop("last_event_id", None),
        )

    async def test_the_stream_opens_with_a_retry_hint(self, app: FastAPI, bus: EventBus) -> None:
        """A native client uses this for every future reconnect.

        The value is deliberately long. A reconnect storm against a struggling
        server makes it worse, so the client is asked to back off further than its
        own instinct would.
        """
        response = await self._open(app)
        first = (await response.body_iterator.__anext__()).decode()
        assert first.startswith("retry: ")
        assert int(first.split(": ", 1)[1]) >= 1000
        await response.body_iterator.aclose()

    async def test_the_second_frame_announces_the_subscription(
        self, app: FastAPI, bus: EventBus
    ) -> None:
        """The handshake: the client learns its topics and resume position.

        Without this the client has to guess whether it is subscribed, and a
        silently-mis-subscribed client looks exactly like an idle system.
        """
        response = await self._open(app, topics=["agent"])
        await response.body_iterator.__anext__()  # retry hint
        connected = (await response.body_iterator.__anext__()).decode()
        assert CONNECTED_EVENT_TYPE in connected
        assert '"topics":["agent"]' in connected
        assert '"heartbeat_seconds"' in connected
        await response.body_iterator.aclose()

    async def test_a_published_event_reaches_the_stream(self, app: FastAPI, bus: EventBus) -> None:
        """The actual purpose of the endpoint."""
        response = await self._open(app)

        async def publish() -> None:
            await bus.publish("AGENT_STARTED", {"agent_id": "a1"})

        frames = await _frames(response, 3, between=publish)
        assert '"AGENT_STARTED"' in frames[2].decode()
        assert '"agent_id":"a1"' in frames[2].decode()
        await response.body_iterator.aclose()

    async def test_a_keepalive_is_emitted_on_a_quiet_stream(
        self, app: FastAPI, bus: EventBus
    ) -> None:
        """Silence must not be mistaken for a dead connection.

        Intermediaries close idle TCP connections, so without a heartbeat a healthy
        stream gets reaped and every client reconnects forever. The frame is a
        comment rather than a no-op event so it does not wake the client's renderer
        or misrepresent activity as work.
        """
        response = await self._open(app)
        await response.body_iterator.__anext__()  # retry hint
        await response.body_iterator.__anext__()  # connected
        keepalive = (await response.body_iterator.__anext__()).decode()
        assert keepalive.startswith(": keepalive")
        await response.body_iterator.aclose()

    async def test_a_disconnect_ends_the_stream(self, app: FastAPI, bus: EventBus) -> None:
        """A vanished client is noticed on the heartbeat tick, not left hanging.

        On a quiet system there may never be a next event, so waiting on the queue
        alone would keep the subscription — and its queue — alive indefinitely.
        """
        request = FakeRequest(app)
        response = await stream_events(
            cast(Request, request), _principal(), topics=None, last_event_id=None
        )
        iterator = _body(response)
        await iterator.__anext__()
        await iterator.__anext__()
        request.disconnected = True

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), timeout=2)

    async def test_closing_the_response_releases_the_subscription(
        self, app: FastAPI, bus: EventBus
    ) -> None:
        """A leaked subscription keeps receiving events for a gone client forever.

        Each holds a bounded queue, so this is a slow memory leak rather than a
        crash -- exactly the kind that no single-connection test would notice.

        The generator has to be started before it is closed: ``aclose`` on a
        generator that has not run yet skips its ``finally`` block entirely, which
        would make this test pass against a route that leaks.
        """
        response = await self._open(app)
        assert bus.subscriber_count == 1
        await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        assert bus.subscriber_count == 0

    async def test_resume_replays_missed_events(self, app: FastAPI, bus: EventBus) -> None:
        """A reconnecting client gets what it missed, without a gap it cannot see."""
        await bus.publish("TASK_CREATED", {"i": 1})
        await bus.publish("TASK_STARTED", {"i": 2})

        response = await self._open(app, last_event_id=1)
        await response.body_iterator.__anext__()  # retry hint
        await response.body_iterator.__anext__()  # connected
        replayed = (await response.body_iterator.__anext__()).decode()
        assert '"TASK_STARTED"' in replayed
        await response.body_iterator.aclose()

    async def test_a_lag_signal_reaches_the_client(self, app: FastAPI, bus: EventBus) -> None:
        """When replay cannot cover the gap, the client is told to resync.

        A resume that quietly skips events is indistinguishable from a working
        stream, and §60.4 is explicit that a dropped connection must never read as
        an idle system.
        """
        small = EventBus(replay_size=2)
        app.state.container.events = small
        for index in range(8):
            await small.publish("TASK_CREATED", {"i": index})

        response = await self._open(app, last_event_id=1)
        await response.body_iterator.__anext__()
        await response.body_iterator.__anext__()
        lagged = (await response.body_iterator.__anext__()).decode()
        assert LAG_EVENT_TYPE in lagged
        assert '"action":"resync"' in lagged
        await response.body_iterator.aclose()

    async def test_delivered_ids_strictly_increase(self, app: FastAPI, bus: EventBus) -> None:
        """Ordering is the property a client uses to spot a gap or a reorder.

        The fixture's queue is large enough that nothing is dropped, so this
        isolates ordering from backpressure, which is covered in the bus tests.
        """
        response = await self._open(app)
        for index in range(4):
            await bus.publish("TASK_CREATED", {"i": index})

        await response.body_iterator.__anext__()  # retry hint
        await response.body_iterator.__anext__()  # connected
        seen = [(await response.body_iterator.__anext__()).decode() for _ in range(4)]

        ids = [int(text.split("id: ", 1)[1].split("\n", 1)[0]) for text in seen]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids), "no id may be delivered twice"
        await response.body_iterator.aclose()
