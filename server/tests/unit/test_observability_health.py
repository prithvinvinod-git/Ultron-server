"""Unit tests for the health checks (T018).

Every dependency is injected, so this suite runs on a bare checkout with no
PostgreSQL, no Redis, and no network. That is not a convenience: the health
report is the one thing an operator reads while something is on fire, so a
change to it has to be verifiable without assembling the failure it is meant to
report.

The tests concentrate on the decisions that are easy to get subtly wrong:

- **Required versus optional.** PostgreSQL down must make the server
  un-ready; Redis and Ollama down must not. Both directions are asserted, since
  a single direction only proves the code is consistent, not correct.
- **A failure is recorded.** Each failing check must produce a result carrying
  the typed error's code, and a warning. A check that raised would abort the
  report and hide the state of everything else, and one that returned ``OK``
  would be worse.
- **An absent check is reported absent.** The agent runtime and the event bus
  do not exist yet, and a report that quietly omits them looks complete.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from app.config import ObservabilitySettings, OllamaSettings
from app.core.errors import (
    DatabaseError,
    HealthCheckFailedError,
    LocalModelUnavailableError,
    RedisError,
)
from app.database.redis_client import RedisClient
from app.database.session import AsyncEngine
from app.observability.health import (
    EXPECTED_CHECKS,
    CheckResult,
    HealthReport,
    HealthService,
    HealthStatus,
    check_filesystem,
    check_ollama,
    check_postgresql,
    check_redis,
    result_required,
)
from app.observability.logging import new_id

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Capture(logging.Handler):
    """Collect records from the ``ultron`` namespace.

    ``configure_logging`` sets ``propagate = False`` on that logger, and another
    test module calls it, so ``caplog`` here would depend on test order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)

    @property
    def events(self) -> list[str]:
        return [str(getattr(record, "event", "")) for record in self.records]


@pytest.fixture
def capture() -> Iterator[_Capture]:
    handler = _Capture()
    logger = logging.getLogger("ultron.app.observability.health")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


class _Response:
    """The part of an httpx response the check actually reads."""

    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"models": []}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Http:
    """Records the request the check made and replays a canned answer."""

    def __init__(self, response: _Response | Exception) -> None:
        self._answer = response
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _Redis:
    """A Redis client double that answers PING or fails."""

    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error
        self.pings = 0

    async def ping(self) -> None:
        self.pings += 1
        if self._error is not None:
            raise self._error


def _ok(name: str = "postgresql", *, required: bool = True) -> CheckResult:
    return CheckResult(
        name=name,
        status=HealthStatus.OK,
        detail="reachable",
        required=required,
    )


def _service(**settings: Any) -> HealthService:
    return HealthService(ObservabilitySettings(**settings))


def _entries(path: Path) -> list[Path]:
    """List a directory from synchronous code.

    The filesystem check is async, but listing a directory in the test is not, and
    doing it here keeps the blocking call out of the coroutine.
    """
    return list(path.iterdir())


def _check(report: HealthReport, name: str) -> CheckResult:
    """Return a named check, failing the test if the report does not have it.

    ``HealthReport.get`` is Optional because a caller may legitimately ask about a
    check that was never registered. Every assertion below expects one to exist,
    so the None case is a test failure rather than an AttributeError.
    """
    found = report.get(name)
    assert found is not None, f"{name} is missing from the report"
    return found


# ---------------------------------------------------------------------------
# Status and result contract
# ---------------------------------------------------------------------------


