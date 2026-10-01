"""Unit tests for the Redis client (T017).

These run on a bare checkout with no Redis, by injecting ``fakeredis``. The
client is built to be constructed without connecting, so there is no reason for
these tests to need a server -- and a suite that needed one would not run on the
checkout that most contributions happen on.

The lock tests are the reason this file is long. ``SET NX`` followed by ``DEL``
looks correct and is not: a holder that stalls past its lease can delete a lock
that has since been granted to somebody else, which is exactly the bug the
compare-and-delete script exists to prevent. That failure mode is invisible in a
unit test that only checks "acquire, release, key gone", so the tests below
deliberately let a lease expire underneath its holder and then assert that the
loser's release leaves the *winner's* lock intact.

A note on ``fakeredis`` and Lua: the lock's release is a Lua script, and
``fakeredis`` executes Lua only when ``lupa`` is installed. The dependency is
declared as ``fakeredis[lua]`` for that reason, and a test asserts the capability
is present so the failure is a clear message rather than a puzzling
"unknown command 'eval'" from whichever test happened to touch a lock first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import patch

import fakeredis
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as DriverConnectionError

from app.config import RedisSettings
from app.core.errors import ErrorCode, LockUnavailableError, RedisError
from app.database.redis_client import (
    DEFAULT_LEASE_MS,
    KEY_PREFIX,
    RedisClient,
    create_redis_client,
)

pytestmark = pytest.mark.unit

#: A DSN with a password in it, used to prove no log line or error text leaks it.
SECRET = "hunter2"
DSN_WITH_PASSWORD = f"redis://ultron:{SECRET}@localhost:6379/0"


# ---------------------------------------------------------------------------
# Fixtures and doubles
# ---------------------------------------------------------------------------


class _Capture(logging.Handler):
    """Collect records without relying on propagation to the root logger.

    ``app.observability.logging.configure_logging`` sets ``propagate = False`` on
    the ``ultron`` logger, and another test file calls it. A ``caplog`` assertion
    here would therefore pass or fail depending on alphabetical test order, so
    the handler is attached to the namespace directly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self) -> list[str]:
        return [str(getattr(record, "event", "")) for record in self.records]


def _render(logs: _Capture) -> str:
    """Return everything a captured record exposes, as one string.

    ``extra=`` keys are set as attributes on the record rather than collected
    under an ``extra`` dict, so the whole ``__dict__`` is what has to be searched
    for a secret. ``getMessage()`` is included because the format arguments are
    only interpolated there.
    """
    parts = [record.getMessage() for record in logs.records]
    parts.append(json.dumps([record.__dict__ for record in logs.records], default=str))
    return " ".join(parts)


def _dsn_fields(logs: _Capture) -> list[str]:
    return [str(record.__dict__["dsn"]) for record in logs.records if "dsn" in record.__dict__]


async def _drain(messages: AsyncGenerator[tuple[str, Any]]) -> None:
    """Consume a message stream forever, the way a real subscriber runs."""
    async for _message in messages:
        pass


class _PubSubSpy:
    """Records the :class:`PubSub` handles the wrapper creates, still real ones.

    ``pubsub_numsub`` cannot be used to assert cleanup here: ``fakeredis`` answers
    it from the calling connection's own view, so it still reports a subscription
    after the handle is closed, and a second connection reports none while one is
    live. A spy over the real handle gives a deterministic signal instead.
    """

    def __init__(self, raw: Redis) -> None:
        self._raw = raw
        self.handles: list[Any] = []

    def __enter__(self) -> _PubSubSpy:
        # Reached through ``object.__setattr__`` because assigning over a method
        # is exactly what this spy exists to do, and mypy rightly objects to it
        # everywhere else.
        self._original = self._raw.pubsub
        object.__setattr__(self._raw, "pubsub", self._record)
        return self

    def _record(self, **_kwargs: Any) -> Any:
        handle = self._original(**_kwargs)
        self.handles.append(handle)
        return handle

    def __exit__(self, *_exc: object) -> None:
        object.__setattr__(self._raw, "pubsub", self._original)

    @property
    def last(self) -> Any:
        assert self.handles, "no PubSub handle was created"
        return self.handles[-1]

    async def wait_until_live(self, budget: float = 5.0) -> Any:
        """Return the handle once it is actually subscribed.

        The subscription happens on the generator's first advance, so a task that
        has been created is not yet subscribed and a message published now would
        be missed.
        """
        deadline = asyncio.get_running_loop().time() + budget
        while asyncio.get_running_loop().time() < deadline:
            if self.handles and self.last.subscribed:
                return self.last
            await asyncio.sleep(0.02)
        msg = "the subscription never came up"
        raise AssertionError(msg)


