"""End-to-end smoke test for the assembled application (T024, spec §53).

Every other test file exercises one subsystem. This one exercises the thing that
only exists when they are all wired together: the routes the app actually
mounts, the shape of an error crossing the boundary, and the behaviour of the
middleware on requests that never reach a route at all.

It is deliberately shallow. A smoke test's job is to fail loudly and early when
the wiring is wrong -- a route that vanished, a response model that no longer
serialises, an exception handler that stopped matching -- not to re-verify
behaviour that the subsystem tests already cover in detail.

Three things are checked that no unit file can check on its own.

**Every mounted route is reachable.** ``main._include_routers`` mounts
explicitly, and an explicit list can silently lose an entry. This walks the real
routing table rather than a hardcoded copy of it, so a forgotten mount fails
here instead of in production.

**The OpenAPI schema generates.** A response model referencing an unserialisable
type raises at schema-build time, not at request time. Asking for the schema is
the cheapest way to find that.

**Requests that never reach a route still behave.** A 404 from an unmatched path
and a 405 from a wrong method both have to produce the documented error envelope
rather than Starlette's bare ``{"detail": ...}``, or every client-side error
handler has to special-case them.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import reload_settings
from app.container import Container
from app.main import create_app

pytestmark = pytest.mark.unit


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real application, built through the real factory.

    ``ALLOWED_HOSTS`` has to name ``testserver``, the host ``TestClient`` sends.
    The default is an empty allowlist and ``TrustedHostMiddleware`` fails closed
    against it, so without this every request would be a ``400`` and the whole
    file would pass while testing nothing.

    ``METRICS_ENABLED`` is pinned off so the file does not depend on ambient
    configuration, and so ``/metrics`` stays cheap: enabling it makes the route
    run the health engine, which means a multi-second attempt to reach a real
    PostgreSQL and Redis.

    ``create_app`` opens no socket and no database connection; the container's
    lifespan is what would, and ``TestClient`` without a context manager does not
    run it. That is what lets the whole route table be walked with nothing
    running.
    """
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Any:
    # Deliberately no ``with``: entering the context runs the lifespan, which
    # would try to reach PostgreSQL. These tests are about the routing table and
    # the error boundary, neither of which needs a live dependency.
    return TestClient(app, raise_server_exceptions=False)


def _schema_paths(client: Any) -> set[str]:
    """Every path the app advertises, read from the OpenAPI schema.

    Not from ``app.routes``: this FastAPI version keeps an included router as a
    single ``_IncludedRouter`` entry rather than flattening its routes into the
    application, so walking ``app.routes`` alone silently misses every mounted
    router. The schema is also the contract a client actually sees, which makes
    it the more meaningful assertion.
    """
    return set(client.get("/openapi.json").json()["paths"])


# --------------------------------------------------------------------------- #
# The mounted surface
# --------------------------------------------------------------------------- #


def test_every_expected_route_is_mounted(client: Any) -> None:
    """The routing table is the contract; assert it in full.

    Read from the OpenAPI schema rather than ``app.routes`` -- see
    ``_schema_paths``. This is also what caught the duplicated inline probe
    handlers in ``create_app``: with them shadowing the mounted router, the
    schema listed ``/health`` and ``/ready`` twice and ``/metrics`` was not
    reachable at all.
    """
    paths = _schema_paths(client)
    assert {"/health", "/ready", "/metrics"} <= paths
    assert {
        "/auth/login",
        "/auth/refresh",
        "/auth/logout",
        "/auth/me",
        "/auth/password",
    } <= paths


def test_probe_routes_are_served_by_the_health_router(client: Any) -> None:
    """No route may be defined twice, or the shadowed copy is dead code.

    ``create_app`` used to declare ``/health`` and ``/ready`` inline *and* mount
    the health router. The inline copies lost the match and were unreachable,
    but they still read as the real handlers. The OpenAPI schema cannot expose
    this -- duplicate path/method pairs collapse into one entry -- so this
    asserts on the shape of the live response instead: only the router version
    carries the health engine's fields.
    """
    body = client.get("/health").json()
    assert "warnings" in body, "the mounted health router should be serving /health"
    assert body["alive"] is True


def test_metrics_is_declared_even_while_disabled(client: Any) -> None:
    """The route exists in the schema while answering 404 by default.

    Disabling metrics must hide the data, not unregister the endpoint, or a
    deployment that later enables it would have no route to hit.
    """
    assert "/metrics" in _schema_paths(client)
    assert client.get("/metrics").status_code == 404


def test_probes_answer_without_a_live_database(client: Any) -> None:
    """Liveness must not need a dependency, or this test would hang.

    ``/health`` is the one route a load balancer hits before anything is known to
    be up, so it has to answer from the process alone.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["alive"] is True


def test_openapi_schema_generates(client: Any) -> None:
    """A broken response model raises here rather than on a live request."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/auth/login" in schema["paths"]
    assert "/health" in schema["paths"]


# --------------------------------------------------------------------------- #
# The error boundary
# --------------------------------------------------------------------------- #


def test_unknown_path_returns_the_error_envelope(client: Any) -> None:
    """Starlette's bare ``{"detail": ...}`` would be a second error shape."""
    response = client.get("/no-such-route")
    assert response.status_code == 404
    assert "error" in response.json(), "404s must use the ULTRON envelope too"
    assert "detail" not in response.json()


def test_wrong_method_returns_the_error_envelope(client: Any) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert "error" in response.json()
    assert "detail" not in response.json()


def test_protected_route_without_a_token_is_401(client: Any) -> None:
    """Unauthenticated is the default, and it must actually be enforced.

    A smoke test that logged in first would miss the single most important
    property of the assembled app: that an anonymous caller gets nothing.
    """
    response = client.get("/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"]
    assert "token" not in response.text.lower(), "no credential may appear in an error"


def test_malformed_authorization_header_is_rejected(client: Any) -> None:
    """A non-empty but unparseable header must not be treated as anonymous."""
    response = client.get("/auth/me", headers={"Authorization": "Token abc"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Middleware on requests that never reach a route
# --------------------------------------------------------------------------- #


def test_correlation_id_is_generated_when_absent(client: Any) -> None:
    """Every response carries one, so a log line can always be found."""
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_correlation_id_is_echoed_when_supplied(client: Any) -> None:
    supplied = "smoke-test-correlation"
    response = client.get("/health", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied


def test_correlation_id_is_applied_to_errors_too(client: Any) -> None:
    """The 404 path skips every route, so the middleware is the only chance."""
    supplied = "smoke-test-on-error"
    response = client.get("/no-such-route", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied


# --------------------------------------------------------------------------- #
# Construction guarantees
# --------------------------------------------------------------------------- #


def test_the_real_container_satisfies_the_container_protocol() -> None:
    """``ContainerProtocol`` is structural, so ``mypy`` is not the only guard.

    ``_check_real_container_satisfies`` is a type-checking-only function. This
    asserts the wiring the probes depend on is actually present at runtime, which
    is what turns a protocol mismatch into a 500 rather than an import error.
    """
    container = Container()
    assert container.health is not None
    assert isinstance(container.settings, type(reload_settings()))
