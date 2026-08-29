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

log = logging.getLogger("omnipotent.notifications.dispatcher")


class NotificationChannel(Enum):
    """Available notification channels."""

    EMAIL = "email"
    PUSH = "push"
    SLACK = "slack"
    WEBHOOK = "webhook"
    SMS = "sms"
    VOICE = "voice"
    WHATSAPP = "whatsapp"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    TEAMS = "teams"


class NotificationPriority(Enum):
    """Notification priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: The severity order, written down once. `_should_send` used to decide whether a
#: notification cleared the recipient's floor by comparing `priority.value` with
#: `min_priority.value` — the enum's *strings*. That comparison is alphabetical,
#: and both "critical" and "high" sort before "low", so with the default floor of
#: LOW every CRITICAL and HIGH notification was discarded before a single channel
#: was tried while MEDIUM and LOW were delivered. The ordering was inverted
#: precisely for the two levels that exist in order to be delivered, which on the
#: on-call path meant a SEV1 page was the one page guaranteed never to be sent.
_PRIORITY_RANK: dict[NotificationPriority, int] = {
    NotificationPriority.LOW: 0,
    NotificationPriority.MEDIUM: 1,
    NotificationPriority.HIGH: 2,
    NotificationPriority.CRITICAL: 3,
}


@dataclass
class NotificationPreferences:
    """User notification preferences."""

    user_id: str
    email: str | None = None
    phone: str | None = None
    slack_user_id: str | None = None
    discord_webhook_url: str | None = None
    telegram_chat_id: str | None = None
    teams_webhook_url: str | None = None
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


def _email_failure_reason() -> str:
    """Why an email send came back False. Imported lazily: only needed to explain
    a failure, and `send_email` is monkeypatched in tests that never touch the
    provider."""
    try:
        from app.notifications.email import get_email_provider

        if not get_email_provider().is_configured():
            return (
                "no email provider is configured on this deployment (set "
                "SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD, SENDGRID_API_KEY or "
                "AWS_SES_ENABLED)"
            )
    except Exception as exc:  # noqa: BLE001 — explaining a failure must not raise
        return f"the email provider could not be inspected: {exc}"
    return "the configured email provider rejected the message"


class NotificationDispatcher:
    """Dispatches notifications across channels based on user preferences."""

    def __init__(self):
        self.webhook_handler: Any | None = None
        self.slack_handler: Any | None = None
        self.sms_handler: Any | None = None
        self.voice_handler: Any | None = None
        self.whatsapp_handler: Any | None = None
        self.discord_handler: Any | None = None
        self.telegram_handler: Any | None = None
        self.teams_handler: Any | None = None

    def set_webhook_handler(self, handler: Any) -> None:
        """Set custom webhook handler."""
        self.webhook_handler = handler

    def set_slack_handler(self, handler: Any) -> None:
        """Set Slack notification handler."""
        self.slack_handler = handler

    def set_sms_handler(self, handler: Any) -> None:
        """Set SMS notification handler."""
        self.sms_handler = handler

    def set_voice_handler(self, handler: Any) -> None:
        """Set Voice call handler."""
        self.voice_handler = handler

    def set_whatsapp_handler(self, handler: Any) -> None:
        """Set WhatsApp handler."""
        self.whatsapp_handler = handler

    def set_discord_handler(self, handler: Any) -> None:
        """Set Discord handler."""
        self.discord_handler = handler

    def set_telegram_handler(self, handler: Any) -> None:
        """Set Telegram handler."""
        self.telegram_handler = handler

    def set_teams_handler(self, handler: Any) -> None:
        """Set Microsoft Teams handler."""
        self.teams_handler = handler

    async def dispatch(
        self,
        user_id: str,
        notification: Notification,
        preferences: NotificationPreferences | None = None,
    ) -> list[DeliveryResult]:
        """Dispatch notification to user across configured channels.

        `preferences` lets a caller that already knows where this recipient can be
        reached say so. Without it the routing comes from `_USER_PREFERENCES`, a
        process-local dictionary that nothing in the application ever writes to, so
        every recipient resolved to the same hardcoded default — no address, no
        phone number, and only EMAIL and PUSH enabled. That default silently
        overrode the channel the caller had explicitly asked for, which is how the
        on-call pager came to have no channel it could deliver on. A caller with
        persisted, per-recipient routing passes it here instead of writing it into
        the process dictionary, where it would go stale and leak across tenants.
        """
        prefs = preferences if preferences is not None else get_user_preferences(user_id)
        results: list[DeliveryResult] = []

        suppressed = self._suppression_reason(notification, prefs)
        if suppressed:
            # Named, not "(filtered)". Three unrelated rules can drop a
            # notification here and the caller sees the same empty list for all
            # of them, so the log line is the only place the difference between
            # "below their priority floor" and "muted by quiet hours" survives.
            log.info(
                "Notification %s for user %s not sent: %s",
                notification.id,
                user_id,
                suppressed,
            )
            return results

        # The dedupe window suppresses a repeat of something that *went out*. The
        # mark used to be written here, before a single channel had been tried, so
        # a page that failed for a real reason — no provider configured, no address
        # on file, the provider refusing the message — silenced every retry of it
        # for the next five minutes, and the retry was reported as a duplicate of a
        # page that had never been delivered. On the on-call path that turns a
        # recoverable failure into a five-minute hole with a reassuring reason
        # attached, which is the failure mode this module keeps being fixed for.
        # The mark is now written after the attempt and only when a channel
        # actually accepted the notification, below.
        cache_key = f"{user_id}:{notification.dedupe_key}" if notification.dedupe_key else ""
        if cache_key in _SENT_NOTIFICATIONS:
            last_sent = _SENT_NOTIFICATIONS[cache_key]
            if (datetime.now(timezone.utc) - last_sent).total_seconds() < _DEDUPE_WINDOW_SECONDS:
                log.debug("Deduplicating notification %s", notification.dedupe_key)
                return results

        channels = notification.channels or prefs.enabled_channels

        for channel in channels:
            if channel not in prefs.enabled_channels:
                # A channel the caller explicitly asked for and the recipient has
                # switched off is reported as a failed delivery rather than
                # dropped from the list. Dropping it silently is how a page that
                # nobody received became indistinguishable from a page that was
                # never attempted: the caller got an empty list and had to guess
                # between "deduplicated", "suppressed" and "channel off".
                results.append(
                    DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error=(
                            f"{channel.value} is not one of this recipient's "
                            f"enabled channels"
                        ),
                    )
                )
                continue

            result = await self._deliver(user_id, prefs, notification, channel)
            results.append(result)

        if cache_key and any(r.success for r in results):
            _SENT_NOTIFICATIONS[cache_key] = datetime.now(timezone.utc)

        return results

    def _should_send(
        self,
        notification: Notification,
        prefs: NotificationPreferences,
    ) -> bool:
        """Check if notification should be sent based on preferences."""
        return not self._suppression_reason(notification, prefs)

    def _suppression_reason(
        self,
        notification: Notification,
        prefs: NotificationPreferences,
    ) -> str:
        """Why this notification must not be sent, or "" when it may be.

        The reason exists because "should I send this" has three different
        answers that an operator has to be able to tell apart after the fact.
        `_should_send` stays a boolean for its callers.
        """
        # Ranked, never lexicographic. A priority missing from the table ranks
        # above every known level and an unknown floor ranks below every known
        # level, so a level added to the enum without being ranked here is
        # delivered rather than quietly dropped — silent suppression is the
        # failure this table exists to prevent, not one to reintroduce.
        floor = _PRIORITY_RANK.get(prefs.min_priority, 0)
        if _PRIORITY_RANK.get(notification.priority, len(_PRIORITY_RANK)) < floor:
            return (
                f"priority {notification.priority.value} is below the recipient's "
                f"floor of {prefs.min_priority.value}"
            )

        if prefs.quiet_hours_start is not None and prefs.quiet_hours_end is not None:
            if notification.priority != NotificationPriority.CRITICAL:
                current_hour = datetime.now(timezone.utc).hour
                if prefs.quiet_hours_start <= current_hour < prefs.quiet_hours_end:
                    return (
                        f"the recipient's quiet hours "
                        f"({prefs.quiet_hours_start}:00-{prefs.quiet_hours_end}:00 UTC) "
                        f"cover a non-critical notification"
                    )

        if notification.type in prefs.alert_filters:
            if not prefs.alert_filters[notification.type]:
                return f"the recipient has {notification.type!r} switched off"

        return ""

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
                        error="No email address on file for this recipient",
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
                    # `send_email` answers with a bare False for both "this
                    # deployment has no mail provider" and "the provider refused
                    # the message", and a DeliveryResult with no error reads
                    # downstream as the generic "delivery failed". Asking the
                    # provider which of the two it was costs nothing and is the
                    # difference between an operator setting SMTP_HOST and an
                    # operator chasing a bounce that never happened.
                    error=None if success else _email_failure_reason(),
                )

            elif channel == NotificationChannel.PUSH:
                results = await send_push_notification(
                    user_id=user_id,
                    title=notification.title,
                    body=notification.body,
                    data=notification.data,
                )
                if not results:
                    # An empty result is not a failed send, it is no send at all:
                    # the recipient has no registered device, and this build ships
                    # no endpoint through which a browser could register one. Say
                    # that, rather than leaving the caller to render an errorless
                    # failure as the generic "delivery failed" — the reader would
                    # go looking for a push provider outage that never happened.
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error=(
                            "No registered device for this recipient; this build "
                            "has no device-registration endpoint"
                        ),
                    )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=any(results.values()),
                )

            elif channel == NotificationChannel.SLACK:
                # "<channel> not configured" conflated two unrelated problems that
                # different people have to fix: nobody knows where to reach this
                # recipient, versus the deployment has no wiring for the channel at
                # all. The branches below name the piece that is actually missing,
                # here and for the phone-backed channels that follow.
                if not prefs.slack_user_id:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="No Slack user ID on file for this recipient",
                    )
                if not self.slack_handler:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Slack delivery is not wired up on this deployment",
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
                if not prefs.phone:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="No phone number on file for this recipient",
                    )
                if not self.sms_handler:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="SMS delivery is not wired up on this deployment",
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

            elif channel == NotificationChannel.VOICE:
                if not prefs.phone:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="No phone number on file for this recipient",
                    )
                if not self.voice_handler:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Voice delivery is not wired up on this deployment",
                    )
                result = await self.voice_handler(
                    phone=prefs.phone,
                    message=f"{notification.title}. {notification.body}",
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=result.get("success", False),
                    error=result.get("error"),
                )

            elif channel == NotificationChannel.WHATSAPP:
                if not prefs.phone:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="No phone number on file for this recipient",
                    )
                if not self.whatsapp_handler:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="WhatsApp delivery is not wired up on this deployment",
                    )
                result = await self.whatsapp_handler(
                    phone=prefs.phone,
                    message=f"*{notification.title}*\n\n{notification.body}",
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=result.get("success", False),
                    error=result.get("error"),
                )

            elif channel == NotificationChannel.DISCORD:
                if not self.discord_handler and not prefs.discord_webhook_url:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Discord not configured",
                    )
                from app.notifications.discord import send_discord_alert
                result = await send_discord_alert(
                    title=notification.title,
                    severity=notification.priority.value,
                    incident_id=notification.id,
                    description=notification.body,
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=result.get("success", False),
                    error=result.get("error"),
                )

            elif channel == NotificationChannel.TELEGRAM:
                if not self.telegram_handler and not prefs.telegram_chat_id:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Telegram not configured",
                    )
                from app.notifications.telegram import send_telegram_alert
                result = await send_telegram_alert(
                    title=notification.title,
                    severity=notification.priority.value,
                    incident_id=notification.id,
                    description=notification.body,
                    chat_id=prefs.telegram_chat_id,
                )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=result.get("success", False),
                    error=result.get("error"),
                )

            elif channel == NotificationChannel.TEAMS:
                if not self.teams_handler and not prefs.teams_webhook_url:
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=False,
                        error="Teams not configured",
                    )
                if self.teams_handler:
                    result = await self.teams_handler(
                        webhook_url=prefs.teams_webhook_url,
                        title=notification.title,
                        body=notification.body,
                    )
                    return DeliveryResult(
                        notification_id=notification.id,
                        channel=channel,
                        success=result.get("success", False),
                        error=result.get("error"),
                    )
                return DeliveryResult(
                    notification_id=notification.id,
                    channel=channel,
                    success=False,
                    error="Teams handler not set",
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
    """Get the global notification dispatcher.

    Default channel handlers are installed on first construction. Without them the
    SLACK, WEBHOOK, SMS, VOICE, WHATSAPP and TEAMS branches below can never
    deliver: each requires its handler slot to be populated, and nothing in the
    codebase populated them, so every send returned "<channel> not configured"
    however the deployment was set up. Imported here rather than at module scope
    because app.notifications.handlers imports back into this package.
    """
    global _dispatcher
    if _dispatcher is None:
        from app.notifications.handlers import install_default_handlers

        _dispatcher = NotificationDispatcher()
        install_default_handlers(_dispatcher)
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
    preferences: NotificationPreferences | None = None,
) -> list[DeliveryResult]:
    """Send notification to a user.

    Pass `preferences` when the caller — not this module's process dictionary —
    is the authority on where the recipient can be reached; see `dispatch`.
    """
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
    return await dispatcher.dispatch(user_id, notification, preferences=preferences)