@pytest.fixture
def logs() -> Iterator[_Capture]:
    """Capture the ``ultron`` logger for the duration of one test."""
    capture = _Capture()
    logger = logging.getLogger("ultron")
    previous = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        yield capture
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous)


@pytest.fixture
async def raw() -> AsyncIterator[Redis]:
    """A bare fakeredis client, for asserting on keys the wrapper never sees."""
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client(raw: Redis) -> AsyncIterator[RedisClient]:
    """A wrapper over :func:`raw` with a one-hour default TTL."""
    wrapper = RedisClient(raw, default_ttl=3600)
    try:
        yield wrapper
    finally:
        await wrapper.aclose()


class _FailingCommand:
    """A driver call that fails, whichever way it is consumed.

    ``redis.asyncio`` is consumed three ways in this module -- awaited
    (``get``), iterated (``scan_iter``), and called-then-awaited (``eval``) -- so a
    double that only supports ``await`` would let ``async for`` iterate a coroutine
    and raise the wrong error. Each path fails with the same exception instead.
    """

    def _error(self) -> DriverConnectionError:
        # The real message contains the DSN, password and all, which is exactly
        # what the wrapper must never let through.
        return DriverConnectionError(
            f"Error 111 connecting to {DSN_WITH_PASSWORD}. Connection refused."
        )

    async def _raise(self) -> None:
        raise self._error()

    def __await__(self) -> Any:
        return self._raise().__await__()

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> None:
        raise self._error()

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        return self


class _BrokenRedis:
    """A driver double that fails every command the way a real outage does.

    ``__getattr__`` hands out :class:`_FailingCommand` for anything, so the
    wrapper's error translation is exercised through the same ``except
    DriverError`` path production uses rather than a hand-raised ULTRON error.
    """

    def __getattr__(self, _name: str) -> _FailingCommand:
        return _FailingCommand()


@pytest.fixture
def broken() -> RedisClient:
    """A wrapper whose every command fails."""
    return RedisClient(cast("Redis", _BrokenRedis()), default_ttl=3600)


# ---------------------------------------------------------------------------
# Environment capability
# ---------------------------------------------------------------------------


def test_fakeredis_can_run_lua() -> None:
    """The lock's release is Lua, so ``lupa`` must be installed.

    Without it ``fakeredis`` answers ``EVAL`` with "unknown command", and the
    failure surfaces inside whichever lock test runs first -- a long way from the
    missing dependency.
    """
    try:
        import lupa  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:  # pragma: no cover - a setup failure, not a code path
        pytest.fail(
            "fakeredis cannot run Lua without lupa; install the dev extra "
            "'fakeredis[lua]' (declared in pyproject.toml)"
        )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


async def test_json_round_trip(client: RedisClient) -> None:
    value = {"projects": ["a", "b"], "count": 2, "nested": {"ok": True, "ratio": 0.5}}
    await client.set_json("cache:projects", value)
    assert await client.get_json("cache:projects") == value


async def test_json_round_trip_preserves_null_and_empty(client: RedisClient) -> None:
    """``None`` and empty containers are values, not absences.

    A cached ``None`` is indistinguishable from a miss on read, which is a real
    limitation of caching JSON, but the *write* must still work: a caller storing
    a legitimately empty result should not hit a ``TypeError``.
    """
    await client.set_json("cache:null", None)
    await client.set_json("cache:list", [])
    await client.set_json("cache:dict", {})
    assert await client.get_json("cache:null") is None
    assert await client.get_json("cache:list") == []
    assert await client.get_json("cache:dict") == {}


async def test_missing_key_is_a_miss_not_an_error(client: RedisClient) -> None:
    assert await client.get_json("cache:absent") is None


async def test_keys_are_namespaced(client: RedisClient, raw: Redis) -> None:
    await client.set_json("cache:x", 1)
    assert await raw.exists(f"{KEY_PREFIX}cache:x") == 1
    # The unprefixed name must be untouched, so two applications sharing a
    # Redis cannot collide.
    assert await raw.exists("cache:x") == 0


