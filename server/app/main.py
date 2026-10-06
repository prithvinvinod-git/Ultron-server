"""FastAPI application factory (T020).

This module creates the ASGI application with:
- Dependency injection container wired into the lifespan
- Exception handlers mapping Ultron errors to HTTP responses
- Router mounting (empty for now; populated by T022â€“T024, T036â€“T039)
- Basic middleware (CORS, trusthost, etc.) as needed

The application is importable without starting services:
    from app.main import create_app
    app = create_app()

This allows tools like `uvicorn app.main:create_app` to work in Docker
and CI without a running PostgreSQL or Redis.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import AppSettings, get_settings
from app.container import Container
from app.core.errors import (
    ConfigError,
    ErrorCode,
    UltronError,
)
from app.observability import configure_logging, get_logger, new_id, request_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.config.settings import Settings

_LOGGER = get_logger(__name__)

#: A correlation id travels from a client into every log line for that request
#: and back out in a response header, so it is restricted to characters that
#: cannot terminate a log line or a header. Anything else is discarded and
#: replaced with a generated id: a malformed correlation header is a client
#: bug, not a reason to fail the request, but it is also not something to copy
#: into the logs verbatim.
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _log_startup_warnings(settings: Settings) -> None:
    """Surface the configuration problems the operator needs to see.

    ``Settings.startup_warnings()`` already knows how to spot a placeholder JWT
    secret, a placeholder admin password, ``ALLOW_ANONYMOUS``, or a
    non-PostgreSQL DSN. Nothing called it, so a deployment could run for weeks
    with a default admin password and nobody would ever be told.
    """
    for warning in settings.startup_warnings():
        _LOGGER.warning(warning, extra={"event": "app.config_warning"})


def _correlation_id(headers: Headers, candidates: tuple[str, ...], max_length: int) -> str:
    """Return a trustworthy correlation id for this request.

    A client-supplied id is honoured so a proxy, a gateway and ULTRON can be
    joined in one log search, but only after validation: the value is length
    limited and restricted to a safe character set, so a caller cannot inject
    newlines into the log stream or grow every log line for a request
    unboundedly. A rejected value is replaced rather than rejected, because
    losing observability should not cost availability.

    A generated id is hex, so it always passes the same check.
    """
    for name in candidates:
        supplied = headers.get(name)
        if supplied is None:
            continue
        candidate = supplied.strip()
        if len(candidate) <= max_length and _SAFE_CORRELATION_ID.match(candidate):
            return candidate
    return new_id()


class RequestContextMiddleware:
    """Bind a correlation id and emit one access log line per request.

    Implemented as raw ASGI rather than ``BaseHTTPMiddleware`` for one reason:
    ``BaseHTTPMiddleware`` runs the downstream app in a separate task, and
    ``ContextVar`` writes made here are not reliably visible to the handlers
    inside. The correlation id is set before ``await self.app(...)`` and read by
    handlers and by the audit logger, so it has to be the same context -- which
    raw ASGI guarantees and the base class does not.

    The id is put in ``scope["state"]`` as well as the context variable so that
    code reached outside the request context (WebSocket callbacks, background
    tasks spawned from a handler) can still find it.
    """

    def __init__(self, app: Any, candidates: tuple[str, ...], max_length: int) -> None:
        self.app = app
        self._candidates = candidates
        self._max_length = max_length

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        rid = _correlation_id(headers, self._candidates, self._max_length)
        scope.setdefault("state", {})["request_id"] = rid

        if scope["type"] == "websocket":
            # A WebSocket handshake never returns a response object, so there
            # is nowhere to echo the id back to; the context still matters for
            # the frames that follow.
            with request_context(rid):
                await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                existing = message.setdefault("headers", [])
                existing.append((b"x-request-id", rid.encode("ascii", errors="replace")))
            await send(message)

        with request_context(rid):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                _LOGGER.info(
                    "request completed",
                    extra={
                        "event": "http.request",
                        "http": {
                            "method": method,
                            "path": path,
                            "status": status_code,
                            "duration_ms": round(elapsed_ms, 2),
                        },
                        "request_id": rid,
                    },
                )


def _add_request_context(app: FastAPI) -> None:
    """Install the correlation/access-log middleware.

    Added first, so it is the *innermost* of the three. Starlette runs the most
    recently added middleware outermost, which means the order a request meets
    is TrustedHost, then CORS, then correlation. A request with a forged ``Host``
    header is therefore rejected before anything else looks at it, and a
    preflight response is produced without needing a correlation id at all.
    """
    from app.api.dependencies import CORRELATION_HEADERS, MAX_CORRELATION_LENGTH

    app.add_middleware(
        RequestContextMiddleware,
        candidates=CORRELATION_HEADERS,
        max_length=MAX_CORRELATION_LENGTH,
    )


def _include_routers(app: FastAPI) -> None:
    """Mount all API routers.

    Routers are mounted explicitly rather than discovered: the mount list is
    the complete list of routes the application exposes, and a missing entry is
    a visible omission instead of a silent one.
    """
    from app.api.routes import auth, events, health

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(events.router)


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


_HTTP_ERROR_CODES: dict[int, ErrorCode] = {
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHENTICATED,
    status.HTTP_403_FORBIDDEN: ErrorCode.PERMISSION_DENIED,
}
"""Wire code for each framework-raised HTTP status.

