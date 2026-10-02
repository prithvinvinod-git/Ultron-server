"""FastAPI application factory (T020).

This module creates the ASGI application with:
- Dependency injection container wired into the lifespan
- Exception handlers mapping Ultron errors to HTTP responses
- Router mounting (empty for now; populated by T022–T024, T036–T039)
- Basic middleware (CORS, trusthost, etc.) as needed

The application is importable without starting services:
    from app.main import create_app
    app = create_app()

This allows tools like `uvicorn app.main:create_app` to work in Docker
and CI without a running PostgreSQL or Redis.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.config import AppSettings, get_settings
from app.container import Container
from app.core.errors import (
    ConfigError,
    ErrorCode,
    UltronError,
)
from app.observability import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.config.settings import Settings

_LOGGER = get_logger(__name__)


def _log_startup_warnings(settings: Settings) -> None:
    """Surface the configuration problems the operator needs to see.

    ``Settings.startup_warnings()`` already knows how to spot a placeholder JWT
    secret, a placeholder admin password, ``ALLOW_ANONYMOUS``, or a
    non-PostgreSQL DSN. Nothing called it, so a deployment could run for weeks
    with a default admin password and nobody would ever be told.
    """
    for warning in settings.startup_warnings():
        _LOGGER.warning(warning, extra={"event": "app.config_warning"})


def _include_routers(app: FastAPI) -> None:
    """Mount all API routers.

    Currently empty; populated by:
    - T022: auth
    - T036: agents
    - T037: tasks
    - T038: memories
    - T039: conversations
    """
    # Placeholder for future routers
    pass


def _error_body(exc: UltronError) -> dict[str, Any]:
    """Build the error envelope for an Ultron exception.

    ``exc.code`` and ``exc.http_status`` are the single source of truth for both
    the wire code and the HTTP status. Re-deriving either from an ``isinstance``
    ladder here would create a second place to keep in sync, and it already had
    drifted: every response was labelled ``INTERNAL_ERROR`` because this used a
    non-existent ``error_code`` attribute, and permission denials were reported
    as 500 instead of 403.

    ``details`` is encoded with ``jsonable_encoder`` because it is caller-supplied
    and may contain non-JSON types, which would otherwise turn an error response
    into a 500.
    """
    body: dict[str, Any] = {
        "error": {
            "code": exc.code.value,
            "message": str(exc),
        }
    }
    if exc.details:
        body["error"]["details"] = jsonable_encoder(exc.details)
    return body


def _add_exception_handlers(app: FastAPI) -> None:
    """Add exception handlers for all Ultron errors."""

    @app.exception_handler(UltronError)
    async def ultron_error_handler(_: Request, exc: UltronError) -> JSONResponse:
        """Map Ultron errors to HTTP responses using their declared status."""
        return JSONResponse(status_code=exc.http_status, content=_error_body(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle Pydantic validation errors.

        ``exc.errors()`` is passed through ``jsonable_encoder`` for two reasons.
        Pydantic puts the original exception object in ``ctx`` for custom
        validators, and a bare ``json.dumps`` on that raises ``TypeError`` —
        turning a client mistake into a 500. And each error carries the
        submitted ``input`` value, which for a login or API-key endpoint would
        reflect the attempted password straight back into the response. The
        errors are reduced to location, message and type instead.
        """
        details = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": ErrorCode.INVALID_INPUT.value,
                    "message": "Invalid input",
                    "details": details,
                }
            },
        )


