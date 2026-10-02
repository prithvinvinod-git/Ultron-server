"""Route-level tests for ``/health``, ``/ready``, and ``/metrics`` (T022).

The engine is already covered in ``test_observability_health.py``: this file
tests what only the assembled application can answer.

* the mounted surface is exactly the three intended routes;
* ``/health`` stays ``200`` under a misconfiguration instead of crash-looping,
  and surfaces the warnings that ``startup_warnings`` promises;
* ``/ready`` returns ``503`` for a failed *required* check but not for a failed
  optional one, which is the distinction spec §33 rests on;
* ``/metrics`` renders, and is absent when disabled.

The container is stubbed at the ``ContainerProtocol`` boundary, matching
``test_auth_routes.py``. A real :class:`HealthService` with fake checks is used
rather than a fake report object, so the folding of statuses into ``ready`` is
exercised here instead of being asserted against a hand-built literal.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.health import router as health_router
from app.config import get_settings
from app.main import add_exception_handlers
from app.observability.health import (
    EXPECTED_CHECKS,
    HealthService,
    HealthStatus,
    _result,
    result_required,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class StubContainer:
    """Just enough container for the probe routes.

    ``health`` is the only member the routes reach for, and it is a real
    ``HealthService`` with fake checks registered so that the report folding --
    not a literal -- is what the assertions run against.
    """

    def __init__(self, health: HealthService, settings: Any) -> None:
        self.health = health
        self._settings = settings

    @property
    def settings(self) -> Any:
        return self._settings


#: The four checks that exist today. ``agent_runtime`` and ``event_bus`` belong to
#: subsystems that do not exist yet (T044, T031), so a default service leaves them
#: unregistered and they report as ``skipped`` -- which is the point.
_REGISTERED_NOW: tuple[str, ...] = ("postgresql", "redis", "ollama", "filesystem")


def _service(*, complete: bool = False, **results: HealthStatus) -> HealthService:
    """A health service whose named checks report the given statuses.

    Checks not named report ``ok``. Pass ``complete=True`` to also register the
    two subsystems that have not landed, which is the only way to get an
    aggregate status of plain ``ok``: an unregistered check outranks ``ok`` by
    design, so a half-covered report must not claim to be fully healthy.

    Each fake check marks itself required exactly as the real container's probes
    do. Omitting that would make a broken PostgreSQL look optional and the
    readiness assertions would pass for the wrong reason.
    """
    service = HealthService()
    names = EXPECTED_CHECKS if complete else _REGISTERED_NOW
    for name in sorted(names):
        service.register(name, _fake_check(name, results.get(name, HealthStatus.OK)))
    return service


def _fake_check(name: str, state: HealthStatus) -> Any:
    """Build a check coroutine returning a fixed verdict for ``name``."""

    async def check() -> Any:
        return _result(
            name,
            state,
            detail="stubbed" if state is not HealthStatus.OK else "",
            latency_ms=1.5,
            required=result_required(name),
        )

    return check


def _failing_check() -> Any:
    """A check that raises, to exercise the engine's failure capture."""

    async def check() -> Any:
        raise RuntimeError("dependency unreachable")

    return check


def _gauge(text: str, name: str) -> float:
    """Read a labelled-free gauge's value out of an exposition body."""
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"gauge {name} not present in exposition")


@pytest.fixture
def settings_ok() -> Any:
    """Settings with no warnings and metrics enabled.

    Note the *flat* field names. ``Settings.security`` and ``Settings.observability``
    are computed properties assembled from flat fields, so updating the nested
    model would be silently discarded and the fixture would quietly test nothing.
    """
    return get_settings().model_copy(update={"metrics_enabled": True})


@pytest.fixture
def settings_warned() -> Any:
    """Settings that trip ``startup_warnings``.

    ``ALLOW_ANONYMOUS`` is the cleanest lever: it produces a warning in every
    environment, so the test does not have to fake a production environment just
    to produce one warning.

    ``metrics_enabled`` is set explicitly because this fixture is used to scrape
    ``/metrics``. It used to rely on the shipped default being ``True``, which
    meant the assertion was silently coupled to a default that was about to
    change; T024 changed it to ``False`` and this test failed for the right
    reason.
    """
    return get_settings().model_copy(update={"allow_anonymous": True, "metrics_enabled": True})


@pytest.fixture
def settings_no_metrics() -> Any:
    return get_settings().model_copy(update={"metrics_enabled": False})


def _app(health: HealthService, settings: Any) -> FastAPI:
    application = FastAPI()
    application.state.container = StubContainer(health, settings)
    add_exception_handlers(application)
    application.include_router(health_router)
    return application


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_app(_service(), get_settings())) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Mounted surface
# --------------------------------------------------------------------------- #


def test_exactly_three_routes_are_mounted() -> None:
    """The mount list is the complete list, so an omission is visible.

    Read off the router itself rather than the application: FastAPI adds
    ``/docs`` and ``/openapi.json`` to every app, and those are not ULTRON
    health routes.
    """
    paths = {route.path for route in health_router.routes}
    assert paths == {"/health", "/ready", "/metrics"}


def test_probe_routes_need_no_token() -> None:
    """An orchestrator cannot present credentials, so probes must not ask."""
    application = _app(_service(), get_settings())
    with TestClient(application) as test_client:
        assert test_client.get("/health").status_code == 200
        assert test_client.get("/ready").status_code == 200


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #


