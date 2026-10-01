"""Redis client: cache, locks, pub/sub, and transient state (T017).

Spec §24 fixes the boundary: *"Use Redis for temporary state, queues, pub/sub,
WebSocket coordination, caching, rate limiting, short-lived locks. Do not use
Redis as the permanent source of truth for important data. PostgreSQL remains
authoritative."* This module implements the four uses the task names -- cache,
locks, pub/sub, transient state. The other three arrive with their consumers:
queues with the event bus (T031), rate limiting with the security work, and
WebSocket coordination with the connection manager (T023). They are built on the
primitives here rather than needing a second client.

Nothing here connects at import or construction, so the module is importable and
testable on a checkout with no Redis. Tests inject ``fakeredis``; production
builds the client from settings.

Six decisions are boundaries someone could later move by accident.

**Nothing is a module-level singleton.** Like :mod:`app.database.session`, which
deliberately has no engine at import, the client is built by the composition root
(T019). A module-level client would open a connection pool on the first import of
anything that touches this file, and a test could not substitute ``fakeredis``
without mutating global state.

**Every driver error becomes a :class:`~app.core.errors.RedisError` at this
boundary.** ``RedisError`` is retryable and reports the operation and the cause's
*type* but never its text, because a connection error from redis-py embeds the
DSN -- and a DSN carries the password, exactly as a SQLAlchemy
``OperationalError`` does. The error is raised, not logged and swallowed, so a
caller that *must* have the value cannot mistake an outage for a miss.

**A cache read raises by default; tolerance is explicit.** :meth:`get_json`
raises, and :meth:`get_or_none` is the separate, deliberately named cache-aside
path that returns ``None`` on a miss *and* on an outage. The split exists because
the two are otherwise indistinguishable: a caller that degrades silently turns a
Redis outage into a total cache miss, which looks like a cold cache and can hide a
real incident until something slower than it should be starts failing. Making
tolerance a separate method puts the decision in the source text.

**Values are JSON, and the contract is "JSON-native or nothing".** Only what
``json`` can represent is storable: ``dict``, ``list``, ``str``, ``int``,
``float``, ``bool``, and ``None``. A ``datetime``, ``UUID``, ``Decimal``, or
``set`` is *rejected at the write* rather than coerced, because the lenient
alternative -- ``json.dumps(..., default=str)`` -- stores something that reads
back as a string with no way left to tell it was ever a ``datetime``. So a caller
that wants to cache one converts it explicitly, and the value it gets back is the
string it wrote. The one silent change a caller must know about is ``tuple``,
which JSON has no distinct type for and which returns as a ``list``;
:func:`_encode` is the single place that would have to change to adopt msgpack.

**Locks are safe, which costs a round trip.** The obvious implementation is
``SET NX PX`` then ``DEL``, and it is wrong: a holder that stalls past its lease
-- a long GC pause, a blocked event loop -- wakes up holding a lock that has since
been granted to someone else, and its unconditional ``DEL`` frees *their* lock.
The fix is a random token stored with the key plus a compare-and-delete before
releasing, so a holder can only ever release its own lock. That is a Lua script
because ``GET`` followed by ``DEL`` is not atomic on its own.

**Locks are not renewed implicitly.** There is :meth:`RedisLock.extend` for a
caller that knows it needs a long critical section, but nothing extends a lease on
the holder's behalf. A background renewal task outlives the work that wanted the
lock, so a request that already gave up would still be holding it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Final

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError as DriverError

from app.config import RedisSettings, get_settings
from app.core.errors import LockUnavailableError, RedisError
from app.database.session import masked_url
from app.observability import get_logger

_LOGGER = get_logger(__name__)

#: Every key and channel this module touches is prefixed with this, so a Redis
#: instance shared with another application can be scanned or swept without
#: guessing whose keys are whose. Spec §24 says nothing here is the source of
#: truth, which is what makes a sweep against a shared instance recoverable
#: rather than data loss -- but only if the keys are separable to begin with.
KEY_PREFIX: Final[str] = "ultron:"

#: Channel names are namespaced under their own segment so a channel can never
#: collide with a cache key of the same name.
_CHANNEL_SEGMENT: Final[str] = "channel:"

#: Lock keys get their own segment for the same reason.
_LOCK_SEGMENT: Final[str] = "lock:"

#: Release a lock only when the stored token matches. A bare `DEL` would let a
#: holder whose lease has expired release a lock that now belongs to someone
#: else. Returns 1 when the key was ours and is now gone, 0 otherwise.
_RELEASE_IF_OWNER: Final[str] = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

#: Raise the lease on a lock we still hold. Same compare-and-act requirement as
#: release: extending a lock we have lost would extend somebody else's.
_EXTEND_IF_OWNER: Final[str] = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

#: Default lease. Short enough that a holder which dies is recovered from
#: quickly, long enough that a normal critical section fits inside it with
#: margin. Overrunning it is safe -- the release becomes a no-op -- it is only
#: unsafe in the sense that the lock is no longer held.
DEFAULT_LEASE_MS: Final[int] = 30_000

#: Poll interval when a caller asks to wait for a lock. Redis publishes no "lock
#: became free" notification, so a waiter can only ask again.
_LOCK_POLL_MS: Final[int] = 50

#: `SCAN` hint. Batches the sweep so a large keyspace is not walked one key per
#: round trip, while staying small enough not to stall the server.
_SCAN_COUNT: Final[int] = 200

#: Keys removed per `DEL` during a prefix sweep, to keep one enormous command
#: from becoming the request.
_DELETE_BATCH: Final[int] = 200

#: Characters refused in a logical key. Whitespace is the only one ruled out,
#: because it is what makes a `SCAN` pattern ambiguous; a colon is fine and is
#: in fact the intended segment separator.
_INVALID_KEY_CHARS: Final[frozenset[str]] = frozenset(" \t\r\n")


def _fail(operation: str, error: BaseException) -> RedisError:
    """Return the ULTRON error for a driver failure, keeping only the cause's type.

    The cause's *text* is deliberately dropped. redis-py puts the connection
    details -- host, port, and the password from the DSN -- into the message of a
    connection error, and a password does not belong in a log line or an HTTP
    response body.
    """
    return RedisError(operation, cause=error)


def _encode(value: Any) -> str:
    """Serialise a value for storage, raising :class:`ValueError` if it cannot be.

    ``json.dumps`` is given no ``default`` hook on purpose. The lenient form --
    falling back to ``str(...)`` for anything unrecognised -- would cache a value
    that reads back as a string with no way left to tell that it was ever
    anything else. A type JSON cannot represent should fail at the write, where
    the caller can still see which value it was.

    The two failure modes are separated because they are different mistakes: a
    ``TypeError`` is a value JSON has no representation for, and a ``ValueError``
    is a structure that contains itself.
    """
    try:
        return json.dumps(value, separators=(",", ":"))
    except TypeError as error:
        msg = f"value is not JSON-serialisable: {type(value).__name__}"
        raise ValueError(msg) from error
    except ValueError as error:
        msg = "value is not JSON-serialisable: it is circular"
        raise ValueError(msg) from error


def _decode(raw: str | bytes) -> Any:
    """Deserialise a stored value, treating an unparseable one as absent.

    A corrupt or foreign value is a miss, not a crash. The key is disposable by
    construction, and raising here would turn a stale cache entry into an outage
    on a code path that merely wanted to read it. The discard is logged, because
    it is a real fault and not a normal miss.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        return json.loads(text)
    except ValueError:
        _LOGGER.warning(
            "discarding unparseable stored value",
            extra={"event": "redis.value_unparseable"},
        )
        return None


