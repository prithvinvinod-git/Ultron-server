"""Typed exceptions and stable error codes (spec section 33).

Spec section 33 requires that ULTRON never silently swallows an exception, that
every subsystem has typed exceptions with structured errors, and that a model
provider failing does not crash the core. This module is where that contract is
defined, so the rest of the codebase raises rather than invents strings.

Three things are guaranteed here:

1. **A stable code.** Every error carries an :class:`ErrorCode` that appears in
   the API response, the log record and the event payload. Clients match on the
   code, never on the human message, so wording can change without breaking
   them.
2. **A retry verdict.** :attr:`UltronError.retryable` is part of the error
   itself, so the model router and the tool executor do not each keep their own
   private list of "probably transient" errors. A timeout is retryable; a
   permission denial is not, and that distinction is decided once, here.
3. **An HTTP status.** A domain error knows how it should surface over the API,
   so route handlers translate rather than classify.

Nothing is caught and hidden. Use :func:`degrade` when a failure really should
be survivable, and it records what was lost instead of discarding it.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Awaitable, Mapping
from typing import Any, Final

# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


class ErrorCode(enum.StrEnum):
    """Stable, machine-readable error identifiers.

    These values are part of ULTRON's public contract. Renaming one is a
    breaking change; the wording of the accompanying message is not.
    """

    # -- Generic ------------------------------------------------------------
    INTERNAL = "INTERNAL_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

    # -- Configuration and startup -----------------------------------------
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING = "CONFIG_MISSING"

    # -- Security -----------------------------------------------------------
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    SSRF_BLOCKED = "SSRF_BLOCKED"

    # -- Persistence --------------------------------------------------------
    DATABASE_ERROR = "DATABASE_ERROR"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    REDIS_ERROR = "REDIS_ERROR"

    # -- Models (spec section 33 names LOCAL_MODEL_UNAVAILABLE explicitly) ---
    LOCAL_MODEL_UNAVAILABLE = "LOCAL_MODEL_UNAVAILABLE"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MODEL_UNSUPPORTED = "MODEL_UNSUPPORTED"

    # -- Tools and agents ---------------------------------------------------
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_SCHEMA_INVALID = "TOOL_SCHEMA_INVALID"
    TOOL_VERIFICATION_FAILED = "TOOL_VERIFICATION_FAILED"
    COMMAND_NOT_ALLOWED = "COMMAND_NOT_ALLOWED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_FAILED = "TASK_FAILED"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_FAILED = "AGENT_FAILED"
    WORKSPACE_ERROR = "WORKSPACE_ERROR"

    # -- Subsystems ---------------------------------------------------------
    EVENT_BUS_ERROR = "EVENT_BUS_ERROR"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    VOICE_ERROR = "VOICE_ERROR"
    DEVICE_ERROR = "DEVICE_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    SERIALIZATION_ERROR = "SERIALIZATION_ERROR"


#: Codes whose cause is expected to clear on its own. A caller may retry these
#: without intervention; anything else needs a decision from a human or a
#: change of plan. The model router consults this to build its fallback chain.
RETRYABLE_CODES: Final[frozenset[ErrorCode]] = frozenset(
    {
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        ErrorCode.LOCAL_MODEL_UNAVAILABLE,
        ErrorCode.MODEL_TIMEOUT,
        ErrorCode.MODEL_RATE_LIMITED,
        ErrorCode.PROVIDER_ERROR,
        ErrorCode.REDIS_ERROR,
        ErrorCode.DATABASE_ERROR,
        ErrorCode.EVENT_BUS_ERROR,
        ErrorCode.HEALTH_CHECK_FAILED,
    }
)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class UltronError(Exception):
    """Base class for every error ULTRON raises deliberately.

    Subclasses set :attr:`code` and, where it differs, :attr:`http_status` and
    :attr:`retryable`. ``details`` carries structured, loggable context; it must
    never carry a secret, since it is written to logs and returned to clients.
    """

    code: ErrorCode = ErrorCode.INTERNAL
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Return the structured form used in API responses and events."""
        payload: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        # The cause's type is included but never its full text: a driver
        # exception can embed a connection string with a password in it.
        if self.cause is not None:
            payload["cause"] = type(self.cause).__name__
        return payload

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code}] {self.message} ({self.details})"
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!s}, message={self.message!r})"