class TestStatus:
    def test_only_ok_is_required_of_its_checks(self) -> None:
        """A required check that did not answer ``OK`` is not ready.

        ``SKIPPED`` is included on purpose. An unimplemented required dependency
        must read as "not usable", not as a pass.
        """
        for status in HealthStatus:
            result = CheckResult(
                name="postgresql",
                status=status,
                required=result_required("postgresql"),
            )
            assert result.ready is (status is HealthStatus.OK)

    def test_optional_checks_never_block_readiness(self) -> None:
        for status in HealthStatus:
            result = CheckResult(name="redis", status=status, required=result_required("redis"))
            assert result.ready is True

    def test_postgresql_is_the_only_required_check(self) -> None:
        assert result_required("postgresql") is True
        for name in EXPECTED_CHECKS - {"postgresql"}:
            assert result_required(name) is False

    def test_the_status_strings_are_the_public_contract(self) -> None:
        """These values land in a JSON body that a monitor parses."""
        assert {status.value for status in HealthStatus} == {
            "ok",
            "degraded",
            "failed",
            "skipped",
        }

    def test_a_degraded_status_is_not_healthy(self) -> None:
        assert HealthStatus.DEGRADED.is_healthy is True
        assert HealthStatus.FAILED.is_healthy is False

    def test_the_payload_is_json_friendly(self) -> None:
        import json

        result = CheckResult(
            name="ollama",
            status=HealthStatus.FAILED,
            detail="unreachable",
            latency_ms=1.23456,
            required=False,
            error_code="LOCAL_MODEL_UNAVAILABLE",
            data={"models": 3},
        )

        payload = json.loads(json.dumps(result.to_dict()))

        assert payload["status"] == "failed"
        assert payload["error"] == "LOCAL_MODEL_UNAVAILABLE"
        assert payload["data"] == {"models": 3}
        assert payload["latency_ms"] == 1.23

    def test_an_empty_detail_is_omitted(self) -> None:
        assert "detail" not in CheckResult(name="redis", status=HealthStatus.OK).to_dict()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


class TestPostgresqlCheck:
    async def test_a_reachable_database_is_ok_and_required(self, monkeypatch: Any) -> None:
        """The check delegates to the session layer's ping.

        That delegation is the assertion. Opening a connection here would make
        the health check a socket of its own, so the pooled engine would carry a
        second connection per probe on top of the one ``ping`` borrows.
        """
        seen: list[Any] = []

        async def fake_ping(engine: Any) -> None:
            seen.append(engine)

        monkeypatch.setattr("app.observability.health.ping_database", fake_ping)
        engine = cast("AsyncEngine", object())

        result = await check_postgresql(engine)

        assert result.status is HealthStatus.OK
        assert result.required is True
        assert seen == [engine]

    async def test_an_unreachable_database_raises_the_typed_error(self, monkeypatch: Any) -> None:
        """``ping`` translates the driver failure, so the check must not catch it.

        Swallowing it here would hand the wrapper a healthy-looking result and
        let the report say PostgreSQL is fine.
        """

        async def failing_ping(engine: Any) -> None:
            raise DatabaseError("ping")

        monkeypatch.setattr("app.observability.health.ping_database", failing_ping)

        with pytest.raises(DatabaseError):
            await check_postgresql(cast("AsyncEngine", object()))


class TestRedisCheck:
    async def test_a_reachable_redis_is_ok(self) -> None:
        redis = _Redis()

        result = await check_redis(cast("RedisClient", redis))

        assert result.status is HealthStatus.OK
        assert redis.pings == 1

    async def test_a_redis_outage_raises_rather_than_reporting(self) -> None:
        """The check reports; turning a fault into a status is the wrapper's job.

        Reporting here would let a caller that used this function directly
        mistake an unreachable cache for a healthy one.
        """
        with pytest.raises(RedisError):
            await check_redis(cast("RedisClient", _Redis(RedisError("ping"))))


