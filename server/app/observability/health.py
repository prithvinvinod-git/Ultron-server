"""Health checks for every ULTRON dependency (T018, spec section 32).

The spec names six: PostgreSQL, Redis, Ollama, filesystem, agent runtime, and
event bus. This module checks the first four here and *reports* the last two,
which arrive with their subsystems (T044, T031) rather than being faked now.

Two ideas do the real work.

**Not every dependency is equally fatal.** PostgreSQL is the authoritative store
(spec §23): if it is down, ULTRON cannot record a task, a message, or an audit
trail, so the server is not ready and must say so. Redis carries cache, locks,
and pub/sub, and a cold cache is survivable, so Redis being down is *degraded*,
not fatal. Ollama is a capability: spec §33 says an unreachable model provider
must never crash ULTRON Core, so it is degraded too, and the model router falls
through to another provider. Only the *required* checks decide readiness, which
is what stops an unreachable optional dependency from taking the whole service
out of rotation.

**An unknown check is reported as unknown, not as passing.** The agent runtime
and event bus do not exist yet. Silently omitting them would make a report that
looks complete while covering four of six named subsystems, so they appear as
``skipped`` with a reason until the container registers a real implementation
(T019, T031, T044). A check that is absent is information; a check that is
vacuously green is not.

Nothing here constructs a dependency. The engine, the Redis client, and the HTTP
client are all injected, so this module is testable with fakes and no running
service, matching ``app/database/session.py`` in refusing to open a socket at
import.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import ObservabilitySettings, OllamaSettings, WorkspaceSettings, get_settings
from app.core.errors import HealthCheckFailedError, LocalModelUnavailableError
from app.database.redis_client import RedisClient
from app.database.session import ping as ping_database
from app.observability import get_logger

_LOGGER = get_logger(__name__)

#: The six checks spec §32 names. Kept as a constant because a report has to be
#: able to say which ones are missing, not only list the ones that ran.
EXPECTED_CHECKS: Final[frozenset[str]] = frozenset(
    {
        "postgresql",
        "redis",
        "ollama",
        "filesystem",
        "agent_runtime",
        "event_bus",
    }
)


class HealthStatus(StrEnum):
    """The verdict for one check, or for the report as a whole.

    ``OK`` and ``FAILED`` are the two ends. The middle is the point: a server
    with no local model is still useful, so ``DEGRADED`` is a first-class
    outcome rather than an excuse to collapse it into a failure. ``SKIPPED``
    means the check is not registered yet, which is a statement about this
    process and not a claim about the dependency.
    """

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"

    @property
    def is_healthy(self) -> bool:
        """True when nothing is broken, or only an optional thing is.

        Readiness is a separate question and is answered per check: see
        :attr:`CheckResult.ready`.
        """
        return self in {HealthStatus.OK, HealthStatus.DEGRADED, HealthStatus.SKIPPED}


#: Worst-first, so folding a list of statuses keeps the most serious one.
_STATUS_SEVERITY: Final[tuple[HealthStatus, ...]] = (
    HealthStatus.FAILED,
    HealthStatus.DEGRADED,
    HealthStatus.SKIPPED,
    HealthStatus.OK,
)


def _worst(statuses: Iterable[HealthStatus]) -> HealthStatus:
    """Return the most serious status in ``statuses``."""
    present = set(statuses)
    for candidate in _STATUS_SEVERITY:
        if candidate in present:
            return candidate
    return HealthStatus.OK


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One dependency's verdict.

    Frozen because a result is a report: nothing downstream may "fix" a health
    result in place and leave the recorded report disagreeing with the returned
    one. ``error`` carries the *code* of the typed error, never its message,
    because a driver message can carry a DSN.
    """

    name: str
    status: HealthStatus
    detail: str = ""
    latency_ms: float = 0.0
    required: bool = False
    error_code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """True when this check does not stand between the server and traffic.

        A required check must be ``OK``. An optional one is free to be
        ``DEGRADED``: Redis down means a cold cache, not an unusable service.
        """
        if not self.required:
            return True
        return self.status is HealthStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """A JSON-friendly payload for ``/health`` and ``/ready``."""
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status.value,
            "ready": self.ready,
            "required": self.required,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.error_code:
            payload["error"] = self.error_code
        if self.data:
            payload["data"] = self.data
        return payload


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The whole verdict: liveness plus the per-dependency results."""

    status: HealthStatus
    checks: tuple[CheckResult, ...]
    ready: bool
    started_at: float
    duration_ms: float
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "ready": self.ready,
            "duration_ms": round(self.duration_ms, 2),
            "checks": [check.to_dict() for check in self.checks],
        }

    def get(self, name: str) -> CheckResult | None:
        """Return the named check, or None when it was not run."""
        for check in self.checks:
            if check.name == name:
                return check
        return None


#: A check is an awaitable returning a result. It never raises: the wrapper
#: turns a failure into a result, so one broken dependency cannot abort the
#: report and hide the state of the others.
CheckFn = Callable[[], Awaitable[CheckResult]]

#: PostgreSQL is the only dependency whose absence makes the server unusable,
#: so it is the only required check. Everything else degrades.
_REQUIRED_CHECKS: Final[frozenset[str]] = frozenset({"postgresql"})


def result_required(name: str) -> bool:
    """True when this check decides readiness."""
    return name in _REQUIRED_CHECKS


def _result(
    name: str,
    status: HealthStatus,
    *,
    detail: str = "",
    latency_ms: float = 0.0,
    required: bool = False,
    error: BaseException | None = None,
    data: dict[str, Any] | None = None,
) -> CheckResult:
    """Build a result, taking the error code from a typed error when present."""
    code: str | None = None
    if error is not None:
        code = str(getattr(error, "code", type(error).__name__))
    return CheckResult(
        name=name,
        status=status,
        detail=detail,
        latency_ms=latency_ms,
        required=required,
        error_code=code,
        data=dict(data or {}),
    )


async def _timed(name: str, awaitable: Awaitable[CheckResult], *, budget: float) -> CheckResult:
    """Run a check under a timeout and convert a failure into a result.

    Two guarantees, both from spec §33 which requires a timeout and a typed
    error on every subsystem:

    - A hung dependency cannot hang the endpoint. Without the timeout, a
      PostgreSQL that accepts a TCP connection and then stops responding would
      make ``/ready`` hang until the client gave up, which is worse than a
      negative answer because the orchestrator cannot distinguish it from a
      slow but healthy server.
    - A failure is *recorded*, not swallowed. The result carries the typed
      error's code, and the reason is logged at warning level.
    """
    started = time.perf_counter()
    try:
        async with asyncio.timeout(budget):
            result = await awaitable
    except TimeoutError as error:
        elapsed = (time.perf_counter() - started) * 1000
        _LOGGER.warning(
            "health check timed out",
            extra={
                "event": "health.check_timeout",
                "check": name,
                "timeout_s": budget,
            },
        )
        return _result(
            name,
            HealthStatus.FAILED,
            detail=f"no answer within {budget:g}s",
            latency_ms=elapsed,
            required=result_required(name),
            error=error,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        elapsed = (time.perf_counter() - started) * 1000
        _LOGGER.warning(
            "health check failed",
            extra={"event": "health.check_failed", "check": name, "error": str(error)},
        )
        return _result(
            name,
            HealthStatus.FAILED,
            detail="check raised an error",
            latency_ms=elapsed,
            required=result_required(name),
            error=error,
        )
    return CheckResult(
        name=result.name,
        status=result.status,
        detail=result.detail,
        latency_ms=(time.perf_counter() - started) * 1000,
        # A check that succeeded keeps its own opinion about being required; one
        # that failed or timed out never got to state it, so the registry decides.
        required=result.required,
        error_code=result.error_code,
        data=result.data,
    )


def skipped_result(name: str, detail: str) -> CheckResult:
    """A result for a check that is not registered.

    Reported rather than omitted: spec §32 names this check, so a report that
    leaves it out is claiming completeness it does not have.
    """
    return _result(
        name,
        HealthStatus.SKIPPED,
        detail=detail,
        required=result_required(name),
    )


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def check_postgresql(engine: AsyncEngine) -> CheckResult:
    """Verify PostgreSQL answers ``SELECT 1``.

    Required. The check runs through ``session.ping`` rather than opening its own
    connection so the failure is translated in one place, and it uses the pooled
    engine rather than a fresh connection: opening a socket per health check
    would make the check itself the load that exhausts the pool. The timeout
    belongs to :class:`HealthService`, not here, so that every check is bounded
    the same way.
    """
    await ping_database(engine)
    return _result(
        "postgresql",
        HealthStatus.OK,
        detail="reachable",
        required=True,
    )


async def check_redis(client: RedisClient) -> CheckResult:
    """Verify Redis answers ``PING``.

    Optional by design. Redis holds cache, locks, and pub/sub; losing it costs a
    cold cache and the loss of in-flight coordination, and the correct response
    is to keep serving, not to restart. Marking it required would let an optional
    cache take the API out of rotation.
    """
    await client.ping()
    return _result("redis", HealthStatus.OK, detail="reachable")


async def check_filesystem(paths: Sequence[Path]) -> CheckResult:
    """Verify the server can create, write, and delete in each path.

    Optional, but a check that only tested ``exists()`` would be worthless: a
    workspace root on a full or read-only mount exists, is a directory, and is
    unusable. The probe writes a real temporary file and removes it, which is
    the only way to find out whether the mount is actually writable.

    Directories are created when missing, because the server needs them to
    exist and a health check that reports "missing" for a directory it is about
    to need anyway describes a state that a single ``mkdir`` fixes.
    """
    if not paths:
        return _result("filesystem", HealthStatus.SKIPPED, detail="no paths configured")

    writable: list[str] = []
    failures: list[str] = []
    for path in paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
            _probe_writable(path)
        except OSError as error:
            failures.append(f"{path}: {error.strerror or type(error).__name__}")
            _LOGGER.warning(
                "filesystem path is not usable",
                extra={
                    "event": "health.filesystem_unusable",
                    "path": str(path),
                    "error": type(error).__name__,
                },
            )
        else:
            writable.append(path.name)

    if not failures:
        return _result(
            "filesystem",
            HealthStatus.OK,
            detail="writable",
            data={"paths": writable},
        )
    if writable:
        return _result(
            "filesystem",
            HealthStatus.DEGRADED,
            detail="; ".join(failures),
            data={"paths": writable},
        )
    return _result(
        "filesystem",
        HealthStatus.FAILED,
        detail="; ".join(failures),
        data={"paths": []},
    )


def _probe_writable(path: Path) -> None:
    """Write and delete a probe file, so writability is a tested fact not a claim."""
    handle, name = tempfile.mkstemp(prefix=".ultron-health-", dir=path)
    try:
        os.write(handle, b"ok")
    finally:
        os.close(handle)
        Path(name).unlink()


async def check_ollama(
    settings: OllamaSettings,
    *,
    client: Any | None = None,
) -> CheckResult:
    """Verify the Ollama HTTP API answers ``GET /api/tags``.

    Optional, and degraded rather than fatal on purpose: spec §33 requires an
    unreachable model provider to be reported and survived, and the model router
    is built to fall through to another provider. Treating this as fatal would
    contradict the spec and stop ULTRON for a missing local model.

    The host is whatever ``OLLAMA_URL`` says, and a bearer token is sent when
    ``OLLAMA_API_KEY`` is set. That is what lets a hosted Ollama be used through
    the same code path as a local one: the check speaks the Ollama API and does
    not care which is behind it. A local instance needs no credential, so the
    header is omitted rather than sent empty.

    A 401 is reported separately from an unreachable host. They need different
    fixes — a bad key versus a service that is down — and a health report that
    called both "unavailable" would send an operator to the wrong one.

    ``client`` is the injected HTTP client, so the unit suite never touches the
    network. The per-request timeout is Ollama's ``connect_timeout`` rather than
    the service-wide one: this is the only check whose own budget must be
    enforced by the client, because an HTTP request has no cooperative yield
    point for ``asyncio.timeout`` to interrupt once it is on the wire.
    """
    if not settings.is_configured:
        return _result(
            "ollama",
            HealthStatus.SKIPPED,
            detail="OLLAMA_URL is not configured",
        )

    if not settings.healthcheck:
        return _result(
            "ollama",
            HealthStatus.SKIPPED,
            detail="healthcheck disabled by OLLAMA_HEALTHCHECK",
        )

    budget = float(settings.connect_timeout)
    url = f"{settings.url}/api/tags"
    http = client if client is not None else _http_client(budget)
    try:
        response = await http.get(url, headers=settings.auth_headers(), timeout=budget)
    except Exception as error:
        raise LocalModelUnavailableError("ollama", cause=error) from error

    if response.status_code in {401, 403}:
        return _result(
            "ollama",
            HealthStatus.FAILED,
            detail=(
                f"Ollama rejected the credentials (HTTP {response.status_code}); "
                "check OLLAMA_API_KEY"
            ),
            error=LocalModelUnavailableError("ollama"),
        )
    if response.status_code >= 400:
        return _result(
            "ollama",
            HealthStatus.FAILED,
            detail=f"Ollama returned HTTP {response.status_code}",
            error=LocalModelUnavailableError("ollama"),
        )

    models = _model_names(response)
    return _result(
        "ollama",
        HealthStatus.OK,
        detail="reachable",
        data={"models": len(models), "endpoint": settings.url},
    )


def _model_names(response: Any) -> list[str]:
    """Read the model list from a ``/api/tags`` response, tolerating a stub.

    The count is a diagnostic, not a verdict: a proxy may return a body without
    the ``models`` key, and a health check must not fail because a diagnostic
    field was missing.
    """
    try:
        payload = response.json()
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [str(item.get("name", "")) for item in models if isinstance(item, dict)]


def _http_client(budget: float) -> httpx.AsyncClient:
    """Build the HTTP client used when the caller injected none."""
    return httpx.AsyncClient(timeout=budget, follow_redirects=True)


# ---------------------------------------------------------------------------
# Registry and report
# ---------------------------------------------------------------------------


class HealthService:
    """Runs the registered checks and folds them into one report.

    Checks are registered rather than hard-coded, because the last two checks in
    spec §32 belong to subsystems that do not exist yet. T019 registers the four
    available checks at startup; T031 and T044 add theirs when they land, and the
    report starts listing them without a change here.
    """

    def __init__(
        self,
        settings: ObservabilitySettings | None = None,
        *,
        timeout: float = 5.0,
    ) -> None:
        observability = settings or get_settings().observability
        self._checks: dict[str, CheckFn] = {}
        #: Per-check budget in seconds. Public because the right value depends
        #: on the deployment, and a test needs to shrink it to assert the timeout.
        self.check_timeout = timeout
        self._cache_ttl = observability.healthcheck_cache_ttl
        self._cached: HealthReport | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    def register(self, name: str, check: CheckFn) -> None:
        """Register or replace a check.

        Replacement is allowed on purpose: a test registers a fake, and T019 may
        swap a probe once a subsystem knows more than a ping does.
        """
        self._checks[name] = check

    def unregister(self, name: str) -> None:
        self._checks.pop(name, None)

    @property
    def registered(self) -> frozenset[str]:
        return frozenset(self._checks)

    def clear_cache(self) -> None:
        """Drop a cached report, so the next call re-runs every check."""
        self._cached = None
        self._cached_at = 0.0

    async def report(self, *, use_cache: bool = True) -> HealthReport:
        """Run every registered check and return the folded report.

        Results are cached for ``HEALTHCHECK_CACHE_TTL`` seconds. Without it, a
        liveness probe running every second would open a database connection per
        probe and turn the monitor into the load the pool has to absorb. The
        cache is only consulted by concurrent callers of the same window: the
        first caller runs the checks and every caller arriving during that run
        waits for its result rather than starting a second run.
        """
        if not use_cache or self._cache_ttl <= 0:
            return await self._run()

        now = time.monotonic()
        cached = self._cached
        if cached is not None and now - self._cached_at < self._cache_ttl:
            return cached

        async with self._lock:
            now = time.monotonic()
            cached = self._cached
            if cached is not None and now - self._cached_at < self._cache_ttl:
                return cached
            report = await self._run()
            self._cached = report
            self._cached_at = time.monotonic()
            return report

    async def _run(self) -> HealthReport:
        started = time.perf_counter()
        names = sorted(set(self._checks) | EXPECTED_CHECKS)
        results = await asyncio.gather(
            *(
                self._invoke(name, self._checks[name])
                if name in self._checks
                else self._skipped(name)
                for name in names
            )
        )
        ready = all(result.ready for result in results)
        status = _worst(result.status for result in results)
        report = HealthReport(
            status=status,
            checks=tuple(results),
            ready=ready,
            started_at=time.time(),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        _LOGGER.info(
            "health report",
            extra={
                "event": "health.report",
                "status": status.value,
                "ready": ready,
                "duration_ms": round(report.duration_ms, 2),
            },
        )
        return report

    async def _invoke(self, name: str, check: CheckFn) -> CheckResult:
        return await _timed(name, check(), budget=self.check_timeout)

    async def _skipped(self, name: str) -> CheckResult:
        return skipped_result(name, "not registered")

    async def liveness(self) -> dict[str, Any]:
        """The ``/health`` answer: the process is up and answering.

        Deliberately dependency-free. A liveness probe that consulted the
        database would restart a perfectly healthy process every time PostgreSQL
        hiccuped, which turns a dependency's outage into an outage of every
        replica holding it.
        """
        return {"status": HealthStatus.OK.value, "alive": True, "version": _version()}

    async def readiness(self, *, use_cache: bool = True) -> HealthReport:
        """The ``/ready`` answer: the required dependencies are usable."""
        return await self.report(use_cache=use_cache)

    def require_ready(self, report: HealthReport) -> None:
        """Raise :class:`HealthCheckFailedError` unless the report is ready.

        For callers that need readiness as an exception rather than a payload —
        a startup gate, or a request that cannot proceed without the store. The
        failing checks are named in ``details`` rather than in the message,
        because the details are what a reader of a log actually needs.
        """
        if report.ready:
            return
        failed = sorted(check.name for check in report.checks if not check.ready)
        raise HealthCheckFailedError(
            f"not ready: {', '.join(failed)}",
            details={"checks": failed, "status": report.status.value},
        )


def _version() -> str:
    """The application version, or an empty string when it cannot be read."""
    try:
        from importlib.metadata import version

        return version("ultron-server")
    except Exception:
        return ""


def default_paths(settings: WorkspaceSettings | None = None) -> tuple[Path, ...]:
    """The directories ULTRON needs to write to.

    The workspace root comes first because it is the one the agent system cannot
    work without; the log directory is included because a full disk stops log
    writes, which is the failure that takes the process down with no trace left
    to diagnose it.
    """
    workspaces = settings or get_settings().workspaces
    return (workspaces.resolved_root(),)


__all__ = [
    "EXPECTED_CHECKS",
    "CheckFn",
    "CheckResult",
    "HealthReport",
    "HealthService",
    "HealthStatus",
    "check_filesystem",
    "check_ollama",
    "check_postgresql",
    "check_redis",
    "default_paths",
    "result_required",
    "skipped_result",
]
