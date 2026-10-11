"""Notification subsystem (T096, spec §45).

Import the abstraction and the facade from here; the concrete providers are
selected at construction time by :func:`build_notification_service`, so no call
site is coupled to a transport.
"""

from __future__ import annotations

from app.config.settings import NotificationSettings
from app.notifications.base import (
    Notification,
    NotificationLevel,
    NotificationProvider,
    NotificationResult,
)
from app.notifications.log import LogNotificationProvider
from app.notifications.service import NotificationService
from app.notifications.webhook import WebhookNotificationProvider

__all__ = [
    "LogNotificationProvider",
    "Notification",
    "NotificationLevel",
    "NotificationProvider",
    "NotificationResult",
    "NotificationService",
    "WebhookNotificationProvider",
    "build_notification_provider",
    "build_notification_service",
]


def build_notification_provider(settings: NotificationSettings) -> NotificationProvider:
    """Choose the configured provider, defaulting to the log.

    A ``webhook`` default without a URL falls back to the log rather than
    failing: the server must always be able to notify (§45).
    """
    if settings.default_provider == "webhook" and settings.webhook_url.strip():
        return WebhookNotificationProvider(settings)
    return LogNotificationProvider()


def build_notification_service(settings: NotificationSettings) -> NotificationService:
    """Build the facade over the configured provider."""
    return NotificationService(build_notification_provider(settings))
