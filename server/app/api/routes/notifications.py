"""Notification routes (T510, spec §45/§29).

§45 delivers a ``NotificationProvider`` abstraction, a facade and a bus
subscriber that turns selected Core events into notifications (T096–T173). None of
that is observable over HTTP yet. These three routes close that gap without
inventing a second notification store:

* ``GET /notifications`` -- recent alerts, read from the **event log**;
* ``GET /notifications/providers`` -- which transport is live and which are wired;
* ``POST /notifications/test`` -- deliver one on demand, to prove the channel.

**History is the event store, not a new table (D049).** The dispatch subscriber
already maps a curated set of events to notification levels; those rows are the
durable record of what was worth waking someone for, so the collection route reads
them back through :class:`~app.database.repositories.events.EventRepository`
instead of persisting a parallel "notifications" table that would immediately
drift from the events it was derived from. The level shown is the same
``NOTIFY_LEVELS`` the dispatcher uses, so history and the live channel agree.

**Reads use the request's transaction.** The collection GET reads through the
repository inside the request's ``DbSession`` (which owns and commits the
transaction), so a page is one consistent read with no second connection.

**Delivery is reported, never raised.** The facade already returns a failed result
instead of raising (§45's loose-coupling rule); these routes carry that through to
HTTP, so a provider that is unavailable — or a subsystem that is disabled — shows
up as ``delivered=false`` with a reason, not as a 5xx.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Auth, Container, DbSession
from app.database.models import Event
from app.database.repositories.events import EventRepository
from app.notifications.base import NotificationLevel
from app.notifications.dispatch import NOTIFY_LEVELS

router = APIRouter(prefix="/notifications", tags=["notifications"])

#: Ceiling on a page. A larger request is refused by validation rather than
#: silently clamped, so a client cannot believe it read everything when it did not.
MAX_PAGE = 1000

#: The event-type vocabulary the dispatcher maps, as plain strings for the query.
_NOTIFY_TYPES: tuple[str, ...] = tuple(event_type.value for event_type in NOTIFY_LEVELS)

#: Reverse of ``NOTIFY_LEVELS``: the level a stored event was delivered at.
_NOTIFY_LEVEL_BY_TYPE: dict[str, NotificationLevel] = {
    event_type.value: level for event_type, level in NOTIFY_LEVELS.items()
}


class NotificationRead(BaseModel):
    """One alerts-history entry, shaped from the event that produced it.

    There is no ORM row of its own: the source is an
    :class:`~app.database.models.Event`, and this is the projection the UI wants —
    the level it was delivered at and a human title/message, both derived exactly
    as the live dispatcher derives them.
    """

    id: uuid.UUID
    event_type: str
    level: NotificationLevel
    title: str
    message: str
    source: str | None = None
    payload: dict[str, Any]
    task_id: uuid.UUID | None = None
    occurred_at: datetime

    @classmethod
    def from_event(cls, event: Event) -> NotificationRead:
        """Project a stored event into a notification-history entry."""
        title, message = _render(event.event_type, event.payload)
        return cls(
            id=event.id,
            event_type=event.event_type,
            level=_NOTIFY_LEVEL_BY_TYPE.get(event.event_type, NotificationLevel.INFO),
            title=title,
            message=message,
            source=event.source,
            payload=dict(event.payload or {}),
            task_id=event.task_id,
            occurred_at=event.occurred_at,
        )


class ProviderRead(BaseModel):
    """One notification transport and whether it is the live one."""

    name: str
    available: bool
    active: bool


class ProvidersRead(BaseModel):
    """The notification transports and which is selected.

    ``enabled`` is the §45 switch: when off, the server still records events but
    sends nothing, and the UI says so rather than showing a dead channel as live.
    """

    enabled: bool
    active: str
    providers: list[ProviderRead]


class TestNotificationRequest(BaseModel):
    """A notification to send on demand; every field has a sensible default.

    ``extra="forbid"`` so a mistyped field is a 422 rather than a silently ignored
    argument.
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=200)] = "ULTRON test notification"
    message: Annotated[str, Field(min_length=1, max_length=2000)] = (
        "This is a test notification from the ULTRON API."
    )
    level: NotificationLevel = NotificationLevel.INFO


class NotificationResultRead(BaseModel):
    """What a send attempt reported back (§45's ``NotificationResult`` over HTTP).

    ``delivered`` is the honest outcome: a provider that was unavailable, or a
    subsystem that is disabled, returns ``delivered=false`` with a ``detail``
    rather than an error status, because "it did not go out" is a state the caller
    needs to see, not an exception to catch.
    """

    provider: str
    delivered: bool
    detail: str | None = None


@router.get("", response_model=list[NotificationRead], summary="Recent notifications")
async def list_notifications(
    session: DbSession,
    principal: Auth,
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE)] = 50,
    since: datetime | None = None,
) -> list[NotificationRead]:
    """Return recent alerts from the event log, newest first.

    Only the curated set §45 maps becomes a notification, so this is what was worth
    a person's attention, not every event the Core emitted.
    """
    events = await EventRepository(session).list_by_types(_NOTIFY_TYPES, since=since, limit=limit)
    return [NotificationRead.from_event(event) for event in events]


@router.get("/providers", response_model=ProvidersRead, summary="Notification providers")
async def list_providers(container: Container, principal: Auth) -> ProvidersRead:
    """Report the active notification transport and the wired alternatives."""
    settings = container.settings.notifications
    active = container.notifications.provider.name
    providers = [
        ProviderRead(name="log", available=True, active=active == "log"),
        ProviderRead(
            name="webhook",
            available=bool(settings.webhook_url.strip()),
            active=active == "webhook",
        ),
    ]
    return ProvidersRead(enabled=settings.enabled, active=active, providers=providers)


@router.post("/test", response_model=NotificationResultRead, summary="Send a test notification")
async def send_test_notification(
    payload: TestNotificationRequest,
    container: Container,
    principal: Auth,
) -> NotificationResultRead:
    """Deliver one notification through the active provider, reporting the outcome.

    When the subsystem is disabled nothing is sent and the result says so, rather
    than reporting a delivery that did not happen.
    """
    provider = container.notifications.provider.name
    if not container.settings.notifications.enabled:
        return NotificationResultRead(
            provider=provider, delivered=False, detail="notifications are disabled"
        )
    result = await container.notifications.notify(
        payload.title,
        payload.message,
        level=payload.level,
        source="ultron.api",
    )
    return NotificationResultRead(
        provider=result.provider, delivered=result.delivered, detail=result.detail or None
    )


def _render(event_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Build a human title and message from a stored event.

    Deliberately identical to the dispatcher's rendering so the history reads the
    same whether the notification is seen live or pulled from the log afterwards.
    """
    title = event_type.replace("_", " ").title()
    detail = payload.get("error") or payload.get("message") or payload.get("reason")
    if isinstance(detail, str) and detail.strip():
        return title, detail
    summary = ", ".join(f"{key}={value}" for key, value in payload.items() if value is not None)
    return title, summary or title


__all__ = [
    "NotificationRead",
    "NotificationResultRead",
    "ProviderRead",
    "ProvidersRead",
    "TestNotificationRequest",
    "router",
]
