"""The default notification provider: structured logging (T096, spec §45).

Always available and dependency-free, so the server is never *unable* to notify.
It maps a notification onto the standard logger at the matching level.
"""

from __future__ import annotations

import logging

from app.notifications.base import (
    Notification,
    NotificationLevel,
    NotificationProvider,
    NotificationResult,
)

__all__ = ["LogNotificationProvider"]

_LEVELS = {
    NotificationLevel.INFO: logging.INFO,
    NotificationLevel.WARNING: logging.WARNING,
    NotificationLevel.ERROR: logging.ERROR,
    NotificationLevel.CRITICAL: logging.CRITICAL,
}


class LogNotificationProvider(NotificationProvider):
    """Write notifications to the application log."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("ultron.notifications")

    @property
    def name(self) -> str:
        return "log"

    async def send(self, notification: Notification) -> NotificationResult:
        level = _LEVELS.get(notification.level, logging.INFO)
        self._logger.log(
            level,
            notification.title,
            extra={
                "notification_message": notification.message,
                "notification_source": notification.source,
                "notification_metadata": notification.metadata,
            },
        )
        return NotificationResult(provider=self.name, delivered=True, detail="logged")