def test_health_is_ok_and_alive(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["alive"] is True
    assert body["status"] == "ok"
    assert "version" in body


def test_health_does_not_consult_dependencies() -> None:
    """Liveness must not open a database connection.

    The strongest available proof: a service whose checks all raise still
    answers ``/health`` cleanly, because ``liveness`` never runs them.
    """
    service = HealthService()
    service.register("postgresql", _failing_check())
    with TestClient(_app(service, get_settings())) as test_client:
        response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_surfaces_configuration_warnings(settings_warned: Any) -> None:
    """The promise in ``startup_warnings`` is that these are observable."""
    with TestClient(_app(_service(), settings_warned)) as test_client:
        response = test_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["warnings"], "expected the misconfiguration to be reported"
    assert any("ALLOW_ANONYMOUS" in warning for warning in body["warnings"])
    assert body["status"] == "degraded"


def test_health_stays_200_under_misconfiguration(settings_warned: Any) -> None:
    """A restart cannot fix configuration, so the probe must not fail.

    If this ever returns non-200 the deployment crash-loops and stops being
    diagnosable, which is the opposite of what ``startup_warnings`` intends.
    """
    with TestClient(_app(_service(), settings_warned)) as test_client:
        assert test_client.get("/health").status_code == 200


# --------------------------------------------------------------------------- #
# /ready
# --------------------------------------------------------------------------- #


def test_ready_is_200_and_ok_when_everything_is_present_and_fine() -> None:
    """The only way to earn a plain ``ok``.

    An unregistered check outranks ``ok``, so the two subsystems that have not
    landed must be registered for this scenario to be reachable at all.
    """
    with TestClient(_app(_service(complete=True), get_settings())) as test_client:
        response = test_client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["status"] == "ok"


def test_ready_is_503_when_a_required_check_fails() -> None:
    """PostgreSQL is the authoritative store, so it alone decides."""
    service = _service(complete=True, postgresql=HealthStatus.FAILED)
    with TestClient(_app(service, get_settings())) as test_client:
        response = test_client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["status"] == "failed"
    failed = next(c for c in body["checks"] if c["name"] == "postgresql")
    assert failed["ready"] is False


def test_ready_stays_200_when_an_optional_check_fails() -> None:
    """Redis down is a cold cache, not an unusable service (spec §33).

    The aggregate status still reads ``failed`` -- something *is* broken -- while
    readiness stays true, because only PostgreSQL stands between the server and
    traffic. The two fields answer different questions on purpose.
    """
    service = _service(complete=True, redis=HealthStatus.FAILED)
    with TestClient(_app(service, get_settings())) as test_client:
        response = test_client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    redis = next(c for c in body["checks"] if c["name"] == "redis")
    assert redis["status"] == "failed"
    assert redis["ready"] is True


def test_ready_lists_unregistered_checks_as_skipped(client: TestClient) -> None:
    """An absent check is information; a vacuously green one is not."""
    body = client.get("/ready").json()
    skipped = {c["name"]: c for c in body["checks"] if c["status"] == "skipped"}
    assert "agent_runtime" in skipped
    assert "event_bus" in skipped


def test_ready_reports_a_raising_check_as_failed_not_500() -> None:
    service = _service(complete=True)
    service.register("postgresql", _failing_check())
    with TestClient(_app(service, get_settings())) as test_client:
        response = test_client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    check = next(c for c in body["checks"] if c["name"] == "postgresql")
    assert check["status"] == "failed"
    assert check["error"], "the typed error code should be recorded"


def test_ready_includes_configuration_warnings(settings_warned: Any) -> None:
    with TestClient(_app(_service(), settings_warned)) as test_client:
        body = test_client.get("/ready").json()
    assert any("ALLOW_ANONYMOUS" in warning for warning in body["warnings"])


# --------------------------------------------------------------------------- #
# /metrics
# --------------------------------------------------------------------------- #


def test_metrics_renders_prometheus_text(settings_ok: Any) -> None:
    with TestClient(_app(_service(), settings_ok)) as test_client:
        response = test_client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "ultron_health_check_status" in text
    assert "ultron_health_ready" in text
    assert 'ultron_health_check_status{check="postgresql",status="ok"} 1.0' in text


def test_metrics_reports_not_ready_as_zero(settings_ok: Any) -> None:
    service = _service(complete=True, postgresql=HealthStatus.FAILED)
    with TestClient(_app(service, settings_ok)) as test_client:
        text = test_client.get("/metrics").text
    assert 'ultron_health_check_status{check="postgresql",status="failed"} 1.0' in text
    assert _gauge(text, "ultron_health_ready") == 0.0
    assert _gauge(text, "ultron_health_up") == 0.0


def test_metrics_counts_startup_warnings_without_leaking_them(
    settings_warned: Any,
    settings_ok: Any,
) -> None:
    """A scrape describes the deployment, so it must not carry secret text."""
    with TestClient(_app(_service(), settings_ok)) as clean:
        assert "ultron_startup_warnings 0.0" in clean.get("/metrics").text
    with TestClient(_app(_service(), settings_warned)) as warned:
        text = warned.get("/metrics").text
    assert _gauge(text, "ultron_startup_warnings") >= 1.0
    assert "ALLOW_ANONYMOUS" not in text


def test_metrics_is_404_when_disabled(settings_no_metrics: Any) -> None:
    """A 404 beats an empty body, which reads as 'healthy, nothing to report'."""
    with TestClient(_app(_service(), settings_no_metrics)) as test_client:
        assert test_client.get("/metrics").status_code == 404