async def test_default_ttl_is_applied(client: RedisClient) -> None:
    await client.set_json("cache:x", 1)
    assert 0 < await client.ttl("cache:x") <= 3600


async def test_explicit_ttl_overrides_the_default(client: RedisClient) -> None:
    await client.set_json("cache:x", 1, ttl=120)
    assert 0 < await client.ttl("cache:x") <= 120


async def test_missing_ttl_is_written_unbounded_and_warned(
    client: RedisClient, logs: _Capture
) -> None:
    """A non-positive TTL means "no expiry", and the omission is logged.

    Redis reclaims an unbounded key only under memory pressure, so a key written
    without one is a leak the operator cannot see.
    """
    await client.set_json("cache:x", 1, ttl=0)
    assert await client.ttl("cache:x") == -1
    assert "redis.no_ttl" in logs.events()


async def test_ttl_reports_redis_conventions(client: RedisClient) -> None:
    assert await client.ttl("cache:absent") == -2


async def test_text_round_trip(client: RedisClient) -> None:
    await client.set_text("agent:1:heartbeat", "2026-01-01T00:00:00Z", ttl=30)
    assert await client.get_text("agent:1:heartbeat") == "2026-01-01T00:00:00Z"
    assert await client.get_text("agent:2:heartbeat") is None
    assert 0 < await client.ttl("agent:1:heartbeat") <= 30


async def test_delete_removes_only_the_named_keys(client: RedisClient) -> None:
    await client.set_json("cache:a", 1)
    await client.set_json("cache:b", 2)
    assert await client.delete("cache:a", "cache:b", "cache:never-written") == 2
    assert not await client.exists("cache:a")


async def test_delete_with_no_keys_is_a_no_op(client: RedisClient) -> None:
    assert await client.delete() == 0


async def test_delete_prefix_leaves_other_keys_alone(client: RedisClient, raw: Redis) -> None:
    await client.set_json("session:1", {"a": 1})
    await client.set_json("session:2", {"a": 2})
    await client.set_json("cache:other", 1)
    await raw.set("foreign:key", "not ours")

    assert await client.delete_prefix("session:") == 2

    assert not await client.exists("session:1")
    assert not await client.exists("session:2")
    assert await client.exists("cache:other")
    assert await raw.exists("foreign:key") == 1


async def test_delete_prefix_batches_a_large_sweep(client: RedisClient) -> None:
    """More keys than one batch holds, so the batching branch is exercised."""
    for index in range(_DELETE_BATCH_FOR_TEST + 5):
        await client.set_json(f"session:{index}", index)
    assert await client.delete_prefix("session:") == _DELETE_BATCH_FOR_TEST + 5


#: Small enough that the batching loop runs several iterations, large enough to
#: stay fast.
_DELETE_BATCH_FOR_TEST = 250


async def test_exists(client: RedisClient) -> None:
    assert not await client.exists("cache:x")
    await client.set_json("cache:x", 1)
    assert await client.exists("cache:x")


# ---------------------------------------------------------------------------
# JSON contract
# ---------------------------------------------------------------------------


async def test_unserialisable_value_fails_at_the_write(client: RedisClient) -> None:
    """A value JSON cannot hold is refused loudly rather than stringified.

    The lenient alternative caches ``str(value)`` and reads it back as a string
    with no way left to tell it was ever a ``set``.
    """
    with pytest.raises(ValueError, match="not JSON-serialisable"):
        await client.set_json("cache:x", {1, 2, 3})
    assert not await client.exists("cache:x")


async def test_circular_value_is_reported_distinctly(client: RedisClient) -> None:
    value: dict[str, Any] = {}
    value["self"] = value
    with pytest.raises(ValueError, match="circular"):
        await client.set_json("cache:x", value)


async def test_datetime_and_uuid_are_rejected_not_coerced(client: RedisClient) -> None:
    """The contract is JSON-native or nothing.

    The lenient alternative -- ``default=str`` -- would accept these and hand back
    a string that looks like a ``datetime`` but is not, and a caller comparing it
    to a real one would fail somewhere far from the cache. Refusing at the write
    puts the error next to the code that caused it.
    """
    for value in (datetime(2026, 1, 1, tzinfo=UTC), uuid.UUID(int=1), {1, 2}):
        with pytest.raises(ValueError, match="not JSON-serialisable"):
            await client.set_json("cache:x", {"v": value})


