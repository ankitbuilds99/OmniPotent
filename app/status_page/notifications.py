"""Notification system for status page updates.

Sends notifications to subscribers via email and webhooks when:
- New incidents are created
- Incident status changes
- Incidents are resolved
- Maintenance windows are scheduled or completed
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Any

import httpx

from app.config import get_settings
from app.status_page.models import (
    Incident,
    IncidentStatus,
    Maintenance,
    MaintenanceStatus,
    Subscriber,
    SubscriptionType,
)
from app.url_guard import is_public_url

log = logging.getLogger("omnipotent.status_page.notifications")


@dataclass
class NotificationPayload:
    """Payload for a status page notification."""

    event_type: str  # incident_created, incident_updated, incident_resolved, maintenance_scheduled, etc.
    title: str
    message: str
    severity: str | None = None
    status: str | None = None
    affected_components: list[str] | None = None
    incident_id: str | None = None
    maintenance_id: str | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "status": self.status,
            "affected_components": self.affected_components or [],
            "incident_id": self.incident_id,
            "maintenance_id": self.maintenance_id,
            "timestamp": (self.timestamp or datetime.now(timezone.utc)).isoformat(),
        }

    def to_email_subject(self) -> str:
        """Generate email subject line."""
        prefix = "[Status Update]"
        if self.event_type == "incident_created":
            prefix = "[Incident]"
        elif self.event_type == "incident_resolved":
            prefix = "[Resolved]"
        elif self.event_type == "maintenance_scheduled":
            prefix = "[Maintenance Scheduled]"
        elif self.event_type == "maintenance_started":
            prefix = "[Maintenance Started]"
        elif self.event_type == "maintenance_completed":
            prefix = "[Maintenance Completed]"
        return f"{prefix} {self.title}"

    def to_email_body(self) -> str:
        """Generate email body text."""
        lines = [
            f"Status Update: {self.title}",
            "",
            self.message,
            "",
        ]
        if self.status:
            lines.append(f"Status: {self.status}")
        if self.severity:
            lines.append(f"Severity: {self.severity}")
        if self.affected_components:
            lines.append(f"Affected Components: {', '.join(self.affected_components)}")
        lines.extend([
            "",
            f"Timestamp: {(self.timestamp or datetime.now(timezone.utc)).isoformat()}",
            "",
            "---",
            "You are receiving this because you subscribed to status updates.",
            "To unsubscribe, visit your status page subscription settings.",
        ])
        return "\n".join(lines)


# --- Subscription verification -----------------------------------------------
#
# A subscription that is never verified is never notified: `notify_subscribers`
# skips `verified=False`. Nothing in the subscribe path ever sent the
# verification link, so every email address collected through the public form
# was inert. These are the delivery half of that; the router owns the HTTP half.

# What a deployment has to set for `get_email_provider()` to have a transport.
# Named in the 503 the subscribe endpoint returns, so an operator reading the
# response knows what is missing instead of guessing.
EMAIL_DELIVERY_REQUIREMENTS = (
    "set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD, "
    "or SENDGRID_API_KEY and SENDGRID_FROM_EMAIL, "
    "or AWS_SES_ENABLED with AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and SES_FROM_EMAIL"
)


def _mask_email(email: str) -> str:
    """An address that can go in a log line."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}" if len(local) > 2 else f"***@{domain}"


def email_delivery_configured() -> bool:
    """Whether the configured email provider can actually send anything."""
    from app.notifications.email import get_email_provider

    return bool(get_email_provider().is_configured())


def verification_email_body(verify_url: str) -> tuple[str, str]:
    """(text, html) for the verification email. Split out so tests can read it."""
    text = "\n".join(
        [
            "Confirm your status page subscription",
            "",
            "Someone (probably you) asked for status updates at this address.",
            "Open the link below to confirm it. The link is single-use.",
            "",
            verify_url,
            "",
            "If this was not you, ignore this email: without the link the",
            "subscription stays unverified and is never notified.",
        ]
    )
    # The URL is built from configuration or the request's own base URL, and
    # part of that is the client-supplied Host header, so it is escaped rather
    # than trusted into an attribute.
    safe_url = escape(verify_url, quote=True)
    body_html = (
        "<html><body>"
        "<h2>Confirm your status page subscription</h2>"
        "<p>Someone &mdash; probably you &mdash; asked for status updates at this "
        "address. Confirm it with the link below. The link is single-use.</p>"
        f'<p><a href="{safe_url}">Confirm this subscription</a></p>'
        f"<p>Or paste this into your browser:<br>{safe_url}</p>"
        "<p>If this was not you, ignore this email: without the link the "
        "subscription stays unverified and is never notified.</p>"
        "</body></html>"
    )
    return text, body_html


