"""Notification dispatcher for routing notifications across channels.

Provides:
- Multi-channel notification routing
- User preference handling
- Rate limiting and deduplication
- Priority-based delivery
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.notifications.email import send_email
from app.notifications.push import send_push_notification

log = logging.getLogger("devcopilot.notifications.dispatcher")


class NotificationChannel(Enum):
    """Available notification channels."""

    EMAIL = "email"
    PUSH = "push"
    SLACK = "slack"
    WEBHOOK = "webhook"
    SMS = "sms"


class NotificationPriority(Enum):
    """Notification priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class NotificationPreferences:
    """User notification preferences."""

    user_id: str
    email: str | None = None
    phone: str | None = None
    slack_user_id: str | None = None
    enabled_channels: set[NotificationChannel] = field(
        default_factory=lambda: {NotificationChannel.EMAIL, NotificationChannel.PUSH}
    )
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    min_priority: NotificationPriority = NotificationPriority.LOW
    alert_filters: dict[str, bool] = field(default_factory=dict)


@dataclass
class Notification:
    """Notification to be dispatched."""

    id: str
    type: str
    title: str
    body: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    channels: set[NotificationChannel] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DeliveryResult:
    """Result of notification delivery."""

    notification_id: str
    channel: NotificationChannel
    success: bool
    error: str | None = None
    delivered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_USER_PREFERENCES: dict[str, NotificationPreferences] = {}
_SENT_NOTIFICATIONS: dict[str, datetime] = {}
_DEDUPE_WINDOW_SECONDS = 300


class NotificationDispatcher:
    """Dispatches notifications across channels based on user preferences."""

    def __init__(self):
        self.webhook_handler: Any | None = None
        self.slack_handler: Any | None = None
        self.sms_handler: Any | None = None

    def set_webhook_handler(self, handler: Any) -> None:
        """Set custom webhook handler."""
        self.webhook_handler = handler

    def set_slack_handler(self, handler: Any) -> None:
        """Set Slack notification handler."""
        self.slack_handler = handler

    def set_sms_handler(self, handler: Any) -> None:
        """Set SMS notification handler."""
        self.sms_handler = handler

    async def dispatch(
        self,
        user_id: str,
        notification: Notification,
    ) -> list[DeliveryResult]:
        """Dispatch notification to user across configured channels."""
        prefs = get_user_preferences(user_id)
        results: list[DeliveryResult] = []

        if not self._should_send(notification, prefs):
            log.debug(
                "Skipping notification %s for user %s (filtered)",
                notification.id,
                user_id,
            )
            return results

        if notification.dedupe_key:
            cache_key = f"{user_id}:{notification.dedupe_key}"
            if cache_key in _SENT_NOTIFICATIONS:
                last_sent = _SENT_NOTIFICATIONS[cache_key]
                if (datetime.now(timezone.utc) - last_sent).total_seconds() < _DEDUPE_WINDOW_SECONDS:
                    log.debug("Deduplicating notification %s", notification.dedupe_key)
                    return results
            _SENT_NOTIFICATIONS[cache_key] = datetime.now(timezone.utc)

        channels = notification.channels or prefs.enabled_channels

        for channel in channels:
            if channel not in prefs.enabled_channels:
                continue

            result = await self._deliver(user_id, prefs, notification, channel)
            results.append(result)

        return results

    def _should_send(
        self,
        notification: Notification,
        prefs: NotificationPreferences,
    ) -> bool:
        """Check if notification should be sent based on preferences."""
        if notification.priority.value < prefs.min_priority.value:
            return False

        if prefs.quiet_hours_start is not None and prefs.quiet_hours_end is not None:
            if notification.priority != NotificationPriority.CRITICAL:
                current_hour = datetime.now(timezone.utc).hour
                if prefs.quiet_hours_start <= current_hour < prefs.quiet_hours_end:
                    return False

        if notification.type in prefs.alert_filters:
            return prefs.alert_filters[notification.type]

        return True

    async def _deliver(
        self,
        user_id: str,
        prefs: NotificationPreferences,
        notification: Notification,
        channel: NotificationChannel,
    ) -> DeliveryResult:
        """Deliver notification to a specific channel."""
        try:
            if channel == NotificationChannel.EMAIL:
                if not prefs.email:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="No email configured",
                    )
                success = await send_email(
                    to=prefs.email,
                    subject=notification.title,
                    body_text=notification.body,
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=success,
                )

            elif channel == NotificationChannel.PUSH:
                results = await send_push_notification(
                    user_id=user_id,
                    title=notification.title,
                    body=notification.body,
                    data=notification.data,
                )
                success = any(results.values()) if results else False
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=success,
                )

            elif channel == NotificationChannel.SLACK:
                if not self.slack_handler or not prefs.slack_user_id:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Slack not configured",
                    )
                success = await self.slack_handler(
                    user_id=prefs.slack_user_id,
                    text=f"*{notification.title}*\n{notification.body}",
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=success,
                )

            elif channel == NotificationChannel.WEBHOOK:
                if not self.webhook_handler:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Webhook not configured",
                    )
                success = await self.webhook_handler(
                    notification_id=notification.id,
                    title=notification.title,
                    body=notification.body,
                    data=notification.data,
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=success,
                )

            elif channel == NotificationChannel.SMS:
                if not self.sms_handler or not prefs.phone:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="SMS not configured",
                    )
                success = await self.sms_handler(
                    phone=prefs.phone,
                    message=f"{notification.title}: {notification.body}"[:160],
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=success,
                )

            return DeliveryResult(
                notification_id=notification.id,
                channel=channel,
                success=False,
                error=f"Unknown channel: {channel}",
            )

        except Exception as e:
            log.error("Delivery failed for %s via %s: %s", notification.id, channel, e)
            return DeliveryResult(
                notification_id=notification.id,
                channel=channel,
                success=False,
                error=str(e),
            )


_dispatcher: NotificationDispatcher | None = None


def get_dispatcher() -> NotificationDispatcher:
    """Get the global notification dispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotificationDispatcher()
    return _dispatcher


def get_user_preferences(user_id: str) -> NotificationPreferences:
    """Get user notification preferences."""
    if user_id not in _USER_PREFERENCES:
        _USER_PREFERENCES[user_id] = NotificationPreferences(user_id=user_id)
    return _USER_PREFERENCES[user_id]


def set_user_preferences(prefs: NotificationPreferences) -> None:
    """Set user notification preferences."""
    _USER_PREFERENCES[prefs.user_id] = prefs


async def notify(
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    priority: NotificationPriority = NotificationPriority.MEDIUM,
    channels: set[NotificationChannel] | None = None,
    data: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> list[DeliveryResult]:
    """Send notification to a user."""
    import secrets

    notification = Notification(
        id=f"notif_{secrets.token_hex(8)}",
        type=notification_type,
        title=title,
        body=body,
        priority=priority,
        channels=channels,
        data=data or {},
        dedupe_key=dedupe_key,
    )

    dispatcher = get_dispatcher()
    return await dispatcher.dispatch(user_id, notification)