async def test_an_explicitly_converted_value_comes_back_as_the_string_it_was(
    client: RedisClient,
) -> None:
    """Conversion is the caller's job, and it is visible in the source.

    This is the documented happy path for a timestamp: the caller decides the
    format, so the cache does not silently impose one.
    """
    at = datetime(2026, 1, 1, tzinfo=UTC)
    await client.set_json("cache:x", {"at": at.isoformat(), "id": str(uuid.UUID(int=1))})
    restored = await client.get_json("cache:x")
    assert restored == {"at": "2026-01-01T00:00:00+00:00", "id": str(uuid.UUID(int=1))}
    assert isinstance(restored["at"], str)


async def test_tuple_round_trips_as_a_list(client: RedisClient) -> None:
    """The one silent type change, pinned deliberately.

    JSON has no distinct tuple type, so this is the case where a caller can get
    back something of a different type without being told. Recording it means a
    move to msgpack is a visible change rather than a difference nobody notices.
    """
    await client.set_json("cache:x", {"pair": (1, 2)})
    restored = await client.get_json("cache:x")
    assert restored == {"pair": [1, 2]}
    assert isinstance(restored["pair"], list)


async def test_unparseable_stored_value_is_a_miss(
    client: RedisClient, raw: Redis, logs: _Capture
) -> None:
    """A corrupt or foreign value must not turn a read into an outage.

    The key is disposable, so treating the value as absent is recoverable, and a
    caller that merely wanted to read it should not be made to fail.
    """
    await raw.set(f"{KEY_PREFIX}cache:x", b"not json at all")
    assert await client.get_json("cache:x") is None
    assert "redis.value_unparseable" in logs.events()


async def test_a_value_written_as_json_still_reads_back(client: RedisClient, raw: Redis) -> None:
    await raw.set(f"{KEY_PREFIX}cache:x", json.dumps({"fine": True}))
    assert await client.get_json("cache:x") == {"fine": True}


@pytest.mark.parametrize("key", ["", "has space", "tab\there", "new\nline"])
async def test_invalid_keys_are_refused(client: RedisClient, key: str) -> None:
    with pytest.raises(ValueError):
        await client.get_json(key)


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


async def test_incr_applies_the_window_on_creation(client: RedisClient) -> None:
    assert await client.incr("rate:login:1", ttl=60) == 1
    assert 0 < await client.ttl("rate:login:1") <= 60


async def test_incr_does_not_extend_the_window_on_later_hits(client: RedisClient) -> None:
    """Traffic must not keep a rate-limit window alive forever."""
    await client.incr("rate:login:1", ttl=60)
    first = await client.ttl("rate:login:1")
    await asyncio.sleep(1.1)
    await client.incr("rate:login:1", ttl=60)
    second = await client.ttl("rate:login:1")
    assert second < first


async def test_incr_refuses_an_unbounded_window(client: RedisClient) -> None:
    """A counter with no expiry is a rate limit that never resets."""
    with pytest.raises(ValueError, match="positive ttl"):
        await client.incr("rate:login:1", ttl=0)
    assert not await client.exists("rate:login:1")


# ---------------------------------------------------------------------------
# Cache failure semantics
# ---------------------------------------------------------------------------


async def test_get_json_raises_on_an_outage(broken: RedisClient) -> None:
    with pytest.raises(RedisError) as caught:
        await broken.get_json("cache:x")
    assert caught.value.code is ErrorCode.REDIS_ERROR
    assert caught.value.retryable is True
    assert caught.value.http_status == 503
    assert caught.value.operation == "get (cache:x)"


async def test_error_text_never_carries_the_dsn(broken: RedisClient) -> None:
    with pytest.raises(RedisError) as caught:
        await broken.get_json("cache:x")
    assert SECRET not in str(caught.value)
    assert SECRET not in json.dumps(caught.value.to_dict())


async def test_error_to_dict_reports_only_the_cause_type(broken: RedisClient) -> None:
    with pytest.raises(RedisError) as caught:
        await broken.get_json("cache:x")
    payload = caught.value.to_dict()
    assert payload["cause"] == "ConnectionError"
    assert SECRET not in json.dumps(payload)


