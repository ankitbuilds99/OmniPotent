"""AI-generated status updates.

Generates customer-friendly status updates from technical incident data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.status_page.updates")


class UpdateTone(Enum):
    """Tone for status updates."""

    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    TECHNICAL = "technical"
    BRIEF = "brief"


@dataclass
class InternalIncidentData:
    """Internal incident data for generating updates."""

    error_type: str
    error_details: str
    affected_service: str
    error_count: int
    affected_users_estimate: int
    root_cause: str | None = None
    fix_status: str | None = None
    deploy_info: str | None = None
    eta_minutes: int | None = None
    additional_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """A generated status update."""

    update_id: str
    customer_message: str
    internal_summary: str
    tone: UpdateTone
    generated_at: datetime
    reviewed: bool = False
    approved_by: str | None = None
    published: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id,
            "customer_message": self.customer_message,
            "internal_summary": self.internal_summary,
            "tone": self.tone.value,
            "generated_at": self.generated_at.isoformat(),
            "reviewed": self.reviewed,
            "approved_by": self.approved_by,
            "published": self.published,
        }


class AIUpdateGenerator:
    """Generates customer-friendly status updates from technical data."""

    def __init__(self, default_tone: UpdateTone = UpdateTone.PROFESSIONAL) -> None:
        self.default_tone = default_tone
        self._templates: dict[str, dict[str, str]] = self._setup_templates()
        self._update_counter = 0

    def _setup_templates(self) -> dict[str, dict[str, str]]:
        """Set up update templates by tone."""
        return {
            "investigating": {
                UpdateTone.PROFESSIONAL.value: "We are currently investigating reports of {issue_summary}. Our team is actively working to identify the cause and restore normal operations.",
                UpdateTone.FRIENDLY.value: "Hey there - we've noticed some issues with {issue_summary} and are looking into it right now. We'll keep you posted!",
                UpdateTone.TECHNICAL.value: "Investigating: {error_type} affecting {service}. Initial analysis in progress.",
                UpdateTone.BRIEF.value: "Investigating {issue_summary}. Updates to follow.",
            },
            "identified": {
                UpdateTone.PROFESSIONAL.value: "We have identified the cause of {issue_summary}. Our engineering team is implementing a fix. {eta_statement}",
                UpdateTone.FRIENDLY.value: "Good news - we've found what's causing {issue_summary}. Our team is working on a fix now. {eta_statement}",
                UpdateTone.TECHNICAL.value: "Root cause identified: {root_cause}. Fix in progress. {eta_statement}",
                UpdateTone.BRIEF.value: "Cause identified. Fix in progress. {eta_statement}",
            },
            "monitoring": {
                UpdateTone.PROFESSIONAL.value: "A fix has been deployed for {issue_summary}. We are monitoring the situation to ensure stability. {action_statement}",
                UpdateTone.FRIENDLY.value: "We've deployed a fix for {issue_summary} and things are looking better! We're keeping an eye on everything to make sure it stays that way. {action_statement}",
                UpdateTone.TECHNICAL.value: "Fix deployed. Monitoring metrics. Current error rate: {current_rate}. {action_statement}",
                UpdateTone.BRIEF.value: "Fix deployed. Monitoring for stability.",
            },
            "resolved": {
                UpdateTone.PROFESSIONAL.value: "The issue affecting {issue_summary} has been resolved. All systems are now operating normally. {action_statement}",
                UpdateTone.FRIENDLY.value: "Great news - {issue_summary} is all fixed now! Everything should be back to normal. {action_statement}",
                UpdateTone.TECHNICAL.value: "Incident resolved. Root cause: {root_cause}. Fix: {fix_status}. MTTR: {duration}.",
                UpdateTone.BRIEF.value: "Resolved. Normal operations restored.",
            },
        }

    def generate(
        self,
        incident_data: InternalIncidentData,
        status: str,
        tone: UpdateTone | None = None,
    ) -> StatusUpdate:
        """Generate a customer-friendly status update."""
        tone = tone or self.default_tone
        self._update_counter += 1
        update_id = f"update_{self._update_counter}"

        issue_summary = self._create_issue_summary(incident_data)
        template = self._templates.get(status, {}).get(tone.value, "")

        variables = {
            "issue_summary": issue_summary,
            "error_type": incident_data.error_type,
            "service": incident_data.affected_service,
            "root_cause": incident_data.root_cause or "under investigation",
            "fix_status": incident_data.fix_status or "in progress",
            "eta_statement": self._create_eta_statement(incident_data.eta_minutes),
            "action_statement": self._create_action_statement(incident_data, status),
            "current_rate": f"{incident_data.error_count} errors",
            "duration": "N/A",
        }

        customer_message = template.format(**variables)
        internal_summary = self._create_internal_summary(incident_data)

        return StatusUpdate(
            update_id=update_id,
            customer_message=customer_message,
            internal_summary=internal_summary,
            tone=tone,
            generated_at=datetime.now(timezone.utc),
        )

    def _create_issue_summary(self, data: InternalIncidentData) -> str:
        """Create a customer-friendly issue summary."""
        service_names = {
            "checkout-api": "checkout",
            "payment-service": "payment processing",
            "user-service": "account services",
            "api-gateway": "API",
            "billing-db": "billing",
        }

        service = service_names.get(data.affected_service, data.affected_service)

        if "500" in data.error_type or "error" in data.error_type.lower():
            return f"errors affecting {service}"
        elif "timeout" in data.error_type.lower():
            return f"slow response times in {service}"
        elif "connection" in data.error_type.lower():
            return f"connectivity issues with {service}"
        else:
            return f"issues with {service}"

    def _create_eta_statement(self, eta_minutes: int | None) -> str:
        """Create an ETA statement."""
        if not eta_minutes:
            return "We will provide updates as we have more information."

        if eta_minutes <= 15:
            return "We expect to resolve this within the next 15 minutes."
        elif eta_minutes <= 30:
            return "We expect resolution within 30 minutes."
        elif eta_minutes <= 60:
            return "We anticipate resolution within the next hour."
        else:
            hours = eta_minutes // 60
            return f"We expect this to be resolved within {hours} hours."

    def _create_action_statement(
        self,
        data: InternalIncidentData,
        status: str,
    ) -> str:
        """Create a statement about customer actions."""
        if status == "resolved":
            if "payment" in data.affected_service.lower():
                return "Customers who experienced errors during checkout can retry now — no duplicate charges will occur."
            return "No further action is required from customers."

        if status == "monitoring":
            return "You may retry any failed operations."

        return "We apologize for any inconvenience."

    def _create_internal_summary(self, data: InternalIncidentData) -> str:
        """Create an internal summary of the incident."""
        parts = [
            f"Error: {data.error_type} in {data.affected_service}",
            f"Count: {data.error_count} errors",
            f"Users affected: ~{data.affected_users_estimate}",
        ]

        if data.root_cause:
            parts.append(f"Root cause: {data.root_cause}")
        if data.deploy_info:
            parts.append(f"Deploy: {data.deploy_info}")
        if data.fix_status:
            parts.append(f"Fix: {data.fix_status}")

        return " | ".join(parts)

    def suggest_updates(
        self,
        incident_data: InternalIncidentData,
        current_status: str,
    ) -> list[StatusUpdate]:
        """Suggest multiple update options for review."""
        suggestions = []

        for tone in [UpdateTone.PROFESSIONAL, UpdateTone.FRIENDLY, UpdateTone.BRIEF]:
            update = self.generate(incident_data, current_status, tone)
            suggestions.append(update)

        return suggestions

    def approve_update(
        self,
        update: StatusUpdate,
        approved_by: str,
    ) -> StatusUpdate:
        """Mark an update as approved."""
        update.reviewed = True
        update.approved_by = approved_by
        return update

    def publish_update(self, update: StatusUpdate) -> StatusUpdate:
        """Mark an update as published."""
        if not update.reviewed:
            raise ValueError("Update must be reviewed before publishing")
        update.published = True
        return update


_generator: AIUpdateGenerator | None = None


def get_update_generator() -> AIUpdateGenerator:
    """Get the default update generator."""
    global _generator
    if _generator is None:
        _generator = AIUpdateGenerator()
    return _generator
