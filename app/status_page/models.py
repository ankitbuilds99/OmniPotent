"""Status page data models.

Defines the core domain models for the public status page system:
- Component: A monitored service or system component
- Incident: A service disruption or issue
- Maintenance: Scheduled maintenance windows
- Subscriber: Users subscribed to status updates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ComponentStatus(Enum):
    """Status values for components."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    MAINTENANCE = "maintenance"

    @property
    def display_name(self) -> str:
        """Human-readable status name."""
        return {
            ComponentStatus.OPERATIONAL: "Operational",
            ComponentStatus.DEGRADED: "Degraded Performance",
            ComponentStatus.PARTIAL_OUTAGE: "Partial Outage",
            ComponentStatus.MAJOR_OUTAGE: "Major Outage",
            ComponentStatus.MAINTENANCE: "Under Maintenance",
        }[self]

    @property
    def severity(self) -> int:
        """Severity level (higher = worse, used for overall status calculation)."""
        return {
            ComponentStatus.OPERATIONAL: 0,
            ComponentStatus.MAINTENANCE: 1,
            ComponentStatus.DEGRADED: 2,
            ComponentStatus.PARTIAL_OUTAGE: 3,
            ComponentStatus.MAJOR_OUTAGE: 4,
        }[self]


class IncidentStatus(Enum):
    """Status values for incidents."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class IncidentSeverity(Enum):
    """Incident severity levels."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class MaintenanceStatus(Enum):
    """Status values for maintenance windows."""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SubscriptionType(Enum):
    """Notification delivery methods."""

    EMAIL = "email"
    WEBHOOK = "webhook"


@dataclass
class Component:
    """A monitored service or system component."""

    id: str
    name: str
    description: str = ""
    status: ComponentStatus = ComponentStatus.OPERATIONAL
    group: str | None = None
    display_order: int = 100
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Uptime intervals are not carried on the component: they live in
    # `status_uptime_records` and are summed by `StatusStore.calculate_uptime`.
    # This used to be a list of (timestamp, duration, was_operational) tuples
    # that only the in-memory store ever read.

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-friendly dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "status_display": self.status.display_name,
            "group": self.group,
            "display_order": self.display_order,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class IncidentUpdate:
    """An update/timeline entry for an incident."""

    id: str
    incident_id: str
    status: IncidentStatus
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-friendly dictionary."""
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "status": self.status.value,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


@dataclass
class Incident:
    """An incident affecting one or more components."""

    id: str
    title: str
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    severity: IncidentSeverity = IncidentSeverity.MINOR
    message: str = ""
    affected_components: list[str] = field(default_factory=list)
    updates: list[IncidentUpdate] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Check if incident is still active."""
        return self.status != IncidentStatus.RESOLVED

    @property
    def duration_minutes(self) -> float | None:
        """Calculate incident duration in minutes."""
        if self.resolved_at:
            return (self.resolved_at - self.created_at).total_seconds() / 60
        return None

    def to_dict(self, include_updates: bool = True) -> dict[str, Any]:
        """Convert to API-friendly dictionary."""
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "affected_components": self.affected_components,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "is_active": self.is_active,
            "duration_minutes": self.duration_minutes,
        }
        if include_updates:
            data["updates"] = [u.to_dict() for u in self.updates]
        return data


@dataclass
class Maintenance:
    """A scheduled maintenance window."""

    id: str
    title: str
    description: str = ""
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
    affected_components: list[str] = field(default_factory=list)
    scheduled_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_upcoming(self) -> bool:
        """Check if maintenance is in the future."""
        return self.status == MaintenanceStatus.SCHEDULED and self.scheduled_start > datetime.now(timezone.utc)

    @property
    def is_active(self) -> bool:
        """Check if maintenance is currently in progress."""
        return self.status == MaintenanceStatus.IN_PROGRESS

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-friendly dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "affected_components": self.affected_components,
            "scheduled_start": self.scheduled_start.isoformat(),
            "scheduled_end": self.scheduled_end.isoformat(),
            "actual_start": self.actual_start.isoformat() if self.actual_start else None,
            "actual_end": self.actual_end.isoformat() if self.actual_end else None,
            "is_upcoming": self.is_upcoming,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class Subscriber:
    """A subscriber to status page updates."""

    id: str
    subscription_type: SubscriptionType
    target: str  # email address or webhook URL
    components: list[str] = field(default_factory=list)  # empty = all components
    verified: bool = False
    verification_token: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_notified_at: datetime | None = None
    notify_on_incidents: bool = True
    notify_on_maintenance: bool = True
    notify_on_resolved: bool = True

    def to_dict(self, include_token: bool = False) -> dict[str, Any]:
        """Convert to API-friendly dictionary."""
        data: dict[str, Any] = {
            "id": self.id,
            "subscription_type": self.subscription_type.value,
            "target": self._mask_target(),
            "components": self.components,
            "verified": self.verified,
            "created_at": self.created_at.isoformat(),
            "notify_on_incidents": self.notify_on_incidents,
            "notify_on_maintenance": self.notify_on_maintenance,
            "notify_on_resolved": self.notify_on_resolved,
        }
        if include_token:
            data["verification_token"] = self.verification_token
        return data

    def _mask_target(self) -> str:
        """Mask the target for privacy."""
        if self.subscription_type == SubscriptionType.EMAIL:
            if "@" in self.target:
                local, domain = self.target.split("@", 1)
                if len(local) > 2:
                    return f"{local[:2]}***@{domain}"
                return f"***@{domain}"
        elif self.subscription_type == SubscriptionType.WEBHOOK:
            # Show only the domain
            if "://" in self.target:
                parts = self.target.split("://", 1)
                if len(parts) > 1:
                    domain = parts[1].split("/")[0]
                    return f"{parts[0]}://{domain}/***"
        return "***"