async def test_get_or_none_treats_an_outage_as_a_miss(broken: RedisClient, logs: _Capture) -> None:
    assert await broken.get_or_none("cache:x") is None
    assert "redis.degraded" in logs.events()


async def test_degraded_read_still_surfaces_the_outage(broken: RedisClient, logs: _Capture) -> None:
    """A tolerated read still has to be visible, or an outage is invisible."""
    await broken.get_or_none("cache:x")
    assert "redis.degraded" in logs.events()
    assert SECRET not in _render(logs)


async def test_get_or_none_does_not_swallow_a_programming_error(broken: RedisClient) -> None:
    """``degrade``-style tolerance must catch the fault, not every exception.

    A bad key is a bug in the caller. Swallowing it would return ``None`` forever
    and look exactly like a cache miss.
    """
    with pytest.raises(ValueError):
        await broken.get_or_none("has space")


@pytest.mark.parametrize(
    ("call", "operation"),
    [
        (lambda c: c.set_json("cache:x", 1), "set (cache:x)"),
        (lambda c: c.get_text("cache:x"), "get_text (cache:x)"),
        (lambda c: c.set_text("cache:x", "v"), "set_text (cache:x)"),
        (lambda c: c.delete("cache:x"), "delete"),
        (lambda c: c.delete_prefix("cache:"), "delete_prefix (cache:)"),
        (lambda c: c.exists("cache:x"), "exists (cache:x)"),
        (lambda c: c.ttl("cache:x"), "ttl (cache:x)"),
        (lambda c: c.incr("rate:x", ttl=5), "incr (rate:x)"),
        (lambda c: c.acquire_lock("job"), "lock (job)"),
        (lambda c: c.publish("chan", {"a": 1}), "publish (chan)"),
        (lambda c: c.ping(), "ping"),
    ],
)
async def test_every_operation_names_itself_when_redis_is_down(
    broken: RedisClient, call: Any, operation: str
) -> None:
    """Each boundary reports which operation failed.

    ``operation`` is the only diagnostic a ``RedisError`` carries, so an
    unattributed one would leave a failure with no way to locate it.
    """
    with pytest.raises(RedisError) as caught:
        await call(broken)
    assert caught.value.operation == operation


async def test_aclose_tolerates_a_broken_connection(broken: RedisClient) -> None:
    """Shutdown must not fail because Redis went away first."""
    await broken.aclose()


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