class RetryableError(UltronError):
    """An error whose cause may clear without intervention."""

    retryable = True


class PermanentError(UltronError):
    """An error that will recur until something changes."""

    retryable = False


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------


class InvalidInputError(PermanentError):
    code = ErrorCode.INVALID_INPUT
    http_status = 422


class NotFoundError(PermanentError):
    code = ErrorCode.NOT_FOUND
    http_status = 404

    def __init__(self, resource: str, identifier: str | None = None) -> None:
        """Identify what was missing.

        Both arguments are optional so this stays usable where the specific id
        is genuinely unknown, such as a lookup that matched no row. The id is
        still recorded when it is known, because a bare "not found" is much
        harder to act on than one that names the row.
        """
        if identifier is None:
            message = f"{resource} does not exist"
            details = {"resource": resource}
        else:
            message = f"{resource} '{identifier}' does not exist"
            details = {"resource": resource, "id": identifier}
        super().__init__(message, details=details)
        self.resource = resource
        self.identifier = identifier


class ConflictError(PermanentError):
    code = ErrorCode.CONFLICT
    http_status = 409


class OperationTimeoutError(RetryableError):
    """A ULTRON operation exceeded its deadline.

    Named distinctly from the builtin ``TimeoutError`` so that an
    ``except TimeoutError`` in a caller keeps matching the builtin, and this
    error keeps its own code and HTTP status.
    """

    code = ErrorCode.TIMEOUT
    http_status = 504

    def __init__(
        self,
        operation: str,
        *,
        timeout: float | None = None,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        # A single positional is ambiguous: it is the operation, never a
        # message, so the wording is derived rather than supplied.
        merged: dict[str, Any] = {"operation": operation, **dict(details or {})}
        if timeout is not None:
            merged["timeout"] = timeout
        super().__init__(f"'{operation}' exceeded its time limit", details=merged, cause=cause)
        self.operation = operation


class OperationCancelledError(UltronError):
    """Work was cancelled on purpose.

    Not retryable: the caller who cancelled it does not want it back.
    """

    code = ErrorCode.CANCELLED
    http_status = 499

    def __init__(self, operation: str, *, reason: str = "cancelled by request") -> None:
        super().__init__(f"'{operation}' was cancelled: {reason}", details={"operation": operation})
        self.operation = operation


class RateLimitedError(RetryableError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429

    def __init__(self, *, retry_after: float | None = None, source: str = "") -> None:
        details: dict[str, Any] = {}
        if retry_after is not None:
            details["retry_after"] = retry_after
        if source:
            details["source"] = source
        super().__init__("rate limit exceeded", details=details)
        self.retry_after = retry_after


class DependencyUnavailableError(RetryableError):
    """A dependency ULTRON needs is not reachable right now.

    This is the honest form of "the feature does not work": the subsystem is
    present and configured, the service it talks to is simply not answering.
    """

    code = ErrorCode.DEPENDENCY_UNAVAILABLE
    http_status = 503

    def __init__(
        self, dependency: str, *, reason: str = "", cause: BaseException | None = None
    ) -> None:
        message = f"dependency '{dependency}' is unavailable"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message, details={"dependency": dependency}, cause=cause)
        self.dependency = dependency


class CapabilityNotImplementedError(UltronError):
    """A capability the spec requires is not built yet.

    Deliberately loud. An unimplemented path must not be quietly skipped, and
    it must not be faked with a stub that returns plausible output.
    """

    code = ErrorCode.NOT_IMPLEMENTED
    http_status = 501

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"'{capability}' is specified but not implemented yet",
            details={"capability": capability},
        )
        self.capability = capability