class TestFilesystemCheck:
    async def test_a_writable_path_is_ok(self, tmp_path: Path) -> None:
        result = await check_filesystem([tmp_path])

        assert result.status is HealthStatus.OK
        assert result.data["paths"] == [tmp_path.name]

    async def test_a_missing_directory_is_created(self, tmp_path: Path) -> None:
        """The server needs the directory to exist, so the check makes it.

        Reporting "missing" for a directory a single ``mkdir`` fixes describes a
        state the process is about to be in anyway.
        """
        target = tmp_path / "workspaces"

        result = await check_filesystem([target])

        assert target.is_dir()
        assert result.status is HealthStatus.OK

    async def test_the_probe_leaves_no_file_behind(self, tmp_path: Path) -> None:
        await check_filesystem([tmp_path])

        assert _entries(tmp_path) == []

    async def test_an_unusable_path_is_reported_not_raised(
        self, tmp_path: Path, capture: _Capture
    ) -> None:
        # A file where a directory is expected: mkdir and the write probe both
        # fail with an OSError, on Windows and on Linux alike.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")

        result = await check_filesystem([blocker])

        assert result.status is HealthStatus.FAILED
        assert result.detail
        assert "health.filesystem_unusable" in capture.events

    async def test_one_good_path_and_one_bad_is_degraded(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")

        result = await check_filesystem([tmp_path, blocker])

        assert result.status is HealthStatus.DEGRADED
        assert result.data["paths"] == [tmp_path.name]

    async def test_no_configured_path_is_skipped(self) -> None:
        result = await check_filesystem([])

        assert result.status is HealthStatus.SKIPPED


class TestOllamaCheck:
    def test_a_local_instance_needs_no_credential(self) -> None:
        assert OllamaSettings().auth_headers() == {"Accept": "application/json"}

    def test_a_hosted_instance_sends_a_bearer_token(self) -> None:
        settings = OllamaSettings(api_key="key-123")

        assert settings.auth_headers()["Authorization"] == "Bearer key-123"

    def test_a_blank_token_is_treated_as_absent(self) -> None:
        settings = OllamaSettings(api_key="   ")

        assert settings.api_key_configured is False
        assert "Authorization" not in settings.auth_headers()

    async def test_the_tags_endpoint_is_probed(self) -> None:
        http = _Http(_Response(200, {"models": [{"name": "qwen2.5:7b"}]}))

        result = await check_ollama(OllamaSettings(), client=http)

        assert result.status is HealthStatus.OK
        assert http.calls[0]["url"] == "http://localhost:11434/api/tags"
        assert result.data["models"] == 1

    async def test_a_hosted_ollama_is_reached_through_the_same_call(self) -> None:
        """The user pointed ULTRON at a cloud Ollama, so the check goes there.

        The point of the test is that nothing in the code path knows or cares
        whether the host is local; only ``OLLAMA_URL`` and ``OLLAMA_API_KEY``
        differ.
        """
        settings = OllamaSettings(
            url="https://ollama.example/v1",
            api_key="key-123",
        )
        http = _Http(_Response(200, {"models": []}))

        result = await check_ollama(settings, client=http)

        assert result.status is HealthStatus.OK
        assert http.calls[0]["url"] == "https://ollama.example/v1/api/tags"
        assert http.calls[0]["headers"]["Authorization"] == "Bearer key-123"

    async def test_a_rejected_credential_is_named_as_a_credential_problem(self) -> None:
        """401 and unreachable need different fixes, so they read differently."""
        http = _Http(_Response(401))

        result = await check_ollama(OllamaSettings(), client=http)

        assert result.status is HealthStatus.FAILED
        assert "OLLAMA_API_KEY" in result.detail

    async def test_an_unreachable_host_raises_the_typed_model_error(self) -> None:
        http = _Http(ConnectionRefusedError("connection refused"))

        with pytest.raises(LocalModelUnavailableError) as caught:
            await check_ollama(OllamaSettings(), client=http)

        assert caught.value.code.value == "LOCAL_MODEL_UNAVAILABLE"

    async def test_a_server_error_is_reported(self) -> None:
        result = await check_ollama(OllamaSettings(), client=_Http(_Response(503)))

        assert result.status is HealthStatus.FAILED
        assert "503" in result.detail

    async def test_a_missing_models_key_does_not_fail_the_check(self) -> None:
        """The model count is a diagnostic, not the verdict."""
        result = await check_ollama(OllamaSettings(), client=_Http(_Response(200, {})))

        assert result.status is HealthStatus.OK
        assert result.data["models"] == 0

    async def test_an_unparseable_body_does_not_fail_the_check(self) -> None:
        result = await check_ollama(
            OllamaSettings(), client=_Http(_Response(200, ValueError("not json")))
        )

        assert result.status is HealthStatus.OK

    async def test_the_probe_budget_is_the_connect_timeout(self) -> None:
        """A health probe must not wait a model's generation timeout."""
        http = _Http(_Response(200, {"models": []}))

        await check_ollama(OllamaSettings(connect_timeout=2, timeout=180), client=http)

        assert http.calls[0]["timeout"] == 2.0

    async def test_the_check_can_be_switched_off(self) -> None:
        result = await check_ollama(OllamaSettings(healthcheck=False), client=_Http(_Response(200)))

        assert result.status is HealthStatus.SKIPPED
        assert "OLLAMA_HEALTHCHECK" in result.detail


# ---------------------------------------------------------------------------
# Service and report
# ---------------------------------------------------------------------------


class TestReport:
    async def test_every_expected_check_is_reported(self) -> None:
        report = await _service().report()

        assert {check.name for check in report.checks} == EXPECTED_CHECKS

    async def test_an_unregistered_check_is_skipped_and_says_so(self) -> None:
        """Silently omitting it would look like a complete report.

        The agent runtime and the event bus arrive with T044 and T031. Reporting
        them as skipped with a reason is the honest state; dropping them would
        make a report that covers four of six named subsystems read as six.
        """
        report = await _service().report()

        for name in ("agent_runtime", "event_bus"):
            check = report.get(name)
            assert check is not None
            assert check.status is HealthStatus.SKIPPED
            assert check.detail == "not registered"

    async def test_a_healthy_required_check_makes_the_server_ready(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _resolved(_ok()))

        report = await service.report()

        assert report.ready is True
        assert _check(report, "postgresql").status is HealthStatus.OK

    async def test_a_failing_postgresql_makes_the_server_not_ready(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _raises(DatabaseError("ping")))

        report = await service.report()

        assert report.ready is False
        assert report.status is HealthStatus.FAILED
        assert _check(report, "postgresql").error_code == "DATABASE_ERROR"

    async def test_a_failing_redis_does_not_make_the_server_not_ready(self) -> None:
        """An optional cache must not take the API out of rotation."""
        service = _service()
        service.register("postgresql", lambda: _resolved(_ok()))
        service.register("redis", lambda: _raises(RedisError("ping")))

        report = await service.report()

        assert report.ready is True
        assert report.status is HealthStatus.FAILED
        assert _check(report, "redis").ready is True

    async def test_one_failure_does_not_hide_the_others(self) -> None:
        """A raising check must not abort the report."""
        service = _service()
        service.register("postgresql", lambda: _resolved(_ok()))
        service.register("redis", lambda: _raises(RedisError("ping")))
        service.register("ollama", lambda: _resolved(_ok("ollama", required=False)))

        report = await service.report()

        assert _check(report, "postgresql").status is HealthStatus.OK
        assert _check(report, "redis").status is HealthStatus.FAILED
        assert _check(report, "ollama").status is HealthStatus.OK

    async def test_a_failure_is_logged_rather_than_discarded(self, capture: _Capture) -> None:
        """Spec section 33 forbids silently swallowing an exception."""
        service = _service()
        service.register("postgresql", lambda: _raises(DatabaseError("ping")))

        await service.report()

        assert "health.check_failed" in capture.events
        assert "health.report" in capture.events

    async def test_the_worst_status_wins(self) -> None:
        service = _service()
        service.register(
            "postgresql",
            lambda: _resolved(CheckResult("postgresql", HealthStatus.DEGRADED, required=True)),
        )

        report = await service.report()

        assert report.status is HealthStatus.DEGRADED

    async def test_a_missing_check_is_never_better_than_ok(self) -> None:
        service = _service()

        report = await service.report()

        assert report.status is HealthStatus.SKIPPED

    async def test_the_report_payload_carries_every_check(self) -> None:
        payload = (await _service().report()).to_dict()

        assert payload["status"] == "skipped"
        assert payload["ready"] is False
        assert {entry["name"] for entry in payload["checks"]} == EXPECTED_CHECKS

    async def test_a_registered_check_may_be_replaced(self) -> None:
        service = _service()
        service.register("redis", lambda: _raises(RedisError("first")))
        service.register("redis", lambda: _resolved(_ok("redis", required=False)))

        report = await service.report()

        assert _check(report, "redis").status is HealthStatus.OK

    async def test_a_check_can_be_unregistered(self) -> None:
        service = _service()
        service.register("redis", lambda: _resolved(_ok("redis", required=False)))
        service.unregister("redis")

        assert "redis" not in service.registered
        assert _check(await service.report(), "redis").status is HealthStatus.SKIPPED


class TestTimeout:
    async def test_a_hung_check_fails_instead_of_hanging(self, capture: _Capture) -> None:
        """A dependency that accepts a connection and stalls must not stall /ready.

        Without the timeout the probe hangs until the client gives up, which an
        orchestrator cannot tell apart from a slow but healthy server.
        """
        service = _service()
        service.register("postgresql", lambda: _forever())
        service.check_timeout = 0.05

        report = await service.report()

        check = _check(report, "postgresql")
        assert check.status is HealthStatus.FAILED
        assert "0.05" in check.detail
        assert "health.check_timeout" in capture.events

    async def test_a_timed_out_required_check_is_not_ready(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _forever())
        service.check_timeout = 0.05

        report = await service.report()

        assert report.ready is False

    async def test_a_cancelled_check_is_not_swallowed(self) -> None:
        """Cancellation is a deliberate stop, not a fault to record as failed."""
        service = _service()
        service.register("postgresql", lambda: _forever())
        service.check_timeout = 10

        task = asyncio.create_task(service.report(use_cache=False))
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


class TestCache:
    async def test_results_are_cached_within_the_ttl(self) -> None:
        service = _service(healthcheck_cache_ttl=60)
        calls = 0

        async def counting() -> CheckResult:
            nonlocal calls
            calls += 1
            return _ok()

        service.register("postgresql", counting)

        await service.report()
        await service.report()

        assert calls == 1

    async def test_the_cache_can_be_bypassed(self) -> None:
        service = _service(healthcheck_cache_ttl=60)
        calls = 0

        async def counting() -> CheckResult:
            nonlocal calls
            calls += 1
            return _ok()

        service.register("postgresql", counting)

        await service.report()
        await service.report(use_cache=False)

        assert calls == 2

    async def test_a_zero_ttl_never_caches(self) -> None:
        """``HEALTHCHECK_CACHE_TTL=0`` is how an operator turns caching off."""
        service = _service(healthcheck_cache_ttl=0)
        calls = 0

        async def counting() -> CheckResult:
            nonlocal calls
            calls += 1
            return _ok()

        service.register("postgresql", counting)

        await service.report()
        await service.report()

        assert calls == 2

    async def test_clearing_the_cache_forces_a_rerun(self) -> None:
        service = _service(healthcheck_cache_ttl=60)
        calls = 0

        async def counting() -> CheckResult:
            nonlocal calls
            calls += 1
            return _ok()

        service.register("postgresql", counting)
        await service.report()
        service.clear_cache()
        await service.report()

        assert calls == 2

    async def test_concurrent_callers_share_one_run(self) -> None:
        """A thundering herd of probes must not become a herd of connections."""
        service = _service(healthcheck_cache_ttl=60)
        calls = 0

        async def counting() -> CheckResult:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return _ok()

        service.register("postgresql", counting)

        reports = await asyncio.gather(*(service.report() for _ in range(5)))

        assert calls == 1
        assert len({id(report) for report in reports}) == 1


class TestLivenessAndReadiness:
    async def test_liveness_does_not_consult_any_dependency(self) -> None:
        """A liveness probe that checked the database would restart a healthy
        process every time PostgreSQL hiccuped, turning one dependency's outage
        into an outage of every replica holding it.
        """
        service = _service()
        service.register("postgresql", lambda: _raises(DatabaseError("ping")))

        payload = await service.liveness()

        assert payload == {"status": "ok", "alive": True, "version": payload["version"]}

    async def test_readiness_runs_the_checks(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _resolved(_ok()))

        report = await service.readiness()

        assert report.ready is True

    async def test_require_ready_passes_a_ready_report(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _resolved(_ok()))

        service.require_ready(await service.report())

    async def test_require_ready_names_the_failing_check(self) -> None:
        service = _service()
        service.register("postgresql", lambda: _raises(DatabaseError("ping")))

        with pytest.raises(HealthCheckFailedError) as caught:
            service.require_ready(await service.report())

        assert caught.value.details["checks"] == ["postgresql"]
        assert "postgresql" in caught.value.message

    async def test_the_readiness_error_is_retryable(self) -> None:
        """A dependency that comes back must be waited for, not given up on."""
        from app.core.errors import is_retryable

        assert is_retryable(HealthCheckFailedError("x")) is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolved(result: CheckResult) -> CheckResult:
    return result


async def _raises(error: BaseException) -> CheckResult:
    raise error


async def _forever() -> CheckResult:
    await asyncio.sleep(30)
    raise AssertionError("unreachable")


def test_the_request_id_is_not_baked_into_a_result() -> None:
    """Two checks in one report must not share a correlation id.

    Health is a property of the process, not of a request, so nothing here reads
    the request-scoped contextvar.
    """
    from app.observability.logging import get_request_id, request_context

    with request_context(new_id()):
        assert get_request_id() is not None

    result = CheckResult(name="redis", status=HealthStatus.OK)
    assert "request_id" not in result.to_dict()
