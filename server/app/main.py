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

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import LoggingSettings, get_settings
from app.container import Container
from app.core.errors import (
    AgentError,
    AuthError,
    BrowserError,
    CapabilityNotImplementedError,
    ConfigError,
    ConflictError,
    DependencyUnavailableError,
    DeviceError,
    ErrorCode,
    HealthCheckFailedError,
    InvalidCredentialsError,
    InvalidInputError,
    LocalModelUnavailableError,
    LockUnavailableError,
    NotFoundError,
    OperationTimeoutError,
    PermissionDeniedError,
    ProviderNotConfiguredError,
    RateLimitedError,
    SerializationError,
    ShuttingDownError,
    SsrfBlockedError,
    TaskError,
    ToolError,
    UltronError,
    VoiceError,
    WorkspaceError,
)
from app.observability import get_logger

_LOGGER = get_logger(__name__)


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


def _add_exception_handlers(app: FastAPI) -> None:
    """Add exception handlers for all Ultron errors."""

    @app.exception_handler(UltronError)
    async def ultron_error_handler(_: Request, exc: UltronError) -> JSONResponse:
        """Map Ultron errors to HTTP responses."""
        # Determine HTTP status code based on error type
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR  # default

        if isinstance(exc, (InvalidInputError, ValidationError, RequestValidationError)):
            status_code = status.HTTP_400_BAD_REQUEST
        elif isinstance(exc, NotFoundError):
            status_code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, (ConflictError, LockUnavailableError)):
            status_code = status.HTTP_409_CONFLICT
        elif isinstance(exc, (AuthError, InvalidCredentialsError)):
            status_code = status.HTTP_401_UNAUTHORIZED
        elif isinstance(exc, PermissionDeniedError):
            status_code = status.HTTP_403_FORBIDDEN
        elif isinstance(exc, (DependencyUnavailableError,
                           OperationTimeoutError,
                           HealthCheckFailedError,
                           LocalModelUnavailableError,
                           ProviderNotConfiguredError)):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif isinstance(exc, RateLimitedError):
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        elif isinstance(exc, SsrfBlockedError):
            status_code = status.HTTP_400_BAD_REQUEST
        elif isinstance(
            exc,
            (
                ConfigError,
                ToolError,
                TaskError,
                AgentError,
                WorkspaceError,
                VoiceError,
                BrowserError,
                DeviceError,
            ),
        ):
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        elif isinstance(exc, ShuttingDownError):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif isinstance(exc, CapabilityNotImplementedError):
            status_code = status.HTTP_501_NOT_IMPLEMENTED
        elif isinstance(exc, SerializationError):
            status_code = status.HTTP_400_BAD_REQUEST

        # Build error response
        error_response = {
            "error": {
                "code": (
                    exc.error_code.value if hasattr(exc, 'error_code') else ErrorCode.INTERNAL.value
                ),
                "message": str(exc),
                "details": getattr(exc, 'details', None),
            }
        }

        # Remove details if None to keep response clean
        if error_response["error"]["details"] is None:
            del error_response["error"]["details"]

        return JSONResponse(
            status_code=status_code,
            content=error_response,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle Pydantic validation errors."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": {
                    "code": ErrorCode.INVALID_INPUT.value,
                    "message": "Invalid input",
                    "details": exc.errors(),
                }
            },
        )


def _add_middleware(app: FastAPI, settings: LoggingSettings) -> None:
    """Add middleware based on settings."""

    # CORS middleware - configure based on security settings
    # For now, allow all origins in development; restrict in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: make configurable via security settings
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Trusted host middleware - protect against host header attacks
    # For now, allow all hosts in development; restrict in production
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"],  # TODO: make configurable via security settings
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="ULTRON Server API",
        version="0.1.0",
        # Disable docs in production if desired
        # docs_url=None if settings.environment == "production" else "/docs",
        # redoc_url=None if settings.environment == "production" else "/redoc",
    )

    # Store container in app state for access in dependencies and routes
    container = Container()
    app.state.container = container

    # Add lifespan events
    @app.on_event("startup")
    async def startup_event() -> None:
        """Run startup checks."""
        _LOGGER.info("Starting ULTRON server", extra={"event": "app.startup"})
        await container.startup()
        _LOGGER.info("Startup complete", extra={"event": "app.startup_complete"})

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        """Run cleanup checks."""
        _LOGGER.info("Shutting down ULTRON server", extra={"event": "app.shutdown"})
        await container.shutdown()
        _LOGGER.info("Shutdown complete", extra={"event": "app.shutdown_complete"})

    # Add exception handlers
    _add_exception_handlers(app)

    # Add middleware
    _add_middleware(app, settings.logging)

    # Include routers
    _include_routers(app)

    # Health check endpoint (liveness probe)
    @app.get("/health", tags=["monitoring"])
    async def liveness() -> dict[str, str]:
        """Liveness probe: is the process alive?"""
        return {"status": "alive"}

    # Readiness probe endpoint
    @app.get("/ready", tags=["monitoring"])
    async def readiness() -> dict[str, str]:
        """Readiness probe: are dependencies available?"""
        # This would ideally check the container's health service
        # For now, return a placeholder - T021 will wire this properly
        return {"status": "ready"}

    return app
