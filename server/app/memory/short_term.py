"""Short-term memory: the current conversation/context (T121, spec §22).

§22's first layer is *"Current conversation/context"* (line 1134), and §24 draws
the storage boundary: Redis holds *"temporary state/caching"* while PostgreSQL
*"remains authoritative"*. Short-term memory is exactly both at once -- it is the
hottest, most ephemeral layer *and* it must survive a Redis restart, because
losing the conversation so far would make the next turn incoherent.

**Redis is the read path; PostgreSQL is the durable mirror.** Every appended
message is written to both: a JSON list under a TTL key for the fast, bounded,
sliding-window read, and a ``short_term`` row with the same expiry for
durability. Reads prefer Redis and fall back to the mirror on a miss or an
outage, ordered chronologically either way. This is why "Redis may handle
temporary state" (line 1154) does not become "Redis is the only copy".

**An outage does not overwrite history.** Appending reads the current list with
:meth:`RedisClient.get_json`, which *raises* on an outage, rather than the
tolerant cache-aside read. That choice matters here: a tolerant read returns
``None`` for both "no conversation yet" and "Redis is down", and writing a fresh
one-message list over the second would silently discard the conversation. On an
outage the append is still mirrored to PostgreSQL and the Redis write is skipped,
so the next healthy read recovers the history from the mirror.

**The window is bounded and slides.** The list is capped at
``short_term_max_messages`` (oldest dropped) and its TTL is refreshed on every
append (``short_term_ttl``), so an active conversation stays warm and an
abandoned one expires on its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import RedisError
from app.database.models import Memory, MemoryLayer
from app.database.redis_client import RedisClient
from app.database.repositories.memories import MemoryRepository
from app.memory import namespaces
from app.memory.types import MemoryMessage
from app.observability import get_logger

_LOGGER = get_logger(__name__)

#: Redis key segment. The model's ``namespace_scope`` keeps a namespace free of
#: whitespace, so it is safe inside a Redis key (see ``RedisClient._validate_key``).
_KEY_PREFIX = "memory:short:"


def _key(namespace: str) -> str:
    return f"{_KEY_PREFIX}{namespace}"


class ShortTermMemory:
    """Conversation/context memory: a bounded, expiring, namespaced window."""

    def __init__(
        self,
        repository: MemoryRepository,
        redis: RedisClient | None = None,
        *,
        max_messages: int = 40,
        ttl: int = 86400,
        default_namespace: str = namespaces.USER,
    ) -> None:
        if max_messages < 1:
            msg = "short-term max_messages must be positive"
            raise ValueError(msg)
        if ttl < 1:
            msg = "short-term ttl must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._redis = redis
        self._max_messages = max_messages
        self._ttl = ttl
        self._default_namespace = default_namespace

    def _namespace(self, namespace: str | None) -> namespaces.Namespace:
        return namespaces.parse(namespace or self._default_namespace)

    def _expiry(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._ttl)

    async def append(
        self,
        role: str,
        content: str,
        *,
        namespace: str | None = None,
        now: datetime | None = None,
    ) -> MemoryMessage:
        """Append one message to the window and mirror it durably."""
        parsed = self._namespace(namespace)
        stamp = now or datetime.now(UTC)
        message = MemoryMessage(role=role, content=content, created_at=stamp)

        if self._redis is not None:
            await self._append_to_redis(self._redis, parsed.raw, message)

        await self._repository.remember(
            namespace=parsed.raw,
            namespace_kind=parsed.kind,
            namespace_slug=parsed.slug,
            content=json.dumps(message.to_dict(), separators=(",", ":")),
            layer=MemoryLayer.SHORT_TERM,
            expires_at=self._expiry(stamp),
        )
        return message

    async def _append_to_redis(
        self, redis: RedisClient, namespace: str, message: MemoryMessage
    ) -> None:
        """Write to the Redis sliding window, degrading to the mirror on outage."""
        key = _key(namespace)
        try:
            current = await redis.get_json(key)
        except RedisError:
            _LOGGER.warning(
                "short-term memory read degraded; appending to the durable mirror only",
                extra={"event": "memory.short_term_degraded", "namespace": namespace},
            )
            return
        messages = current if isinstance(current, list) else []
        messages.append(message.to_dict())
        if len(messages) > self._max_messages:
            messages = messages[-self._max_messages :]
        try:
            await redis.set_json(key, messages, ttl=self._ttl)
        except RedisError:
            _LOGGER.warning(
                "short-term memory write degraded; the durable mirror still holds it",
                extra={"event": "memory.short_term_write_failed", "namespace": namespace},
            )

    async def recent(
        self,
        *,
        namespace: str | None = None,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[MemoryMessage]:
        """Return the window's messages in chronological order."""
        parsed = self._namespace(namespace)
        if self._redis is not None:
            cached = await self._redis.get_or_none(_key(parsed.raw))
            if isinstance(cached, list) and cached:
                messages = [
                    MemoryMessage.from_dict(item)
                    for item in cached
                    if isinstance(item, dict)
                ]
                return messages[-limit:] if limit else messages
        return await self._recent_from_mirror(parsed.raw, limit=limit, now=now)

    async def _recent_from_mirror(
        self, namespace: str, *, limit: int | None, now: datetime | None
    ) -> list[MemoryMessage]:
        """Reconstruct the window from the durable mirror, oldest first.

        Ordered by each message's *own* timestamp rather than by the row's
        ``created_at``: the column is often a database default (coarse to the
        second on some drivers), so rows written in the same second share it and
        a row-order sort would be arbitrary. The per-message timestamp is
        microsecond-precise and is what defines the conversation's order.
        """
        rows = await self._repository.list_live(namespace, now=now)
        messages = [
            _message_from_row(row) for row in rows if row.layer == MemoryLayer.SHORT_TERM
        ]
        messages.sort(key=lambda message: message.created_at)
        if limit:
            messages = messages[-limit:]
        return messages

    async def clear(self, *, namespace: str | None = None) -> int:
        """Clear the window in Redis *and* the durable mirror, returning rows removed."""
        parsed = self._namespace(namespace)
        if self._redis is not None:
            try:
                await self._redis.delete(_key(parsed.raw))
            except RedisError:
                _LOGGER.warning(
                    "short-term memory clear left the Redis key behind",
                    extra={"event": "memory.short_term_clear_failed", "namespace": parsed.raw},
                )
        return await self._repository.clear_layer(parsed.raw, MemoryLayer.SHORT_TERM)


def _message_from_row(row: Memory) -> MemoryMessage:
    """Parse one mirrored row back into a message, keeping the role."""
    try:
        payload: Any = json.loads(row.content)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return MemoryMessage.from_dict(payload)
    return MemoryMessage(role="user", content=row.content, created_at=row.created_at)


__all__ = ["ShortTermMemory"]