def _as_text(value: str | bytes) -> str:
    """Return a driver value as ``str``, decoding bytes.

    redis-py hands back ``bytes`` for anything it did not decode on the way in,
    and a single ``SCAN`` result can mix the two. The decode is strict on
    purpose: a ``UnicodeDecodeError`` here is a corrupt-key incident, not a
    programming error, and should not be papered over.
    """
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _validate_key(key: str) -> str:
    """Return ``key`` namespaced under :data:`KEY_PREFIX`."""
    if not isinstance(key, str) or not key:
        msg = "redis key must be a non-empty string"
        raise ValueError(msg)
    if _INVALID_KEY_CHARS & set(key):
        msg = f"redis key {key!r} contains whitespace, which makes SCAN patterns ambiguous"
        raise ValueError(msg)
    return f"{KEY_PREFIX}{key}"


async def _aclose_pubsub(pubsub: PubSub) -> None:
    """Close a pub/sub handle, tolerating a connection that is already gone.

    Redis holds one connection per subscription, so leaking a handle is a slow
    exhaustion of the client connection limit rather than an obvious failure.
    """
    with contextlib.suppress(DriverError):
        await pubsub.aclose()


class RedisLock:
    """A held distributed lock, released by token rather than by name.

    Obtained from :meth:`RedisClient.acquire_lock` or used through
    :meth:`RedisClient.lock`. The lease is fixed at acquisition and is *not*
    extended automatically, so a critical section that overruns its lease simply
    loses the lock and its release becomes a no-op. That is the safe direction:
    the alternative -- a background renewal -- means a task that has already
    given up still holds the lock.
    """

    def __init__(self, client: Redis, key: str, token: str, lease_ms: int) -> None:
        self._client = client
        self._key = key
        self._token = token
        self._lease_ms = lease_ms
        self._released = False

    @property
    def key(self) -> str:
        """The fully namespaced key, for logs and for assertions in tests."""
        return self._key

    @property
    def lease_ms(self) -> int:
        """The lease this lock was taken with, in milliseconds."""
        return self._lease_ms

    @property
    def released(self) -> bool:
        """Whether :meth:`release` has run.

        Exposed so a caller can tell "released cleanly" from "the lease expired
        and someone else now holds it", which look identical from its side.
        """
        return self._released

    async def release(self) -> bool:
        """Release the lock if it is still ours. Returns whether it was freed.

        Idempotent, and safe to call from a ``finally`` after the context manager
        already released it. A ``False`` return means the lease had expired and
        another holder owns the key, so this lock was not held at the time it was
        released -- which is worth knowing and worth a log line.
        """
        if self._released:
            return False
        try:
            freed = await self._client.eval(_RELEASE_IF_OWNER, 1, self._key, self._token)
        except DriverError as error:
            raise _fail(f"lock release ({self._key})", error) from error
        self._released = True
        if not freed:
            _LOGGER.warning(
                "lock lease expired before release; another holder may own it",
                extra={"event": "redis.lock_lease_expired", "key": self._key},
            )
        return bool(freed)

    async def extend(self, lease_ms: int | None = None) -> bool:
        """Raise the lease if it is still ours. Returns whether it was extended.

        For a critical section with a genuinely long runtime, called by the
        holder and never on its behalf. ``False`` means the lease was already lost,
        and the caller is no longer protected by this lock.
        """
        if self._released:
            return False
        if lease_ms is not None and lease_ms <= 0:
            msg = f"lease_ms must be positive, got {lease_ms}"
            raise ValueError(msg)
        millis = self._lease_ms if lease_ms is None else lease_ms
        try:
            extended = await self._client.eval(
                _EXTEND_IF_OWNER, 1, self._key, self._token, str(millis)
            )
        except DriverError as error:
            raise _fail(f"lock extend ({self._key})", error) from error
        return bool(extended)

    async def __aenter__(self) -> RedisLock:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


