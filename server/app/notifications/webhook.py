"""Webhook notification provider (T096, spec §45).

POSTs a JSON payload to the configured URL. The transport is bounded by
``NotificationSettings.webhook_timeout`` and a failure raises a typed error, so
the service facade can record it rather than lose it.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config.settings import NotificationSettings
from app.core.errors import ConfigMissingError, DependencyUnavailableError, OperationTimeoutError
from app.notifications.base import (
    Notification,
    NotificationProvider,
    NotificationResult,
)

__all__ = ["WebhookNotificationProvider"]


class WebhookNotificationProvider(NotificationProvider):
    """Deliver notifications to an HTTP webhook as JSON."""

    def __init__(self, settings: NotificationSettings) -> None:
        self._settings = settings
        self._url = settings.webhook_url.strip()
        self._timeout = float(settings.webhook_timeout)
        if not self._url:
            raise ConfigMissingError("NOTIFICATION_WEBHOOK_URL")

    @property
    def name(self) -> str:
        return "webhook"

    async def send(self, notification: Notification) -> NotificationResult:
        payload: dict[str, Any] = {
            "title": notification.title,
            "message": notification.message,
            "level": notification.level.value,
            "source": notification.source,
            "metadata": notification.metadata,
            "created_at": notification.created_at.isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(f"webhook notification to {self._url} timed out") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "webhook", reason="the notification could not be sent", cause=exc
            ) from exc
        delivered = 200 <= response.status_code < 300
        return NotificationResult(
            provider=self.name,
            delivered=delivered,
            detail=f"HTTP {response.status_code}",
        )
