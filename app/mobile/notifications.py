"""Mobile Notifications.

AI-enriched push notifications with actionable buttons.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.mobile.notifications")


class NotificationPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ActionType(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"
    SNOOZE = "snooze"
    VIEW = "view"
    HANDOFF = "handoff"


@dataclass
class NotificationAction:
    """An action button on a notification."""

    action_type: ActionType
    label: str
    icon: str = ""
    requires_confirmation: bool = False
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.action_type.value,
            "label": self.label,
            "icon": self.icon,
            "requires_confirmation": self.requires_confirmation,
            "deep_link": self.deep_link,
        }


@dataclass
class AIEnrichment:
    """AI-generated enrichment for notification."""

    summary: str
    root_cause: str
    confidence: float
    suggested_action: str
    deploy_correlation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "suggested_action": self.suggested_action,
            "deploy_correlation": self.deploy_correlation,
        }


@dataclass
class IncidentMetrics:
    """Real-time metrics for notification."""

    error_rate: float
    baseline_error_rate: float
    affected_requests: int
    affected_customers: int
    duration_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_rate": self.error_rate,
            "baseline": self.baseline_error_rate,
            "affected_requests": self.affected_requests,
            "affected_customers": self.affected_customers,
            "duration_minutes": self.duration_minutes,
        }


@dataclass
class MobileNotification:
    """A mobile push notification."""

    notification_id: str
    incident_id: str
    title: str
    body: str
    severity: str
    priority: NotificationPriority
    actions: list[NotificationAction]
    recipient: str
    ai_enrichment: AIEnrichment | None = None
    metrics: IncidentMetrics | None = None
    escalation_info: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    sound: str = "default"
    badge_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.notification_id,
            "incident_id": self.incident_id,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "priority": self.priority.value,
            "actions": [a.to_dict() for a in self.actions],
            "recipient": self.recipient,
            "ai_enrichment": self.ai_enrichment.to_dict() if self.ai_enrichment else None,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "escalation_info": self.escalation_info,
            "created_at": self.created_at.isoformat(),
            "sound": self.sound,
        }

    def to_apns_payload(self) -> dict[str, Any]:
        """Generate Apple Push Notification Service payload."""
        alert = {
            "title": self.title,
            "body": self.body,
            "sound": self.sound,
        }

        if self.ai_enrichment:
            alert["subtitle"] = self.ai_enrichment.summary[:50]

        payload = {
            "aps": {
                "alert": alert,
                "badge": self.badge_count,
                "sound": self.sound,
                "category": f"INCIDENT_{self.severity}",
                "thread-id": self.incident_id,
            },
            "incident_id": self.incident_id,
            "severity": self.severity,
        }

        if self.ai_enrichment:
            payload["ai_summary"] = self.ai_enrichment.summary
            payload["root_cause"] = self.ai_enrichment.root_cause

        if self.metrics:
            payload["metrics"] = self.metrics.to_dict()

        return payload

    def to_fcm_payload(self) -> dict[str, Any]:
        """Generate Firebase Cloud Messaging payload."""
        return {
            "notification": {
                "title": self.title,
                "body": self.body,
            },
            "data": {
                "incident_id": self.incident_id,
                "severity": self.severity,
                "priority": self.priority.value,
                "ai_summary": self.ai_enrichment.summary if self.ai_enrichment else "",
            },
            "android": {
                "priority": "high" if self.priority == NotificationPriority.URGENT else "normal",
                "notification": {
                    "channel_id": f"incidents_{self.severity.lower()}",
                },
            },
        }

    def to_voice_summary(self) -> str:
        """Generate voice summary for AirPods/Watch."""
        severity_text = {
            "SEV1": "severity 1",
            "SEV2": "severity 2",
            "SEV3": "severity 3",
        }.get(self.severity, self.severity)

        parts = [f"You have a {severity_text} incident."]

        if self.ai_enrichment:
            parts.append(self.ai_enrichment.summary)
            parts.append(f"Confidence {int(self.ai_enrichment.confidence)}%.")
            parts.append(self.ai_enrichment.suggested_action)

        return " ".join(parts)


class NotificationService:
    """Service for managing mobile notifications."""

    def __init__(self) -> None:
        self._notifications: dict[str, MobileNotification] = {}
        self._counter = 0

        self._default_actions = {
            "SEV1": [
                NotificationAction(ActionType.ACKNOWLEDGE, "Acknowledge", "checkmark"),
                NotificationAction(ActionType.ROLLBACK, "Rollback", "arrow.uturn.left", requires_confirmation=True),
                NotificationAction(ActionType.ESCALATE, "Escalate", "arrow.up"),
            ],
            "SEV2": [
                NotificationAction(ActionType.ACKNOWLEDGE, "Acknowledge", "checkmark"),
                NotificationAction(ActionType.SNOOZE, "Snooze 5m", "clock"),
                NotificationAction(ActionType.VIEW, "View", "eye"),
            ],
            "SEV3": [
                NotificationAction(ActionType.ACKNOWLEDGE, "Acknowledge", "checkmark"),
                NotificationAction(ActionType.VIEW, "View", "eye"),
            ],
        }

    def create_notification(
        self,
        incident_id: str,
        title: str,
        severity: str,
        recipient: str,
        root_cause: str = "",
        confidence: float = 0.0,
        deploy_info: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        escalation_minutes: int | None = None,
    ) -> MobileNotification:
        """Create an AI-enriched notification."""
        self._counter += 1
        notification_id = f"notif_{self._counter}"

        ai_enrichment = None
        if root_cause:
            summary = self._generate_summary(root_cause, deploy_info)
            suggested_action = self._generate_suggested_action(root_cause, severity)

            ai_enrichment = AIEnrichment(
                summary=summary,
                root_cause=root_cause,
                confidence=confidence,
                suggested_action=suggested_action,
                deploy_correlation=deploy_info,
            )

        inc_metrics = None
        if metrics:
            inc_metrics = IncidentMetrics(
                error_rate=metrics.get("error_rate", 0),
                baseline_error_rate=metrics.get("baseline", 0),
                affected_requests=metrics.get("affected_requests", 0),
                affected_customers=metrics.get("affected_customers", 0),
                duration_minutes=metrics.get("duration_minutes", 0),
            )

        priority = {
            "SEV1": NotificationPriority.URGENT,
            "SEV2": NotificationPriority.HIGH,
            "SEV3": NotificationPriority.NORMAL,
        }.get(severity, NotificationPriority.NORMAL)

        body = ai_enrichment.summary if ai_enrichment else title
        actions = self._default_actions.get(severity, self._default_actions["SEV3"])

        escalation_info = None
        if escalation_minutes:
            escalation_info = {
                "auto_escalate_minutes": escalation_minutes,
                "message": f"Will escalate in {escalation_minutes} minutes",
            }

        notification = MobileNotification(
            notification_id=notification_id,
            incident_id=incident_id,
            title=f"[{severity}] {title}",
            body=body,
            severity=severity,
            priority=priority,
            actions=actions,
            recipient=recipient,
            ai_enrichment=ai_enrichment,
            metrics=inc_metrics,
            escalation_info=escalation_info,
            sound="critical" if severity == "SEV1" else "default",
        )

        self._notifications[notification_id] = notification
        log.info(
            "Created notification: %s for %s severity=%s",
            notification_id,
            recipient,
            severity,
        )

        return notification

    def _generate_summary(
        self,
        root_cause: str,
        deploy_info: dict[str, Any] | None,
    ) -> str:
        """Generate AI summary for notification."""
        if deploy_info:
            deploy_id = deploy_info.get("deploy_id", "unknown")
            author = deploy_info.get("author", "unknown")
            return f"Likely caused by deploy #{deploy_id} by {author}. {root_cause[:100]}"

        return root_cause[:150]

    def _generate_suggested_action(self, root_cause: str, severity: str) -> str:
        """Generate suggested action based on root cause."""
        root_lower = root_cause.lower()

        if "deploy" in root_lower or "rollback" in root_lower:
            return "Consider rolling back the recent deploy"
        elif "null" in root_lower or "none" in root_lower:
            return "Check for missing null checks in the code"
        elif "timeout" in root_lower:
            return "Check service dependencies and timeouts"
        elif "database" in root_lower or "query" in root_lower:
            return "Check database performance and queries"
        elif severity == "SEV1":
            return "Acknowledge and begin investigation immediately"
        else:
            return "Acknowledge and review the incident details"

    def get_notification(self, notification_id: str) -> MobileNotification | None:
        """Get a notification by ID."""
        return self._notifications.get(notification_id)

    def handle_action(
        self,
        notification_id: str,
        action_type: ActionType,
        user: str,
    ) -> dict[str, Any]:
        """Handle a notification action."""
        notification = self._notifications.get(notification_id)
        if not notification:
            return {"error": "Notification not found"}

        result: dict[str, Any] = {
            "notification_id": notification_id,
            "action": action_type.value,
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if action_type == ActionType.ACKNOWLEDGE:
            result["status"] = "acknowledged"
            result["message"] = f"Incident {notification.incident_id} acknowledged by {user}"

        elif action_type == ActionType.ROLLBACK:
            result["status"] = "rollback_initiated"
            result["message"] = "Rollback command sent"
            result["requires_confirmation"] = True

        elif action_type == ActionType.ESCALATE:
            result["status"] = "escalated"
            result["message"] = "Escalated to next responder"

        elif action_type == ActionType.SNOOZE:
            result["status"] = "snoozed"
            result["snooze_minutes"] = 5
            result["message"] = "Snoozed for 5 minutes"

        elif action_type == ActionType.HANDOFF:
            result["status"] = "handoff_ready"
            result["message"] = "Ready for desktop handoff"
            result["handoff_url"] = f"/incidents/{notification.incident_id}"

        log.info(
            "Action handled: notification=%s action=%s user=%s",
            notification_id,
            action_type.value,
            user,
        )

        return result

    def create_handoff_message(
        self,
        notification: MobileNotification,
    ) -> dict[str, Any]:
        """Create a handoff message for desktop continuation."""
        return {
            "type": "desktop_handoff",
            "incident_id": notification.incident_id,
            "title": notification.title,
            "investigation_url": f"/investigate/{notification.incident_id}",
            "terminal_commands": self._generate_terminal_commands(notification),
            "message": "Click to continue on desktop",
        }

    def _generate_terminal_commands(
        self,
        notification: MobileNotification,
    ) -> list[str]:
        """Generate terminal commands for desktop handoff."""
        commands = []

        if notification.ai_enrichment and notification.ai_enrichment.deploy_correlation:
            deploy_id = notification.ai_enrichment.deploy_correlation.get("deploy_id")
            if deploy_id:
                commands.append(f"kubectl rollout undo deploy/{notification.incident_id.split('-')[0]}")

        commands.append(f"kubectl logs -l app={notification.incident_id.split('-')[0]} --tail=100")
        commands.append(f"kubectl get pods -l app={notification.incident_id.split('-')[0]}")

        return commands


_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get the default notification service."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service