Starlette's router raises ``HTTPException`` directly for the statuses that mean
"you asked for something that does not exist here", so those never pass through
an ``UltronError`` constructor. Anything not listed falls back to ``INTERNAL``:
the statuses left over are ones ULTRON does not emit on its own, and reporting
them as an internal fault is the safer guess.
"""


def add_exception_handlers(app: FastAPI) -> None:
    """Add exception handlers for all Ultron errors.

    Public because a test that assembles a partial app still needs them: a
    ``UltronError`` raised in a dependency only becomes its declared HTTP status
    because of what this function installs.
    """

    @app.exception_handler(UltronError)
    async def ultron_error_handler(_: Request, exc: UltronError) -> JSONResponse:
        """Map Ultron errors to HTTP responses using their declared status."""
        return JSONResponse(status_code=exc.http_status, content=_error_body(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Map framework-raised HTTP errors into the ULTRON envelope.

        A 404 from an unmatched path and a 405 from a wrong method are raised by
        Starlette's router, not by ULTRON code, so they never become an
        ``UltronError`` and bypass the handler above. They would otherwise answer
        with FastAPI's bare ``{"detail": "Not Found"}`` while every other failure
        answers ``{"error": {...}}`` -- two error shapes on one API, which forces
        every client to special-case the second one. This was found by the T024
        smoke test, which is the only place both shapes are visible side by side.
        """
        code = _HTTP_ERROR_CODES.get(exc.status_code, ErrorCode.INTERNAL)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code.value, "message": str(exc.detail)}},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle Pydantic validation errors.

        ``exc.errors()`` is passed through ``jsonable_encoder`` for two reasons.
        Pydantic puts the original exception object in ``ctx`` for custom
        validators, and a bare ``json.dumps`` on that raises ``TypeError`` â€”
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
    connects at import time â€” importing this module must stay side-effect free
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

    Two rules govern this function, both from spec Â§30/Â§31:

    **Nothing is open by default.** An empty list means the middleware does
    nothing, not that it permits everything. ``CORS_ORIGINS=`` is documented as
    "disabled", and that has to be the real behaviour â€” otherwise a deployment
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
    dependency. Spec Â§30 says administrative functionality must not be exposed
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
    add_exception_handlers(app)

    # Middleware order matters. Starlette runs the most recently added
    # middleware outermost, so this sequence produces: TrustedHost -> CORS ->
    # RequestContext -> routing. A forged Host is rejected before any other
    # logic runs, and every request that does proceed has a correlation id
    # bound before it reaches a handler.
    _add_request_context(app)
    _add_middleware(app, settings.app)

    # Include routers
    _include_routers(app)

    # `/health`, `/ready` and `/metrics` are served by the health router mounted
    # above. They used to also be defined inline here, after the mount -- which
    # left the inline copies unreachable (an included router is matched first)
    # while still appearing to be the real handlers. T024's smoke test found the
    # duplicate; the router versions are the ones that should survive, because
    # they are the ones with the health engine behind them.

    return app


app = create_app()