class RedisClient:
    """Async Redis access for cache, locks, pub/sub, and transient state.

    Wraps :class:`redis.asyncio.Redis` rather than reimplementing it: the driver
    already speaks the protocol, and what this class adds is the boundary -- a key
    namespace, a JSON contract, and exactly one place where a driver error
    becomes a ULTRON error.
    """

    def __init__(self, client: Redis, *, default_ttl: int | None = None) -> None:
        self._client = client
        self._default_ttl = default_ttl

    @property
    def raw(self) -> Redis:
        """The underlying driver client, for health checks and pool inspection."""
        return self._client

    @property
    def default_ttl(self) -> int | None:
        """TTL applied to a write that does not supply one, or ``None`` if unbounded."""
        return self._default_ttl

    def _expiry(self, ttl: int | None, key: str) -> int | None:
        """Resolve the TTL for a write, warning if the result is unbounded.

        A key with no TTL can only be reclaimed by eviction, which Redis does
        under memory pressure and not before. Since nothing here is the source of
        truth, an unbounded key is pure waste, so the omission is logged rather
        than treated as a normal configuration.
        """
        effective = self._default_ttl if ttl is None else ttl
        if effective is not None and effective <= 0:
            _LOGGER.warning(
                "cache write with no expiry; the key can only be reclaimed by eviction",
                extra={"event": "redis.no_ttl", "key": f"{KEY_PREFIX}{key}"},
            )
            return None
        return effective

    # -- cache ------------------------------------------------------------

    async def get_json(self, key: str) -> Any:
        """Return the cached value, or ``None`` if absent.

        Raises :class:`~app.core.errors.RedisError` if Redis is unreachable. This
        is the deliberate default: a caller that cannot distinguish "absent" from
        "unreachable" cannot decide whether to fall back or to fail, so the
        distinction is made here and the tolerant path is :meth:`get_or_none`.
        """
        try:
            raw = await self._client.get(_validate_key(key))
        except DriverError as error:
            raise _fail(f"get ({key})", error) from error
        return None if raw is None else _decode(raw)

    async def get_or_none(self, key: str) -> Any:
        """Return the cached value, or ``None`` for either a miss or an outage.

        The cache-aside path. Use it where a miss is genuinely recoverable -- a
        recomputable query, a cache of something PostgreSQL can answer -- and not
        where a miss would be a silent correctness problem.

        The outage is logged rather than discarded, so the log still shows that
        Redis is down even though this call succeeded. Callers that would rather
        fail loudly can use :meth:`get_json`.
        """
        try:
            raw = await self._client.get(_validate_key(key))
        except DriverError as error:
            _LOGGER.warning(
                "cache read degraded; treating as a miss",
                extra={
                    "event": "redis.degraded",
                    "status": "degraded",
                    "key": f"{KEY_PREFIX}{key}",
                    "error": type(error).__name__,
                },
            )
            return None
        return None if raw is None else _decode(raw)

    async def set_json(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Store a value as JSON under a TTL.

        ``ttl`` defaults to the configured default. Passing a non-positive ``ttl``
        means no expiry and logs a warning; use :meth:`delete` to reclaim the key.
        """
        try:
            await self._client.set(_validate_key(key), _encode(value), ex=self._expiry(ttl, key))
        except DriverError as error:
            raise _fail(f"set ({key})", error) from error

    async def get_text(self, key: str) -> str | None:
        """Return a stored string, or ``None`` if absent. Raises on an outage."""
        try:
            raw = await self._client.get(_validate_key(key))
        except DriverError as error:
            raise _fail(f"get_text ({key})", error) from error
        return None if raw is None else _as_text(raw)

    async def set_text(self, key: str, value: str, *, ttl: int | None = None) -> None:
        """Store a plain string, for state that is not structured.

        A heartbeat or a presence marker: something with a definite lifetime and
        no structure worth serialising.
        """
        try:
            await self._client.set(_validate_key(key), value, ex=self._expiry(ttl, key))
        except DriverError as error:
            raise _fail(f"set_text ({key})", error) from error

    async def delete(self, *keys: str) -> int:
        """Delete keys, returning how many were removed."""
        if not keys:
            return 0
        try:
            return int(await self._client.delete(*[_validate_key(key) for key in keys]))
        except DriverError as error:
            raise _fail("delete", error) from error

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key under a logical prefix. Returns how many were removed.

        Uses ``SCAN`` rather than ``KEYS``: ``KEYS`` is O(n) over the whole
        keyspace and blocks the server while it runs, which on a shared instance
        is a self-inflicted denial of service. The count is advisory, because a
        key that expires between the scan and the delete is counted as removed
        either way.
        """
        pattern = f"{KEY_PREFIX}{prefix}*"
        removed = 0
        try:
            batch: list[str] = []
            async for key in self._client.scan_iter(match=pattern, count=_SCAN_COUNT):
                batch.append(_as_text(key))
                if len(batch) >= _DELETE_BATCH:
                    removed += int(await self._client.delete(*batch))
                    batch.clear()
            if batch:
                removed += int(await self._client.delete(*batch))
        except DriverError as error:
            raise _fail(f"delete_prefix ({prefix})", error) from error
        return removed

    async def exists(self, key: str) -> bool:
        """Report whether a key is present."""
        try:
            return bool(await self._client.exists(_validate_key(key)))
        except DriverError as error:
            raise _fail(f"exists ({key})", error) from error

    async def ttl(self, key: str) -> int:
        """Return the remaining TTL in **seconds**.

        Note the unit: the lock API is in milliseconds (``lease_ms``, matching
        Redis's own ``PEXPIRE``) while ``TTL`` is Redis's seconds-valued command.
        Mixing the two is the easy mistake here, so it is stated at both ends.

        ``-2`` means the key does not exist and ``-1`` means it has no expiry,
        both of which are Redis's own conventions. They are passed through rather
        than remapped, because a caller that cannot tell them apart cannot tell
        whether a key is leaking.
        """
        try:
            return int(await self._client.ttl(_validate_key(key)))
        except DriverError as error:
            raise _fail(f"ttl ({key})", error) from error

    async def incr(self, key: str, *, ttl: int) -> int:
        """Increment a counter, returning the new value.

        The TTL is mandatory and applied only when the counter is created, so
        traffic cannot keep a window alive forever. A counter with no expiry is a
        rate limit that never resets, which fails closed for the life of the
        process, so an absent TTL is refused rather than defaulted.
        """
        if ttl <= 0:
            msg = f"incr requires a positive ttl, got {ttl}"
            raise ValueError(msg)
        namespaced = _validate_key(key)
        try:
            value = int(await self._client.incr(namespaced))
            if value == 1:
                await self._client.expire(namespaced, ttl)
        except DriverError as error:
            raise _fail(f"incr ({key})", error) from error
        return value

    # -- locks ------------------------------------------------------------

    async def acquire_lock(
        self, name: str, *, lease_ms: int = DEFAULT_LEASE_MS, wait_ms: int = 0
    ) -> RedisLock | None:
        """Take the lock, optionally waiting up to ``wait_ms``. ``None`` if contended.

        A ``None`` return is not an error: it is the ordinary answer to "someone
        else has it", and the caller decides whether to give up, retry, or skip.
        The wait is a poll, because Redis offers no notification when a lock
        becomes free; ``wait_ms`` is a total budget, not a number of attempts.
        """
        if lease_ms <= 0:
            msg = f"lease_ms must be positive, got {lease_ms}"
            raise ValueError(msg)
        if wait_ms < 0:
            msg = f"wait_ms must not be negative, got {wait_ms}"
            raise ValueError(msg)

        key = _validate_key(f"{_LOCK_SEGMENT}{name}")
        # Generated per attempt rather than once, so a caller that waits and
        # eventually acquires is not holding a token an earlier failed attempt
        # already used.
        waited = 0
        while True:
            token = uuid.uuid4().hex
            try:
                acquired = await self._client.set(key, token, px=lease_ms, nx=True)
            except DriverError as error:
                raise _fail(f"lock ({name})", error) from error
            if acquired:
                return RedisLock(self._client, key, token, lease_ms)
            if waited >= wait_ms:
                return None
            step = min(_LOCK_POLL_MS, wait_ms - waited)
            await asyncio.sleep(step / 1000)
            waited += step

    @contextlib.asynccontextmanager
    async def lock(
        self, name: str, *, lease_ms: int = DEFAULT_LEASE_MS, wait_ms: int = 0
    ) -> AsyncIterator[RedisLock]:
        """Hold a distributed lock for the body, or raise if it is contended.

        Use this when the work genuinely must not run twice. Use
        :meth:`acquire_lock` when losing the race is acceptable and the right
        response is to skip the work.

        A failure to release is logged rather than raised, because this runs in a
        ``finally``: letting it propagate would replace whatever exception the
        body raised with a Redis error and hide the real cause. The lease bounds
        the damage either way.
        """
        acquired = await self.acquire_lock(name, lease_ms=lease_ms, wait_ms=wait_ms)
        if acquired is None:
            raise LockUnavailableError(name)
        try:
            yield acquired
        finally:
            try:
                await acquired.release()
            except RedisError:
                _LOGGER.warning(
                    "lock release failed; the lease will expire on its own",
                    extra={"event": "redis.lock_release_failed", "key": acquired.key},
                )

    # -- pub/sub ----------------------------------------------------------

    async def publish(self, channel: str, message: Any) -> int:
        """Publish a JSON message. Returns how many subscribers received it.

        A return of ``0`` is normal and is not a failure: no subscriber is
        interested yet. Treating it as an error would make publishing before the
        first worker connects look broken.
        """
        try:
            return int(await self._client.publish(_channel(channel), _encode(message)))
        except DriverError as error:
            raise _fail(f"publish ({channel})", error) from error

    @contextlib.asynccontextmanager
    async def subscribe(self, *channels: str) -> AsyncIterator[PubSub]:
        """Subscribe to channels, yielding the live :class:`PubSub`.

        The subscription is opened before the ``yield``, so a message published
        between this call and the consumer's first read is not missed. For the
        common case, :meth:`iter_messages` decodes for you.
        """
        if not channels:
            msg = "subscribe requires at least one channel"
            raise ValueError(msg)
        names = [_channel(channel) for channel in channels]
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(*names)
        except DriverError as error:
            await _aclose_pubsub(pubsub)
            raise _fail(f"subscribe ({', '.join(names)})", error) from error
        try:
            yield pubsub
        finally:
            await _aclose_pubsub(pubsub)

    async def iter_messages(self, *channels: str) -> AsyncGenerator[tuple[str, Any]]:
        """Yield ``(channel, decoded)`` for each message on the channels.

        Backed by the driver's ``listen``, which blocks rather than spinning.

        Releasing the subscription takes one of two deliberate acts, because
        ``break`` out of an ``async for`` does **not** close an async generator --
        it leaves cleanup to the finaliser, and Redis holds a connection open the
        whole time. Either cancel the consuming task, which unwinds this
        generator's ``finally`` deterministically and is how a long-running
        subscriber (T023, T031) should stop, or ``aclose()`` the iterator
        explicitly when a consumer takes only what it needs.
        """
        async with self.subscribe(*channels) as pubsub:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if data is None:
                    continue
                yield _as_text(message.get("channel", "")), _decode(_as_text(data))

    # -- lifecycle --------------------------------------------------------

    async def ping(self) -> None:
        """Check Redis is reachable, raising :class:`RedisError` if not.

        Used by startup and by the health check (T018). Wrapped here because there
        is no adapter in the way, and a bare driver error escaping to a caller
        would carry the DSN.
        """
        try:
            await self._client.ping()
        except DriverError as error:
            raise _fail("ping", error) from error

    async def aclose(self) -> None:
        """Close the connection pool. Safe to call more than once.

        Does not raise on a broken connection: shutdown should not fail because
        Redis went away first, and the pool is being discarded regardless.
        """
        with contextlib.suppress(DriverError):
            await self._client.aclose()
        _LOGGER.info("redis client closed", extra={"event": "redis.closed"})

    async def __aenter__(self) -> RedisClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


def _channel(channel: str) -> str:
    """Namespace a pub/sub channel the same way a key is namespaced."""
    return _validate_key(f"{_CHANNEL_SEGMENT}{channel}")


def create_redis_client(
    settings: RedisSettings | None = None, *, client: Redis | None = None
) -> RedisClient:
    """Build a :class:`RedisClient` from settings. No connection is opened here.

    Pool sizing comes from :class:`RedisSettings`, and the DSN is masked before it
    is logged, because a startup line is not a safe place for a password.

    ``client`` exists so a test can hand in ``fakeredis``; production passes
    ``None`` and gets a real client built from the DSN.
    """
    redis_settings = settings or get_settings().redis
    driver = client if client is not None else _build_driver(redis_settings)
    _LOGGER.info(
        "redis client created",
        extra={
            "event": "redis.client_created",
            "dsn": masked_url(redis_settings.url),
            "max_connections": redis_settings.max_connections,
        },
    )
    return RedisClient(driver, default_ttl=redis_settings.default_ttl)


def _build_driver(settings: RedisSettings) -> Redis:
    """Return a driver client for ``settings``. Nothing connects until first use."""
    return Redis.from_url(
        settings.url,
        max_connections=settings.max_connections,
        socket_timeout=settings.socket_timeout,
        socket_connect_timeout=settings.socket_timeout,
    )


__all__ = [
    "DEFAULT_LEASE_MS",
    "KEY_PREFIX",
    "LockUnavailableError",
    "RedisClient",
    "RedisLock",
    "create_redis_client",
]