@contextlib.asynccontextmanager
async def container_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own startup and shutdown, so the container is created once per process.

    The container lives on ``app.state`` because every request dependency needs
    it, but it is constructed *here* rather than at module scope so that nothing
    connects at import time — importing this module must stay side-effect free
    for tests and for Alembic.

    Startup pings PostgreSQL and Redis and therefore fails loudly if the
    database is unreachable, rather than accepting traffic that cannot be
    served.
    """
    container = app.state.container
    _LOGGER.info("Starting ULTRON server", extra={"event": "app.startup"})
    await container.startup()
    _LOGGER.info("Startup complete", extra={"event": "app.startup_complete"})
    try:
        yield
    finally:
        _LOGGER.info("Shutting down ULTRON server", extra={"event": "app.shutdown"})
        await container.shutdown()
        _LOGGER.info("Shutdown complete", extra={"event": "app.shutdown_complete"})


def _add_middleware(app: FastAPI, settings: AppSettings) -> None:
    """Add the middleware the configuration actually asks for.

    Two rules govern this function, both from spec §30/§31:

    **Nothing is open by default.** An empty list means the middleware does
    nothing, not that it permits everything. ``CORS_ORIGINS=`` is documented as
    "disabled", and that has to be the real behaviour — otherwise a deployment
    that never set the variable gets the most permissive policy in the
    application. Wildcards are therefore only honoured when a deployment asks
    for them explicitly.

    **Credentials are never combined with a wildcard.** ``allow_origins=["*"]``
    with ``allow_credentials=True`` is rejected by browsers, so it grants
    nothing and misleads whoever reads the config into thinking it works. It is
    refused at startup instead, because failing to boot is the only outcome
    that gets the misconfiguration noticed.
    """
    origins = settings.cors_origins
    wildcard = "*" in origins

    if wildcard and origins != ["*"]:
        message = (
            "CORS_ORIGINS mixes '*' with explicit origins, which browsers resolve as '*' anyway"
        )
        raise ConfigError(message)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Credentials are only meaningful with an explicit origin list.
        allow_credentials=bool(origins) and not wildcard,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
        expose_headers=["X-Request-ID"],
    )

    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


def _docs_disabled(settings: AppSettings) -> bool:
    """Whether the interactive API documentation should be disabled.

    ``/docs`` serves a browsable catalogue of every route and every declared
    dependency. Spec §30 says administrative functionality must not be exposed
    without authentication, and an endpoint whose entire job is to describe the
    administrative surface is not something to publish in production.
    """
    return settings.is_production


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    settings = get_settings()

    # Configure logging before anything else can log. Redaction and the JSON
    # handler are installed here; without this the `ultron` logger has no
    # handlers and Python's last-resort stderr handler prints every record
    # unredacted, which defeats the redaction layer entirely.
    configure_logging(settings.logging)

    _log_startup_warnings(settings)

    docs_off = _docs_disabled(settings.app)

    app = FastAPI(
        title=settings.app_name,
        description="ULTRON Server API",
        version="0.1.0",
        lifespan=container_lifespan,
        docs_url=None if docs_off else "/docs",
        redoc_url=None if docs_off else "/redoc",
        openapi_url=None if docs_off else "/openapi.json",
    )

    # Store container in app state for access in dependencies and routes
    container = Container()
    app.state.container = container

    # Add exception handlers
    _add_exception_handlers(app)

    # Add middleware
    _add_middleware(app, settings.app)

    # Include routers
    _include_routers(app)

    # Health check endpoint (liveness probe)
    @app.get("/health", tags=["monitoring"])
    async def liveness(request: Request) -> dict[str, Any]:
        """Liveness probe: is the process alive?"""
        payload: dict[str, Any] = await request.app.state.container.health.liveness()
        return payload

    # Readiness probe endpoint
    @app.get("/ready", tags=["monitoring"])
    async def readiness(request: Request) -> JSONResponse:
        """Readiness probe: are the required dependencies usable?

        This delegates to the same :class:`HealthService` the rest of the system
        uses, so the probe reports what the application can actually do rather
        than a hardcoded 200. Only a failing *required* check makes the probe
        fail — an unreachable Redis is reported as degraded but still ready,
        because Redis is transient state and the service is designed to degrade.
        """
        report = await request.app.state.container.health.readiness()
        return JSONResponse(
            status_code=status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=report.to_dict(),
        )

    return app
