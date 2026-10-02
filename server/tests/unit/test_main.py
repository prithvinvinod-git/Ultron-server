"""Tests for the FastAPI application factory (T020 hardening).

The factory is the one place where a whole class of defect is invisible at
runtime: a permissive default produces a server that starts cleanly, answers
requests, and fails only in the browser of somebody else. Every test here pins
a *closed* default, because spec section 30 requires authorization to hold
"even on trusted networks" and section 31 requires secrets and services to stay
unexposed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.requests import Request

from app.config import Settings, reload_settings
from app.core.errors import (
    AgentError,
    CommandNotAllowedError,
    ConfigError,
    DatabaseError,
    DeviceError,
    ErrorCode,
    NotFoundError,
    OperationTimeoutError,
    PermissionDeniedError,
    RateLimitedError,
    SerializationError,
    SsrfBlockedError,
    ToolPermissionDeniedError,
    UltronError,
)
from app.main import create_app
from app.observability.health import CheckResult, HealthReport, HealthStatus


def _build(monkeypatch: pytest.MonkeyPatch, **env: str) -> FastAPI:
    """Create an app with ``env`` applied to the process environment.

    ``get_settings`` is cached, so the cache has to be dropped after the
    variables are set. Without this the tests would quietly assert against the
    defaults no matter what they configured, which is exactly the kind of green
    that proves nothing.
    """
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reload_settings()
    return create_app()


def _cors_kwargs(app: FastAPI) -> dict[str, Any]:
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return dict(middleware.kwargs)
    raise AssertionError("CORSMiddleware is not installed")


def _host_allowlist(app: FastAPI) -> list[str] | None:
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "TrustedHostMiddleware":
            return list(middleware.kwargs["allowed_hosts"])
    return None


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": Headers({})})


def _report(*, ready: bool) -> HealthReport:
    """A minimal report whose required PostgreSQL check decides readiness."""
    database = CheckResult(
        name="postgresql",
        status=HealthStatus.OK if ready else HealthStatus.FAILED,
        required=True,
    )
    return HealthReport(
        status=HealthStatus.OK if ready else HealthStatus.FAILED,
        checks=(database,),
        ready=ready,
        started_at=0.0,
        duration_ms=1.0,
    )


class _StubHealth:
    """Stands in for ``HealthService`` so probes need no live dependencies."""

    def __init__(self, ready: bool) -> None:
        self._ready = ready

    async def liveness(self) -> dict[str, Any]:
        return {"status": HealthStatus.OK.value, "alive": True, "version": "test"}

    async def readiness(self, *, use_cache: bool = True) -> HealthReport:
        return _report(ready=self._ready)


class _StubContainer:
    """Replaces the real container so probes need no live dependencies.

    ``Container.health`` is a read-only property, so the whole container is
    substituted rather than patched attribute-wise.

    ``settings`` is present because the probe routes report configuration
    warnings (T022), which is how ``Settings.startup_warnings``' promise of being
    "observable through /health" is kept. Real settings are used rather than a
    stub so the warnings under test are the ones the settings module actually
    produces.
    """

    def __init__(self, ready: bool) -> None:
        self.health = _StubHealth(ready=ready)

    @property
    def settings(self) -> Settings:
        return reload_settings()


def _app_with_stub(app: FastAPI, *, ready: bool) -> FastAPI:
    app.state.container = _StubContainer(ready=ready)
    return app


class TestCorsDefaultsAreClosed:
    """An unset ``CORS_ORIGINS`` must disable CORS, not open it."""

    def test_default_allows_no_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch)
        assert _cors_kwargs(app)["allow_origins"] == []

    def test_default_does_not_send_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "*" with credentials is the classic permissive misconfiguration.
        kwargs = _cors_kwargs(_build(monkeypatch))
        assert kwargs["allow_credentials"] is False
        assert "*" not in kwargs["allow_origins"]

    def test_wildcard_credentials_pair_is_never_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        kwargs = _cors_kwargs(_build(monkeypatch, CORS_ORIGINS="*"))
        # A wildcard is honoured, but only by giving up credentialed access.
        assert kwargs["allow_origins"] == ["*"]
        assert kwargs["allow_credentials"] is False

    def test_explicit_origins_enable_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kwargs = _cors_kwargs(_build(monkeypatch, CORS_ORIGINS="https://app.example"))
        assert kwargs["allow_origins"] == ["https://app.example"]
        assert kwargs["allow_credentials"] is True

    def test_mixing_wildcard_with_origins_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # This resolves to "*" in every browser anyway, so it is a silent
        # broadening of policy. Failing at construction is the only way the
        # misconfiguration gets noticed.
        with pytest.raises(ConfigError):
            _build(monkeypatch, CORS_ORIGINS="*,https://app.example")

    def test_methods_and_headers_are_not_wildcards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kwargs = _cors_kwargs(_build(monkeypatch, CORS_ORIGINS="https://app.example"))
        assert "*" not in kwargs["allow_methods"]
        assert "*" not in kwargs["allow_headers"]

    def test_unlisted_origin_is_not_reflected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch, CORS_ORIGINS="https://app.example")
        # No lifespan: these probes answer from the app itself.
        client = TestClient(app)
        response = client.get("/health", headers={"Origin": "https://evil.example"})
        assert response.headers.get("access-control-allow-origin") is None


class TestTrustedHost:
    """Host-header checking is off unless a deployment names its hosts."""

    def test_absent_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _host_allowlist(_build(monkeypatch)) is None

    def test_installed_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch, ALLOWED_HOSTS="ultron.example,10.0.0.5")
        assert _host_allowlist(app) == ["ultron.example", "10.0.0.5"]

    def test_foreign_host_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch, ALLOWED_HOSTS="ultron.example")
        client = TestClient(app)
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/health", headers={"Host": "ultron.example"}).status_code == 200


class TestErrorMappingUsesDeclaredStatus:
    """``UltronError.http_status`` and ``.code`` are the single source of truth.

    The factory used to rebuild both from an ``isinstance`` ladder that had
    drifted away from ``errors.py``, which reported every error as
    ``INTERNAL_ERROR`` and answered permission denials with 500.
    """

    @pytest.mark.parametrize(
        ("exc", "expected_status"),
        [
            (PermissionDeniedError("nope"), 403),
            (ToolPermissionDeniedError("terminal"), 403),
            (CommandNotAllowedError("rm"), 403),
            (SsrfBlockedError("169.254.169.254"), 403),
            (NotFoundError("agent"), 404),
            (RateLimitedError(), 429),
            (OperationTimeoutError("slow"), 504),
            (SerializationError("payload"), 500),
            (DatabaseError("ping"), 503),
            (AgentError("boom"), 500),
            (DeviceError("offline"), 503),
        ],
    )
    async def test_status_matches_the_error_contract(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exc: UltronError,
        expected_status: int,
    ) -> None:
        app = _build(monkeypatch)
        handler = app.exception_handlers[UltronError]
        response = await handler(_request(), exc)
        assert response.status_code == expected_status
        assert response.status_code == exc.http_status

    @pytest.mark.parametrize(
        ("exc", "expected_code"),
        [
            (PermissionDeniedError("nope"), ErrorCode.PERMISSION_DENIED),
            (CommandNotAllowedError("rm"), ErrorCode.COMMAND_NOT_ALLOWED),
            (NotFoundError("agent"), ErrorCode.NOT_FOUND),
            (RateLimitedError(), ErrorCode.RATE_LIMITED),
            (DatabaseError("ping"), ErrorCode.DATABASE_ERROR),
        ],
    )
    async def test_wire_code_is_the_error_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exc: UltronError,
        expected_code: ErrorCode,
    ) -> None:
        import json

        app = _build(monkeypatch)
        handler = app.exception_handlers[UltronError]
        response = await handler(_request(), exc)
        payload = json.loads(response.body)
        assert payload["error"]["code"] == expected_code.value

    async def test_non_serializable_details_do_not_break_the_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``details`` is caller-supplied. A value json.dumps cannot handle used to
        # turn an error into a 500 from inside the error handler itself.
        import json

        exc = SerializationError(
            "bad payload",
            details={"at": datetime(2026, 1, 1, tzinfo=UTC), "when": timedelta(seconds=5)},
        )
        app = _build(monkeypatch)
        handler = app.exception_handlers[UltronError]
        response = await handler(_request(), exc)
        assert response.status_code == 500
        encoded = json.loads(response.body)["error"]["details"]
        assert encoded["at"] == "2026-01-01T00:00:00+00:00"


class TestValidationErrorsDoNotEchoInput:
    """A validation failure must not reflect the submitted secret back."""

    def test_response_is_422_and_carries_no_submitted_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from pydantic import BaseModel, Field

        class Payload(BaseModel):
            username: str = Field(min_length=3)
            password: str = Field(min_length=8)

        app = _build(monkeypatch)

        @app.post("/probe")
        async def probe(payload: Payload) -> dict[str, str]:  # pragma: no cover - never runs
            return {"ok": "yes"}

        client = TestClient(app)
        response = client.post("/probe", json={"username": "ab", "password": "s3cret-value"})

        assert response.status_code == 422
        body = response.text
        assert "s3cret-value" not in body
        assert response.json()["error"]["code"] == ErrorCode.INVALID_INPUT.value

    async def test_validation_errors_stay_serialisable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pydantic puts the original exception object in ``ctx`` for custom
        # validators. A bare json.dumps on that raises TypeError, which would
        # replace a clear 422 with an opaque 500.
        import json

        from fastapi.exceptions import RequestValidationError

        raw = [
            {
                "loc": ("body", "count"),
                "msg": "Value error, not a number",
                "type": "value_error",
                "input": "abc",
                "ctx": {"error": ValueError("not a number")},
            }
        ]

        app = _build(monkeypatch)
        handler = app.exception_handlers[RequestValidationError]
        response = await handler(_request(), RequestValidationError(raw))
        assert response.status_code == 422
        payload = json.loads(response.body)
        assert payload["error"]["code"] == ErrorCode.INVALID_INPUT.value
        # The submitted value is not reflected back to the caller.
        assert "abc" not in response.body.decode()


class TestProbesReportRealState:
    """``/health`` and ``/ready`` must answer from the health service."""

    def test_liveness_reports_the_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _app_with_stub(_build(monkeypatch), ready=True)
        payload = TestClient(app).get("/health").json()
        assert payload["alive"] is True

    def test_ready_returns_200_when_dependencies_are_usable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app = _app_with_stub(_build(monkeypatch), ready=True)
        response = TestClient(app).get("/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True

    def test_ready_returns_503_when_a_required_check_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A probe that always answers 200 reports health the process does not
        # have, which is how traffic gets routed to a server that cannot serve it.
        app = _app_with_stub(_build(monkeypatch), ready=False)
        response = TestClient(app).get("/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False


class TestDocumentationExposure:
    """``/docs`` catalogues the whole route surface, so production disables it."""

    def test_served_in_development(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch, ENVIRONMENT="development")
        assert TestClient(app).get("/docs").status_code == 200

    def test_disabled_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build(monkeypatch, ENVIRONMENT="production")
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


class TestAppIsImportableWithoutServices:
    def test_creating_the_app_opens_no_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Import-time side effects break Alembic and the test suite alike.
        app = _build(monkeypatch)
        assert isinstance(app, FastAPI)


class TestSettingsSurfaceSecurityDefaults:
    def test_allowed_hosts_defaults_to_empty(self) -> None:
        assert Settings().app.allowed_hosts == []

    @pytest.mark.parametrize(
        "value",
        ["a.example", "a.example,b.example", " a.example , b.example "],
    )
    def test_allowed_hosts_accepts_a_comma_separated_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", value)
        reload_settings()
        allowed = Settings().app.allowed_hosts
        assert all(" " not in host for host in allowed)
        assert set(allowed) == {part.strip() for part in value.split(",")}

    def test_production_warns_about_a_wildcard_bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_HOST", "0.0.0.0")
        reload_settings()
        warnings = Settings().startup_warnings()
        assert any("0.0.0.0" in warning for warning in warnings)