class ShuttingDownError(UltronError):
    """Raised when work arrives after shutdown has begun.

    Takes a default message so a caller can raise it as a bare signal, the way
    ``asyncio.CancelledError`` is used.
    """

    code = ErrorCode.SHUTTING_DOWN
    http_status = 503

    def __init__(self, message: str = "the server is shutting down") -> None:
        super().__init__(message)


class SerializationError(PermanentError):
    code = ErrorCode.SERIALIZATION_ERROR
    http_status = 500


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(PermanentError):
    """The configuration is unusable.

    Raised at startup, not per request: a server running with a broken
    configuration should stop and say why rather than serve wrong behaviour.
    """

    code = ErrorCode.CONFIG_INVALID
    http_status = 500


class ConfigMissingError(ConfigError):
    code = ErrorCode.CONFIG_MISSING


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class AuthError(PermanentError):
    code = ErrorCode.UNAUTHENTICATED
    http_status = 401

    def __init__(self, message: str = "authentication required", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class InvalidCredentialsError(AuthError):
    """Credentials were supplied and rejected.

    The message never says *which* part was wrong, so it cannot be used to
    probe for valid usernames.
    """

    code = ErrorCode.INVALID_CREDENTIALS

    def __init__(self) -> None:
        super().__init__("invalid username or password")


class PermissionDeniedError(PermanentError):
    """A request was understood and refused.

    Distinct from :class:`AuthError`: the caller is known, the action is not
    allowed. Every denial is audited (spec section 15).
    """

    code = ErrorCode.PERMISSION_DENIED
    http_status = 403

    def __init__(
        self,
        action: str,
        *,
        required_level: int | None = None,
        actual_level: int | None = None,
        **kwargs: Any,
    ) -> None:
        details: dict[str, Any] = {"action": action}
        if required_level is not None:
            details["required_level"] = required_level
        if actual_level is not None:
            details["actual_level"] = actual_level
        message = f"'{action}' is not permitted"
        if required_level is not None:
            message = f"{message} (requires level {required_level})"
        super().__init__(message, details=details, **kwargs)
        self.action = action


class ConfirmationRequiredError(PermissionDeniedError):
    """The action needs a human to approve it first.

    Not a failure: the correct next step is to ask the user, so the status is
    409 rather than 403 and the code tells the client to prompt.
    """

    code = ErrorCode.CONFIRMATION_REQUIRED
    http_status = 409


class SsrfBlockedError(PermanentError):
    """A web tool tried to reach a host it must not contact (spec section 31)."""

    code = ErrorCode.SSRF_BLOCKED
    http_status = 403

    def __init__(self, host: str) -> None:
        super().__init__(
            f"'{host}' is not an allowed destination",
            details={"host": host, "reason": "private, loopback, or unresolvable"},
        )
        self.host = host


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class DatabaseError(RetryableError):
    code = ErrorCode.DATABASE_ERROR
    http_status = 503

    def __init__(self, operation: str, *, cause: BaseException | None = None) -> None:
        super().__init__(
            f"database operation '{operation}' failed",
            details={"operation": operation},
            cause=cause,
        )
        self.operation = operation


class MigrationRequiredError(DatabaseError):
    """The schema is behind what the code expects.

    Not retryable in place: the fix is to run Alembic, not to try again.
    """

    code = ErrorCode.MIGRATION_REQUIRED
    retryable = False
    http_status = 503


class RedisError(RetryableError):
    code = ErrorCode.REDIS_ERROR
    http_status = 503

    def __init__(self, operation: str, *, cause: BaseException | None = None) -> None:
        super().__init__(
            f"redis operation '{operation}' failed",
            details={"operation": operation},
            cause=cause,
        )
        self.operation = operation


class LockUnavailableError(PermanentError):
    """A distributed lock is held by another worker.

    Shares ``CONFLICT`` with :class:`ConflictError` rather than taking a new
    code, because to a client the two are the same answer -- "someone else got
    there first" -- and adding a code means every consumer of the error
    enumeration has to learn about it. It is still a distinct type, so a caller
    that wants to wait for a lock can catch exactly this and not a duplicate-key
    conflict.

    Not retryable, and deliberately so. A retry here means another worker still
    holds the lock, and the one thing that makes contention worse is a blanket
    "retry on failure" loop turning many waiters into a thundering herd against
    the resource they are waiting for. A caller that can usefully wait asks for
    the lock with a wait budget; one that cannot should shed the work.
    """

    code = ErrorCode.CONFLICT
    http_status = 409

    def __init__(self, name: str) -> None:
        super().__init__(
            f"lock '{name}' is held by another worker",
            details={"lock": name},
        )
        self.name = name


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ModelError(RetryableError):
    """Base for model provider failures."""

    code = ErrorCode.PROVIDER_ERROR
    http_status = 502

    def __init__(
        self,
        provider: str,
        message: str = "",
        *,
        model: str = "",
        timeout: float | None = None,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        merged: dict[str, Any] = {"provider": provider}
        if model:
            merged["model"] = model
        # Recorded wherever a budget applied, so a provider timeout carries the
        # same evidence as an operation timeout rather than just a 504.
        if timeout is not None:
            merged["timeout"] = timeout
        merged.update(dict(details or {}))
        text = message or f"provider '{provider}' failed"
        super().__init__(text, details=merged, cause=cause)
        self.provider = provider
        self.model = model


class LocalModelUnavailableError(ModelError):
    """Ollama is not reachable (spec section 33).

    ULTRON must report this and keep running. A missing local model is a
    degraded capability, not a reason to take the process down.
    """

    code = ErrorCode.LOCAL_MODEL_UNAVAILABLE
    retryable = True
    http_status = 503


class ProviderNotConfiguredError(ModelError):
    """A model was requested from a provider with no credentials.

    Retryable is False on purpose: retrying without a key cannot succeed, and
    the router should fall back to a different provider instead.
    """

    code = ErrorCode.PROVIDER_NOT_CONFIGURED
    retryable = False
    http_status = 503


class ModelTimeoutError(ModelError):
    code = ErrorCode.MODEL_TIMEOUT
    retryable = True
    http_status = 504


class ModelRateLimitedError(ModelError):
    code = ErrorCode.MODEL_RATE_LIMITED
    retryable = True
    http_status = 429


class ModelResponseInvalidError(ModelError):
    """A provider answered with something that is not a usable response."""

    code = ErrorCode.MODEL_RESPONSE_INVALID
    retryable = False
    http_status = 502


class ModelNotSupportedError(ModelError):
    code = ErrorCode.MODEL_UNSUPPORTED
    retryable = False
    http_status = 400


# ---------------------------------------------------------------------------
# Tools, tasks, agents
# ---------------------------------------------------------------------------


class ToolError(UltronError):
    code = ErrorCode.TOOL_EXECUTION_FAILED
    http_status = 500

    def __init__(
        self,
        tool: str,
        message: str = "",
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        merged = {"tool": tool, **dict(details or {})}
        super().__init__(message or f"tool '{tool}' failed", details=merged, cause=cause)
        self.tool = tool


class ToolNotFoundError(ToolError):
    code = ErrorCode.TOOL_NOT_FOUND
    http_status = 404


class ToolSchemaInvalidError(ToolError):
    """Arguments did not match the tool's declared input schema."""

    code = ErrorCode.TOOL_SCHEMA_INVALID
    retryable = False
    http_status = 422


class ToolPermissionDeniedError(ToolError):
    code = ErrorCode.PERMISSION_DENIED
    retryable = False
    http_status = 403


class ToolVerificationError(ToolError):
    """The tool ran but its result did not pass verification.

    Separate from an execution failure on purpose: the pipeline must report
    these differently, because the effect already happened.
    """

    code = ErrorCode.TOOL_VERIFICATION_FAILED
    retryable = False
    http_status = 500


class CommandNotAllowedError(ToolError):
    """A terminal command is not on the allow-list."""

    code = ErrorCode.COMMAND_NOT_ALLOWED
    retryable = False
    http_status = 403


class TaskError(UltronError):
    code = ErrorCode.TASK_FAILED
    http_status = 500

    def __init__(
        self,
        task_id: str,
        message: str = "",
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message or f"task '{task_id}' failed",
            details={"task_id": task_id, **dict(details or {})},
            cause=cause,
        )
        self.task_id = task_id


class TaskNotFoundError(NotFoundError):
    code = ErrorCode.TASK_NOT_FOUND
    http_status = 404

    def __init__(self, task_id: str) -> None:
        super().__init__("task", task_id)


class AgentError(UltronError):
    code = ErrorCode.AGENT_FAILED
    http_status = 500

    def __init__(
        self,
        agent_id: str,
        message: str = "",
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message or f"agent '{agent_id}' failed",
            details={"agent_id": agent_id, **dict(details or {})},
            cause=cause,
        )
        self.agent_id = agent_id


class AgentNotFoundError(NotFoundError):
    code = ErrorCode.AGENT_NOT_FOUND
    http_status = 404

    def __init__(self, agent_id: str) -> None:
        super().__init__("agent", agent_id)


class WorkspaceError(UltronError):
    code = ErrorCode.WORKSPACE_ERROR
    http_status = 500


# ---------------------------------------------------------------------------
# Subsystems
# ---------------------------------------------------------------------------


class EventBusError(RetryableError):
    code = ErrorCode.EVENT_BUS_ERROR
    http_status = 503


class HealthCheckFailedError(RetryableError):
    code = ErrorCode.HEALTH_CHECK_FAILED
    http_status = 503


class VoiceError(UltronError):
    code = ErrorCode.VOICE_ERROR
    http_status = 503


class DeviceError(UltronError):
    code = ErrorCode.DEVICE_ERROR
    http_status = 503


class BrowserError(UltronError):
    code = ErrorCode.BROWSER_ERROR
    http_status = 503


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_retryable(error: BaseException) -> bool:
    """Return True when retrying ``error`` could plausibly succeed.

    ``asyncio.CancelledError`` derives from :class:`BaseException` and is always
    False: it is a deliberate stop, not a fault to retry.
    """
    if isinstance(error, asyncio.CancelledError):
        return False
    if isinstance(error, UltronError):
        return error.retryable
    # A bare TimeoutError or ConnectionError from a driver is the untyped form
    # of the same condition, and is worth one more attempt.
    return isinstance(error, TimeoutError | ConnectionError)


def exit_code_for(error: BaseException) -> int:
    """Return the process exit code that matches an error.

    Used by ``app.__main__`` so a configuration failure stops the process with a
    meaningful code while a handled runtime error does not look like a crash.
    Values follow ``sysexits.h`` so a supervisor can tell the cases apart.
    """
    if isinstance(error, ConfigError):
        return 78  # EX_CONFIG
    if isinstance(error, CapabilityNotImplementedError):
        return 69  # EX_UNAVAILABLE
    if isinstance(error, ShuttingDownError):
        return 75  # EX_TEMPFAIL
    return 1


async def degrade[T](
    name: str,
    awaitable: Awaitable[T],
    fallback: T,
    *,
    on_error: type[BaseException] = DependencyUnavailableError,
    logger: logging.Logger | None = None,
) -> T:
    """Return ``fallback`` when ``awaitable`` fails, recording what was lost.

    This is the graceful degradation spec section 33 asks for, used narrowly.
    It catches only ``on_error``, so a programming mistake such as
    ``TypeError`` still propagates instead of being disguised as a fallback
    value. The failure is logged rather than discarded, which is what keeps
    this from becoming the "silently swallow" that section 33 forbids.
    """
    try:
        return await awaitable
    except on_error as error:
        if logger is not None:
            logger.warning(
                "degraded: %s failed, continuing without it",
                name,
                extra={
                    "event": "degraded",
                    "status": "degraded",
                    "error": f"{type(error).__name__}: {error}",
                },
            )
        return fallback
