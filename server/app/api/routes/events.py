"""Authenticated receive-only event stream (T023, spec §19/§27/§29).

One endpoint, ``GET /events``, speaking Server-Sent Events.

**Why SSE and not WebSocket.** Decided in T311/T320: the client only ever
*receives* on this channel. SSE reconnects on its own, survives proxies, needs no
heartbeat bookkeeping on the wire, and every HTTP library speaks it. The cost is
that there is nowhere on this channel for a client to *send* — acknowledging an
event, cancelling work, streaming audio. Those need explicit HTTP calls (T320)
or a later bidirectional stream. That trade was made deliberately, not
discovered later.

**Authentication uses the ``Authorization`` header, which means the client cannot
use ``EventSource``.** ``EventSource`` sends no custom headers and accepts no
request body, so it cannot present a bearer token except as a query parameter —
and §30 makes these long-lived opaque credentials, so a token in the URL ends up
in proxy and access logs. The client must therefore use ``fetch()`` with a
``ReadableStream`` and parse the frames itself.

The trade is real and worth stating: ``EventSource``'s automatic reconnect and
frame parsing are both lost, so T320 reimplements them. The alternative was a
same-origin HttpOnly cookie, which needs a BFF in front of the API and inherits
CSRF concerns. Between a hand-written parser and a CSRF surface, the parser is
the smaller problem to own.

This is why ``/events`` reads the header through the ordinary
``require_principal`` dependency rather than doing its own token handling: the
stream is authenticated exactly like every other route, and an unauthenticated
subscriber gets a ``401`` before any stream opens.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import Auth
from app.core.errors import InvalidInputError
from app.events.bus import (
    ALL_TOPICS,
    CONNECTED_EVENT_TYPE,
    SYSTEM_TOPIC,
    EventEnvelope,
    iter_topics,
    new_stream_id,
    topic_for,
)
from app.observability.logging import get_logger

_LOGGER = get_logger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

#: ``text/event-stream`` is what makes a client treat the body as a stream of
#: frames rather than as a document that ends at the first connection close.
SSE_CONTENT_TYPE = "text/event-stream"


def _keepalive(stream_id: str) -> bytes:
    """A comment frame, which every SSE client ignores.

    This exists because a silent connection is indistinguishable from a dead one.
    Intermediaries close idle TCP connections, so without a heartbeat a perfectly
    healthy stream gets reaped as idle and every client reconnects forever. The
    alternative -- a no-op event frame -- would wake the client's renderer and
    misrepresent activity as work happening, so this is a comment instead.
    """
    return f": keepalive {stream_id}\n\n".encode()


def _retry_hint(milliseconds: int) -> bytes:
    """Tell the client how long to wait before reconnecting.

    SSE clients reconnect on their own, and this sets that interval. A long value
    is deliberate: a reconnect storm against a struggling server makes it worse,
    so the client is asked to back off further than its own instinct would.
    """
    return f"retry: {milliseconds}\n\n".encode()


@router.get(
    "",
    summary="Event stream (Server-Sent Events)",
    response_description="A text/event-stream of events.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "No usable credential."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Invalid topic filter."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The stream is at capacity."},
    },
)
async def stream_events(
    request: Request,
    principal: Auth,
    topics: list[str] | None = Query(
        default=None,
        description=(
            "Topics to receive. Omit or pass '*' for everything. "
            f"Available: {SYSTEM_TOPIC}, agent, task, tool, model, build, test, "
            "device, voice, schedule."
        ),
    ),
    last_event_id: int | None = Query(
        default=None,
        alias="lastEventId",
        description=(
            "Resume after this bus id. The client normally sends this via the "
            "standard Last-Event-ID header instead; this exists for the "
            "fetch()-based client, which can set the header but should not have "
            "to rebuild URL state."
        ),
    ),
) -> StreamingResponse:
    """Open an authenticated, receive-only event stream.

    Resumes from ``Last-Event-ID`` when present, replaying anything missed. A
    client that has fallen too far behind gets a ``stream.lagged`` event telling
    it to resync rather than a silent resume from "now" — a resume that skips
    events looks exactly like a working stream, and §60.4 is explicit that a
    dropped connection must never read as an idle system.
    """
    requested = set(iter_topics(topics))
    if requested and ALL_TOPICS not in requested:
        known = _known_topics()
        unknown = requested - known
        if unknown:
            # Reject rather than silently drop: a client asking for "agentz"
            # should find out, not sit waiting for events that cannot exist.
            raise InvalidInputError(
                f"unknown topic(s): {', '.join(sorted(unknown))}",
                details={"unknown_topics": sorted(unknown), "known_topics": sorted(known)},
            )

    resume_from = _resume_from(request, last_event_id)
    container = request.app.state.container
    stream_id = new_stream_id()

    try:
        subscription = container.events.subscribe(requested or None, last_event_id=resume_from)
    except Exception as exc:  # surfaced as 503 below
        _LOGGER.warning(
            "stream refused",
            extra={"event": "stream.refused", "actor_id": principal.actor_id},
        )
        raise _capacity_error(exc) from exc

    _LOGGER.info(
        "stream opened",
        extra={
            "event": "stream.opened",
            "actor_id": principal.actor_id,
            "stream_id": stream_id,
            "topics": sorted(requested) or [ALL_TOPICS],
            "resumed_from": resume_from,
        },
    )

    heartbeat = container.settings.events.heartbeat_seconds
    retry_ms = container.settings.events.retry_milliseconds

    async def body() -> AsyncIterator[bytes]:
        """Yield SSE frames until the client goes away.

        Every exit path closes the subscription. A leaked subscription would keep
        receiving events for a disconnected client forever, and because each holds
        a bounded queue that is a slow leak of memory rather than a crash — the
        kind that never shows up in a test that only checks one connection.
        """
        try:
            yield _retry_hint(retry_ms)
            yield EventEnvelope(
                id=subscription.last_id or resume_from or 0,
                event_type=CONNECTED_EVENT_TYPE,
                topic=SYSTEM_TOPIC,
                payload={
                    "stream_id": stream_id,
                    "topics": sorted(subscription.topics),
                    "resumed_from": resume_from,
                    "dropped": subscription.dropped,
                    "heartbeat_seconds": heartbeat,
                },
            ).to_sse()

            while True:
                # Disconnect is checked on the same timeout as the heartbeat, so
                # a client that vanishes is noticed within one interval instead
                # of holding the subscription until the next event arrives. On a
                # quiet system there may not be a next event.
                try:
                    envelope = await asyncio.wait_for(subscription.get(), timeout=heartbeat)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield _keepalive(stream_id)
                    continue

                if envelope is None:
                    return
                yield envelope.to_sse()
        except asyncio.CancelledError:
            # Client disconnect during a streaming response surfaces here rather
            # than through is_disconnected(). Re-raise: swallowing it would leave
            # the generator task in an undefined state.
            raise
        finally:
            subscription.close()
            _LOGGER.info(
                "stream closed",
                extra={
                    "event": "stream.closed",
                    "actor_id": principal.actor_id,
                    "stream_id": stream_id,
                    "dropped": subscription.dropped,
                    "last_id": subscription.last_id,
                },
            )

    return StreamingResponse(
        body(),
        media_type=SSE_CONTENT_TYPE,
        headers={
            # Proxies cache and buffer responses by default; both would break a
            # stream by holding frames until enough accumulate.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx not to buffer. Without it a reverse proxy will happily
            # hold every frame until its buffer fills, which looks like a hung
            # connection rather than a misconfigured proxy.
            "X-Accel-Buffering": "no",
        },
    )


def _resume_from(request: Request, explicit: int | None) -> int | None:
    """Prefer the standard header, falling back to the query parameter."""
    header = request.headers.get("last-event-id")
    if header and header.strip().isdigit():
        return int(header.strip())
    return explicit


def _known_topics() -> frozenset[str]:
    """Every topic derivable from a spec §19 event type, plus the wildcard."""
    return frozenset(
        {
            ALL_TOPICS,
            SYSTEM_TOPIC,
            "agent",
            "task",
            "tool",
            "model",
            "build",
            "test",
            "device",
            "voice",
            "schedule",
        }
    )


def _capacity_error(exc: Exception) -> Exception:
    """Translate a refused subscription into the right ULTRON error.

    Imported lazily to keep this module's import graph the same as the other
    routes'; the bus raises plain exceptions precisely so it stays usable without
    the HTTP layer.
    """
    from app.core.errors import DependencyUnavailableError

    if isinstance(exc, ValueError):
        return InvalidInputError(str(exc))
    return DependencyUnavailableError(str(exc))


__all__ = ["SSE_CONTENT_TYPE", "router", "topic_for"]
