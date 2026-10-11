"""Notification abstraction (T096, spec §45).

§45 asks for one thing above all: do **not** couple notification logic to a
single provider. Everything that wants to notify depends on
:class:`NotificationProvider`; which concrete transport is live — log today,
webhook today, desktop/Telegram/email/ESP32 later — is a construction decision
made once, in the container.

This module is only the contract and the value types. The providers live beside
it, and the service facade in :mod:`app.notifications.service` is the piece the
rest of the server talks to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class NotificationLevel(StrEnum):
    """How urgent a notification is."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Notification:
    """A single notification, independent of how it is delivered."""

    title: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO
    source: str = "ultron"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """What a provider reports back after an attempt."""

    provider: str
    delivered: bool
    detail: str = ""


class NotificationProvider(ABC):
    """A way to deliver a :class:`Notification` (§45).

    Implementations must be interchangeable: the caller only ever holds this
    type, never a concrete provider, so adding Telegram or ESP32 later is a new
    subclass rather than a change to every call site.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier, for results and logs."""

    @abstractmethod
    async def send(self, notification: Notification) -> NotificationResult:
        """Deliver ``notification``; raise a typed error if the transport fails."""
