"""Notification service facade (T096, spec §45).

The rest of the server talks to this, not to a provider. Its one job beyond
forwarding is §45's loose coupling in the failure direction: a notification that
cannot be delivered is recorded and reported as a failed result, never allowed
to break the task or agent that emitted it.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.errors import UltronError
from app.notifications.base import (
    Notification,
    NotificationLevel,
    NotificationProvider,
    NotificationResult,
)

__all__ = ["NotificationService"]


class NotificationService:
    """Deliver notifications through a configured provider, safely."""

    def __init__(self, provider: NotificationProvider) -> None:
        self._provider = provider
        self._logger = logging.getLogger("ultron.notifications")

    @property
    def provider(self) -> NotificationProvider:
        """The active provider (its ``name`` appears in every result)."""
        return self._provider

    async def notify(
        self,
        title: str,
        message: str,
        *,
        level: NotificationLevel = NotificationLevel.INFO,
        source: str = "ultron",
        metadata: dict[str, Any] | None = None,
    ) -> NotificationResult:
        """Send a notification; a delivery failure is returned, not raised."""
        notification = Notification(
            title=title,
            message=message,
            level=level,
            source=source,
            metadata=metadata or {},
        )
        try:
            return await self._provider.send(notification)
        except UltronError as exc:
            self._logger.warning(
                "notification delivery failed",
                extra={
                    "notification_provider": self._provider.name,
                    "notification_title": title,
                    "error": exc.message,
                },
            )
            return NotificationResult(
                provider=self._provider.name, delivered=False, detail=exc.message
            )