async def test_lock_round_trip(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    assert await client.exists("lock:job:1")
    assert await handle.release() is True
    assert not await client.exists("lock:job:1")


async def test_a_freed_lock_is_reusable(client: RedisClient) -> None:
    """Release must actually free the key, or a lock is a one-shot dead bolt.

    Asserting only that release returns ``True`` would pass against a no-op.
    """
    first = await client.acquire_lock("job:1")
    assert first is not None
    assert await first.release() is True
    second = await client.acquire_lock("job:1")
    assert second is not None
    assert second.key == first.key
    await second.release()


async def test_lock_key_is_namespaced(client: RedisClient, raw: Redis) -> None:
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    assert handle.key == f"{KEY_PREFIX}lock:job:1"
    assert await raw.exists(handle.key) == 1


async def test_lock_uses_the_default_lease(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    assert handle.lease_ms == DEFAULT_LEASE_MS


async def test_second_acquisition_is_refused_while_held(client: RedisClient) -> None:
    held = await client.acquire_lock("job:1")
    assert held is not None
    assert await client.acquire_lock("job:1") is None
    assert await client.acquire_lock("job:1", wait_ms=0) is None
    await held.release()


async def test_waiting_acquisition_succeeds_once_released(client: RedisClient) -> None:
    held = await client.acquire_lock("job:1")
    assert held is not None

    async def _release_soon() -> None:
        await asyncio.sleep(0.1)
        await held.release()

    releaser = asyncio.create_task(_release_soon())
    try:
        second = await client.acquire_lock("job:1", wait_ms=2000)
    finally:
        await releaser
    assert second is not None
    assert await second.release() is True


async def test_waiting_gives_up_after_the_budget(client: RedisClient) -> None:
    held = await client.acquire_lock("job:1")
    assert held is not None
    assert await client.acquire_lock("job:1", wait_ms=120) is None
    await held.release()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"lease_ms": 0}, "lease_ms must be positive"),
        ({"lease_ms": -1}, "lease_ms must be positive"),
        ({"wait_ms": -1}, "wait_ms must not be negative"),
    ],
)
async def test_lock_arguments_are_validated(
    client: RedisClient, kwargs: dict[str, int], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        await client.acquire_lock("job:1", **kwargs)


async def test_release_is_idempotent(client: RedisClient) -> None:
    """``release`` in a ``finally`` must not fail because the body released it."""
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    assert await handle.release() is True
    assert await handle.release() is False
    assert handle.released is True


async def test_expired_holder_cannot_release_the_new_owners_lock(client: RedisClient) -> None:
    """The safety property the whole design exists for.

    The first holder's lease runs out and a second worker takes the lock. The
    first holder then finishes and releases. With a bare ``DEL`` -- the obvious
    implementation -- that delete frees the *second* worker's lock and a third
    worker walks in believing it holds exclusive access. The compare-and-delete
    must refuse.
    """
    stale = await client.acquire_lock("job:1", lease_ms=60)
    assert stale is not None

    await asyncio.sleep(0.15)

    winner = await client.acquire_lock("job:1", lease_ms=10_000)
    assert winner is not None, "the lease should have expired and freed the lock"

    # The stale holder releases. It must not touch the winner's key.
    assert await stale.release() is False
    assert await client.exists("lock:job:1"), "the winner's lock was deleted by a stale holder"

    # And the winner still genuinely owns it: nobody else can take it.
    assert await client.acquire_lock("job:1") is None
    assert await winner.release() is True


async def test_expired_release_is_logged(client: RedisClient, logs: _Capture) -> None:
    """Losing a lock is a real fault the operator should see."""
    stale = await client.acquire_lock("job:1", lease_ms=50)
    assert stale is not None
    await asyncio.sleep(0.12)
    assert await stale.release() is False
    assert "redis.lock_lease_expired" in logs.events()


async def test_extend_raises_the_lease(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1", lease_ms=60_000)
    assert handle is not None
    assert await handle.extend(120_000) is True
    # ``ttl`` is seconds; ``extend`` took milliseconds.
    assert 100 < await client.ttl("lock:job:1") <= 120


async def test_extend_defaults_to_the_original_lease(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1", lease_ms=90_000)
    assert handle is not None
    assert await handle.extend() is True
    assert 60 < await client.ttl("lock:job:1") <= 90


async def test_extend_refuses_a_lock_we_have_lost(client: RedisClient) -> None:
    """Extending a lock we no longer hold would extend somebody else's."""
    stale = await client.acquire_lock("job:1", lease_ms=50)
    assert stale is not None
    await asyncio.sleep(0.12)
    winner = await client.acquire_lock("job:1", lease_ms=60_000)
    assert winner is not None
    assert await stale.extend(600_000) is False
    assert 30 < await client.ttl("lock:job:1") <= 60
    await winner.release()


async def test_extend_after_release_is_a_no_op(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    await handle.release()
    assert await handle.extend() is False


async def test_extend_validates_its_lease(client: RedisClient) -> None:
    handle = await client.acquire_lock("job:1")
    assert handle is not None
    with pytest.raises(ValueError, match="lease_ms must be positive"):
        await handle.extend(0)


async def test_lock_context_manager_releases(client: RedisClient) -> None:
    async with client.lock("job:1") as handle:
        # `exists` namespaces its own argument, so the already-namespaced handle
        # key has to be checked on the driver directly.
        assert await client.raw.exists(handle.key) == 1
    assert not await client.exists("lock:job:1")


async def test_lock_context_manager_releases_after_a_failure(client: RedisClient) -> None:
    with pytest.raises(RuntimeError, match="body failed"):
        async with client.lock("job:1"):
            raise RuntimeError("body failed")
    assert not await client.exists("lock:job:1")


async def test_lock_context_manager_raises_when_contended(client: RedisClient) -> None:
    held = await client.acquire_lock("job:1")
    assert held is not None
    with pytest.raises(LockUnavailableError) as caught:
        async with client.lock("job:1"):
            pytest.fail("the body must not run without the lock")
    assert caught.value.name == "job:1"
    await held.release()


async def test_contention_is_not_a_redis_fault() -> None:
    """The lock error's contract is asserted in ``test_core_errors``; this is the
    module-local view of the same rule, kept here so a change to this file alone
    still shows why the two are distinct.
    """
    assert LockUnavailableError("job:1").code is ErrorCode.CONFLICT
    assert LockUnavailableError("job:1").retryable is False
    assert not isinstance(LockUnavailableError("job:1"), RedisError)


async def test_release_failure_does_not_mask_the_body_error(
    client: RedisClient, raw: Redis, logs: _Capture
) -> None:
    """A release in ``finally`` must not replace the exception that got us there.

    Letting the ``RedisError`` propagate would discard the body's real cause and
    report a Redis outage for what was actually a business-logic failure. The
    lease bounds the damage, so the release failure is logged instead.
    """
    with (
        patch.object(raw, "eval", side_effect=DriverConnectionError("down")),
        pytest.raises(RuntimeError, match="body failed"),
    ):
        async with client.lock("job:1"):
            raise RuntimeError("body failed")
    assert "redis.lock_release_failed" in logs.events()


# ---------------------------------------------------------------------------
# Pub/sub
# ---------------------------------------------------------------------------


async def test_publish_with_no_subscriber_is_not_an_error(client: RedisClient) -> None:
    """Zero recipients is the normal state before the first worker connects."""
    assert await client.publish("events", {"a": 1}) == 0


async def _collect_one(
    messages: AsyncGenerator[tuple[str, Any]], sink: list[tuple[str, Any]]
) -> None:
    """Take one message into ``sink``, then close the stream.

    The explicit ``aclose()`` is the documented pattern for a consumer that stops
    early -- ``break`` alone would leave cleanup to the finaliser -- and it is here
    so these tests do not leave a subscription open behind them.
    """
    try:
        async for message in messages:
            sink.append(message)
            return
    finally:
        await messages.aclose()


async def test_channel_is_namespaced(client: RedisClient) -> None:
    """A channel and a cache key of the same name must not collide.

    They share the ``ultron:`` namespace, so this proves a cache key can never be
    mistaken for a channel, or the reverse.
    """
    await client.set_json("events", "a cache value")
    received: list[tuple[str, Any]] = []
    messages = client.iter_messages("events")

    with _PubSubSpy(client.raw) as spy:
        consumer = asyncio.create_task(_collect_one(messages, received))
        handle = await spy.wait_until_live()
        assert await client.publish("events", {"kind": "task.done"}) == 1
        await asyncio.wait_for(consumer, timeout=5)

    assert received == [(f"{KEY_PREFIX}channel:events", {"kind": "task.done"})]
    assert await client.get_json("events") == "a cache value"
    # The cached value survived a message arriving on the same logical name.
    assert await client.raw.exists(f"{KEY_PREFIX}events") == 1
    assert handle.subscribed is False


async def test_iter_messages_handles_several_channels(client: RedisClient) -> None:
    """One subscription, several channels, each reported with its own name."""
    received: list[tuple[str, Any]] = []

    async def _take_two() -> None:
        messages = client.iter_messages("a", "b")
        try:
            async for message in messages:
                received.append(message)
                if len(received) == 2:
                    return
        finally:
            await messages.aclose()

    with _PubSubSpy(client.raw) as spy:
        consumer = asyncio.create_task(_take_two())
        handle = await spy.wait_until_live()
        await client.publish("a", 1)
        await client.publish("b", "two")
        await asyncio.wait_for(consumer, timeout=5)

    assert sorted(received) == [
        (f"{KEY_PREFIX}channel:a", 1),
        (f"{KEY_PREFIX}channel:b", "two"),
    ]
    assert handle.subscribed is False


async def test_iter_messages_survives_a_foreign_payload(client: RedisClient) -> None:
    """A non-JSON message is skipped, not raised.

    Another tenant or a hand-issued ``PUBLISH`` must not be able to crash a
    consumer loop.
    """
    received: list[tuple[str, Any]] = []

    async def _consume() -> None:
        async for message in client.iter_messages("chan"):
            if message[1] == {"good": True}:
                received.append(message)
                return

    with _PubSubSpy(client.raw) as spy:
        consumer = asyncio.create_task(_consume())
        await spy.wait_until_live()
        await client.raw.publish(f"{KEY_PREFIX}channel:chan", b"plain text, not json")
        await client.publish("chan", {"good": True})
        await asyncio.wait_for(consumer, timeout=5)
    assert received == [(f"{KEY_PREFIX}channel:chan", {"good": True})]


async def test_cancelling_a_consumer_releases_the_subscription(client: RedisClient) -> None:
    """The long-running consumer pattern must release on shutdown.

    Redis holds one connection per subscription, so a leak is a slow exhaustion
    of the client connection limit rather than an obvious failure. Cancelling the
    consuming task is how a real subscriber stops, and it unwinds the generator's
    ``finally`` deterministically.
    """
    with _PubSubSpy(client.raw) as spy:
        consumer = asyncio.create_task(_drain(client.iter_messages("chan")))
        handle = await spy.wait_until_live()

        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    assert handle.subscribed is False
    assert handle.connection is None


async def test_closing_the_iterator_early_releases_the_subscription(client: RedisClient) -> None:
    """A consumer that takes what it needs must close the subscription itself.

    ``break`` out of an ``async for`` does **not** close the async generator --
    cleanup is left to the finaliser -- so a caller that stops early has to
    ``aclose()``. The subscription has to be live first for this to prove
    anything, which means the generator must already have been advanced.
    """
    with _PubSubSpy(client.raw) as spy:
        iterator = client.iter_messages("chan")

        async def _pull_one_only() -> None:
            async for _message in iterator:
                return

        consumer = asyncio.create_task(_pull_one_only())
        handle = await spy.wait_until_live()
        await client.publish("chan", {"a": 1})
        await asyncio.wait_for(consumer, timeout=5)
        assert handle.subscribed is True, "the handle should still be open here"

        await iterator.aclose()

    assert handle.subscribed is False
    assert handle.connection is None


async def test_subscribe_requires_a_channel(client: RedisClient) -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        async with client.subscribe():
            pytest.fail("unreachable")


async def test_subscribe_yields_a_live_pubsub(client: RedisClient) -> None:
    async with client.subscribe("chan") as pubsub:
        assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5) is None
        await client.publish("chan", {"a": 1})
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
        assert message is not None
        assert json.loads(message["data"]) == {"a": 1}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_create_redis_client_injects_a_driver(raw: Redis) -> None:
    wrapper = create_redis_client(RedisSettings(), client=raw)
    assert wrapper.raw is raw
    assert wrapper.default_ttl == 3600


def test_create_redis_client_uses_the_configured_ttl(raw: Redis) -> None:
    wrapper = create_redis_client(RedisSettings(default_ttl=90), client=raw)
    assert wrapper.default_ttl == 90


def test_create_redis_client_opens_nothing() -> None:
    """Construction must not connect, or importing this module needs a server."""
    settings = RedisSettings(url=DSN_WITH_PASSWORD, socket_timeout=1)
    wrapper = create_redis_client(settings)
    assert isinstance(wrapper, RedisClient)
    assert wrapper.default_ttl == 3600


def test_the_dsn_password_is_never_logged(logs: _Capture) -> None:
    create_redis_client(RedisSettings(url=DSN_WITH_PASSWORD))
    assert SECRET not in _render(logs)
    assert "redis.client_created" in logs.events()


def test_the_dsn_is_still_identifiable_when_masked(logs: _Capture) -> None:
    """Masking must keep the host: a log line with no host is not diagnostic."""
    create_redis_client(RedisSettings(url=DSN_WITH_PASSWORD))
    dsn = _dsn_fields(logs)[0]
    assert "localhost" in dsn
    assert "6379" in dsn
    assert "ultron" in dsn
    assert SECRET not in dsn


async def test_aclose_is_safe_to_call_twice(raw: Redis) -> None:
    wrapper = RedisClient(raw)
    await wrapper.aclose()
    await wrapper.aclose()


async def test_client_context_manager_closes_the_driver(raw: Redis) -> None:
    """The context manager has to reach the driver, or the pool outlives the block.

    Asserting the effect instead of the call is not an option here: ``fakeredis``
    keeps serving after ``aclose()``, so a behavioural check would pass whether or
    not the close happened.
    """
    wrapper = RedisClient(raw)
    with patch.object(raw, "aclose") as spy:
        async with wrapper:
            assert wrapper.raw is raw
        spy.assert_awaited_once()


async def test_aclose_tolerates_a_driver_that_errors_on_close() -> None:
    """Shutdown must not fail because Redis went away first."""
    wrapper = RedisClient(cast("Redis", _BrokenRedis()))
    await wrapper.aclose()