async def send_verification_email(email: str, verify_url: str) -> bool:
    """Send the verification link. Returns what the provider actually did."""
    from app.notifications.email import send_email

    text, html = verification_email_body(verify_url)
    try:
        delivered = await send_email(
            to=email,
            subject="Confirm your status page subscription",
            body_text=text,
            body_html=html,
        )
    except Exception as e:  # a broken provider must not 500 the subscribe form
        log.error(
            "verification email failed",
            extra={"email": _mask_email(email), "error": str(e)},
        )
        return False

    log.info(
        "verification email %s",
        "sent" if delivered else "not delivered",
        extra={"email": _mask_email(email)},
    )
    return bool(delivered)


class StatusNotifier:
    """Handles sending notifications to subscribers."""

    def __init__(
        self,
        webhook_timeout: float = 10.0,
        webhook_secret: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.webhook_timeout = webhook_timeout
        self.webhook_secret = webhook_secret or get_settings().copilot_api_token or ""
        self.max_retries = max_retries

    async def notify_subscribers(
        self,
        subscribers: list[Subscriber],
        payload: NotificationPayload,
    ) -> dict[str, list[str]]:
        """Send notifications to all provided subscribers.

        Returns:
            dict with "success" and "failed" lists of subscriber IDs
        """
        success: list[str] = []
        failed: list[str] = []

        # Kept alongside the tasks: the loop below skips unverified subscribers
        # and subscribers whose preferences exclude this event, so the results
        # line up with the *sent* list, not with `subscribers`. Zipped against
        # the full list, one skipped subscriber shifted every result after it
        # onto the wrong id.
        targets: list[Subscriber] = []
        tasks = []
        for subscriber in subscribers:
            if not subscriber.verified:
                continue

            # Check notification preferences
            if payload.event_type.startswith("incident_"):
                if not subscriber.notify_on_incidents:
                    continue
                if payload.event_type == "incident_resolved" and not subscriber.notify_on_resolved:
                    continue
            elif payload.event_type.startswith("maintenance_"):
                if not subscriber.notify_on_maintenance:
                    continue

            # Check component filter
            if subscriber.components and payload.affected_components:
                if not any(c in subscriber.components for c in payload.affected_components):
                    continue

            targets.append(subscriber)
            tasks.append(self._send_notification(subscriber, payload))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for subscriber, result in zip(targets, results):
            if isinstance(result, Exception):
                log.error(
                    "notification failed",
                    extra={"subscriber_id": subscriber.id, "error": str(result)},
                )
                failed.append(subscriber.id)
            elif result:
                success.append(subscriber.id)
            else:
                failed.append(subscriber.id)

        log.info(
            "notifications sent",
            extra={"success_count": len(success), "failed_count": len(failed)},
        )

        return {"success": success, "failed": failed}

    async def _send_notification(
        self,
        subscriber: Subscriber,
        payload: NotificationPayload,
    ) -> bool:
        """Send a notification to a single subscriber."""
        if subscriber.subscription_type == SubscriptionType.EMAIL:
            return await self._send_email(subscriber.target, payload)
        elif subscriber.subscription_type == SubscriptionType.WEBHOOK:
            return await self._send_webhook(subscriber.target, payload)
        return False

    async def _send_email(self, email: str, payload: NotificationPayload) -> bool:
        """Send an email notification.

        The provider's answer is the answer. This used to discard it and return
        True, so an unconfigured SMTP host — which makes `send_email` log a
        warning and return False without opening a socket — was reported to the
        caller as a delivered notification.
        """
        try:
            from app.notifications.email import send_email

            delivered = await send_email(
                to=email,
                subject=payload.to_email_subject(),
                body_text=payload.to_email_body(),
            )
        except Exception as e:
            log.error("email send failed", extra={"error": str(e)})
            return False

        if delivered:
            log.info("email sent", extra={"email": _mask_email(email)})
        else:
            log.warning(
                "email not delivered", extra={"email": _mask_email(email)}
            )
        return bool(delivered)

    async def _send_webhook(self, url: str, payload: NotificationPayload) -> bool:
        """Send a webhook notification with retry."""
        # Re-checked here and not only where the subscription was accepted: a
        # name that resolved to a public address then can resolve to 127.0.0.1
        # now, and rows predating the check at subscribe time were never
        # validated at all.
        if not await is_public_url(url):
            log.warning(
                "webhook target refused", extra={"url": url[:50]}
            )
            return False

        data = payload.to_dict()
        body = json.dumps(data, default=str)

        # Generate HMAC signature for webhook verification
        timestamp = str(int(time.time()))
        signature_payload = f"{timestamp}.{body}"
        signature = hmac.new(
            self.webhook_secret.encode(),
            signature_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Status-Signature": f"t={timestamp},v1={signature}",
            "X-Status-Timestamp": timestamp,
            "User-Agent": "OmniPotent-StatusPage/1.0",
        }

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.webhook_timeout) as client:
                    resp = await client.post(url, content=body, headers=headers)
                    if resp.status_code < 300:
                        log.info(
                            "webhook delivered",
                            extra={"url": url[:50], "status": resp.status_code},
                        )
                        return True
                    elif resp.status_code >= 500:
                        # Retry on server errors
                        log.warning(
                            "webhook server error, retrying",
                            extra={"url": url[:50], "status": resp.status_code, "attempt": attempt + 1},
                        )
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        # Don't retry on client errors (4xx)
                        log.error(
                            "webhook client error",
                            extra={"url": url[:50], "status": resp.status_code},
                        )
                        return False
            except httpx.TimeoutException:
                log.warning(
                    "webhook timeout, retrying",
                    extra={"url": url[:50], "attempt": attempt + 1},
                )
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                log.error("webhook error", extra={"url": url[:50], "error": str(e)})
                return False

        return False

    # Convenience methods for specific events

    async def notify_incident_created(
        self,
        subscribers: list[Subscriber],
        incident: Incident,
    ) -> dict[str, list[str]]:
        """Notify about a new incident."""
        payload = NotificationPayload(
            event_type="incident_created",
            title=incident.title,
            message=incident.message or f"We are investigating an issue: {incident.title}",
            severity=incident.severity.value,
            status=incident.status.value,
            affected_components=incident.affected_components,
            incident_id=incident.id,
            timestamp=incident.created_at,
        )
        return await self.notify_subscribers(subscribers, payload)

    async def notify_incident_updated(
        self,
        subscribers: list[Subscriber],
        incident: Incident,
        update_message: str,
    ) -> dict[str, list[str]]:
        """Notify about an incident update."""
        payload = NotificationPayload(
            event_type="incident_updated",
            title=f"Update: {incident.title}",
            message=update_message,
            severity=incident.severity.value,
            status=incident.status.value,
            affected_components=incident.affected_components,
            incident_id=incident.id,
            timestamp=incident.updated_at,
        )
        return await self.notify_subscribers(subscribers, payload)

    async def notify_incident_resolved(
        self,
        subscribers: list[Subscriber],
        incident: Incident,
        resolution_message: str = "",
    ) -> dict[str, list[str]]:
        """Notify about incident resolution."""
        payload = NotificationPayload(
            event_type="incident_resolved",
            title=f"Resolved: {incident.title}",
            message=resolution_message or "This incident has been resolved.",
            severity=incident.severity.value,
            status=IncidentStatus.RESOLVED.value,
            affected_components=incident.affected_components,
            incident_id=incident.id,
            timestamp=incident.resolved_at,
        )
        return await self.notify_subscribers(subscribers, payload)

    async def notify_maintenance_scheduled(
        self,
        subscribers: list[Subscriber],
        maintenance: Maintenance,
    ) -> dict[str, list[str]]:
        """Notify about scheduled maintenance."""
        payload = NotificationPayload(
            event_type="maintenance_scheduled",
            title=f"Scheduled Maintenance: {maintenance.title}",
            message=(
                f"{maintenance.description}\n\n"
                f"Scheduled Start: {maintenance.scheduled_start.isoformat()}\n"
                f"Scheduled End: {maintenance.scheduled_end.isoformat()}"
            ),
            status=MaintenanceStatus.SCHEDULED.value,
            affected_components=maintenance.affected_components,
            maintenance_id=maintenance.id,
            timestamp=maintenance.created_at,
        )
        return await self.notify_subscribers(subscribers, payload)

    async def notify_maintenance_started(
        self,
        subscribers: list[Subscriber],
        maintenance: Maintenance,
    ) -> dict[str, list[str]]:
        """Notify that maintenance has started."""
        payload = NotificationPayload(
            event_type="maintenance_started",
            title=f"Maintenance Started: {maintenance.title}",
            message=f"Scheduled maintenance is now in progress. {maintenance.description}",
            status=MaintenanceStatus.IN_PROGRESS.value,
            affected_components=maintenance.affected_components,
            maintenance_id=maintenance.id,
            timestamp=maintenance.actual_start,
        )
        return await self.notify_subscribers(subscribers, payload)

    async def notify_maintenance_completed(
        self,
        subscribers: list[Subscriber],
        maintenance: Maintenance,
    ) -> dict[str, list[str]]:
        """Notify that maintenance has completed."""
        payload = NotificationPayload(
            event_type="maintenance_completed",
            title=f"Maintenance Completed: {maintenance.title}",
            message="Scheduled maintenance has been completed. All systems are operational.",
            status=MaintenanceStatus.COMPLETED.value,
            affected_components=maintenance.affected_components,
            maintenance_id=maintenance.id,
            timestamp=maintenance.actual_end,
        )
        return await self.notify_subscribers(subscribers, payload)


# Global notifier instance
_notifier: StatusNotifier | None = None


def get_status_notifier() -> StatusNotifier:
    """Get the default status notifier."""
    global _notifier
    if _notifier is None:
        _notifier = StatusNotifier()
    return _notifier
