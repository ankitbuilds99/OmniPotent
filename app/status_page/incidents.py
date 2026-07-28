"""Incident management for the status page.

Tracks incidents and their updates for public communication.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.status_page.incidents")


class IncidentStatus(Enum):
    """Incident status for status page."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    POSTMORTEM = "postmortem"


class IncidentSeverity(Enum):
    """Incident severity."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


@dataclass
class IncidentImpact:
    """Impact assessment for an incident."""

    affected_components: list[str]
    affected_features: list[str]
    customer_impact: str
    estimated_affected_users: int | None = None
    business_impact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "affected_components": self.affected_components,
            "affected_features": self.affected_features,
            "customer_impact": self.customer_impact,
            "estimated_affected_users": self.estimated_affected_users,
            "business_impact": self.business_impact,
        }


@dataclass
class IncidentUpdate:
    """An update to an incident."""

    update_id: str
    incident_id: str
    status: IncidentStatus
    message: str
    posted_at: datetime
    posted_by: str | None = None
    internal_notes: str | None = None
    is_public: bool = True

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "update_id": self.update_id,
            "status": self.status.value,
            "message": self.message,
            "posted_at": self.posted_at.isoformat(),
        }
        if include_internal:
            data["posted_by"] = self.posted_by
            data["internal_notes"] = self.internal_notes
        return data


@dataclass
class StatusIncident:
    """An incident on the status page."""

    incident_id: str
    title: str
    status: IncidentStatus
    severity: IncidentSeverity
    impact: IncidentImpact
    created_at: datetime
    resolved_at: datetime | None = None
    updates: list[IncidentUpdate] = field(default_factory=list)
    scheduled: bool = False
    scheduled_for: datetime | None = None
    scheduled_until: datetime | None = None
    postmortem_url: str | None = None

    @property
    def duration_minutes(self) -> int | None:
        if self.resolved_at:
            return int((self.resolved_at - self.created_at).total_seconds() / 60)
        return None

    @property
    def is_active(self) -> bool:
        return self.status not in (IncidentStatus.RESOLVED, IncidentStatus.POSTMORTEM)

    def to_dict(self, include_internal: bool = False) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "status": self.status.value,
            "severity": self.severity.value,
            "impact": self.impact.to_dict(),
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "duration_minutes": self.duration_minutes,
            "updates": [u.to_dict(include_internal) for u in self.updates],
            "scheduled": self.scheduled,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "scheduled_until": self.scheduled_until.isoformat() if self.scheduled_until else None,
            "postmortem_url": self.postmortem_url,
        }


class IncidentManager:
    """Manages incidents for the status page."""

    def __init__(self) -> None:
        self._incidents: dict[str, StatusIncident] = {}
        self._incident_counter = 0
        self._update_counter = 0
        self._notification_handlers: list[Any] = []

    def create_incident(
        self,
        title: str,
        severity: IncidentSeverity,
        affected_components: list[str],
        affected_features: list[str],
        customer_impact: str,
        initial_message: str | None = None,
        posted_by: str | None = None,
    ) -> StatusIncident:
        """Create a new incident."""
        self._incident_counter += 1
        incident_id = f"inc_{self._incident_counter}"
        now = datetime.now(timezone.utc)

        impact = IncidentImpact(
            affected_components=affected_components,
            affected_features=affected_features,
            customer_impact=customer_impact,
        )

        incident = StatusIncident(
            incident_id=incident_id,
            title=title,
            status=IncidentStatus.INVESTIGATING,
            severity=severity,
            impact=impact,
            created_at=now,
        )

        if initial_message:
            self._add_update(
                incident,
                IncidentStatus.INVESTIGATING,
                initial_message,
                posted_by,
            )

        self._incidents[incident_id] = incident

        log.info(
            "incident created",
            extra={
                "incident_id": incident_id,
                "title": title,
                "severity": severity.value,
            },
        )

        self._notify("incident_created", incident)

        return incident

    def create_scheduled_maintenance(
        self,
        title: str,
        affected_components: list[str],
        scheduled_for: datetime,
        scheduled_until: datetime,
        description: str,
    ) -> StatusIncident:
        """Create a scheduled maintenance window."""
        self._incident_counter += 1
        incident_id = f"maint_{self._incident_counter}"
        now = datetime.now(timezone.utc)

        impact = IncidentImpact(
            affected_components=affected_components,
            affected_features=[],
            customer_impact=description,
        )

        incident = StatusIncident(
            incident_id=incident_id,
            title=title,
            status=IncidentStatus.INVESTIGATING,
            severity=IncidentSeverity.MINOR,
            impact=impact,
            created_at=now,
            scheduled=True,
            scheduled_for=scheduled_for,
            scheduled_until=scheduled_until,
        )

        self._add_update(
            incident,
            IncidentStatus.INVESTIGATING,
            f"Scheduled maintenance: {description}",
            None,
        )

        self._incidents[incident_id] = incident

        return incident

    def add_update(
        self,
        incident_id: str,
        status: IncidentStatus,
        message: str,
        posted_by: str | None = None,
        internal_notes: str | None = None,
    ) -> IncidentUpdate | None:
        """Add an update to an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        update = self._add_update(incident, status, message, posted_by, internal_notes)

        incident.status = status
        if status == IncidentStatus.RESOLVED:
            incident.resolved_at = datetime.now(timezone.utc)

        self._notify("incident_updated", incident, update)

        return update

    def _add_update(
        self,
        incident: StatusIncident,
        status: IncidentStatus,
        message: str,
        posted_by: str | None = None,
        internal_notes: str | None = None,
    ) -> IncidentUpdate:
        """Internal method to add an update."""
        self._update_counter += 1
        update_id = f"upd_{self._update_counter}"

        update = IncidentUpdate(
            update_id=update_id,
            incident_id=incident.incident_id,
            status=status,
            message=message,
            posted_at=datetime.now(timezone.utc),
            posted_by=posted_by,
            internal_notes=internal_notes,
        )

        incident.updates.append(update)
        return update

    def resolve_incident(
        self,
        incident_id: str,
        resolution_message: str,
        posted_by: str | None = None,
    ) -> StatusIncident | None:
        """Resolve an incident."""
        update = self.add_update(
            incident_id,
            IncidentStatus.RESOLVED,
            resolution_message,
            posted_by,
        )
        if update:
            return self._incidents.get(incident_id)
        return None

    def get_incident(self, incident_id: str) -> StatusIncident | None:
        """Get an incident by ID."""
        return self._incidents.get(incident_id)

    def get_active_incidents(self) -> list[StatusIncident]:
        """Get all active (unresolved) incidents."""
        return sorted(
            [i for i in self._incidents.values() if i.is_active],
            key=lambda i: i.created_at,
            reverse=True,
        )

    def get_recent_incidents(
        self,
        days: int = 7,
        limit: int = 10,
    ) -> list[StatusIncident]:
        """Get recent incidents."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        incidents = [
            i for i in self._incidents.values()
            if i.created_at >= cutoff
        ]

        return sorted(
            incidents,
            key=lambda i: i.created_at,
            reverse=True,
        )[:limit]

    def get_scheduled_maintenances(self) -> list[StatusIncident]:
        """Get upcoming scheduled maintenances."""
        now = datetime.now(timezone.utc)
        return sorted(
            [
                i for i in self._incidents.values()
                if i.scheduled and i.scheduled_for and i.scheduled_for > now
            ],
            key=lambda i: i.scheduled_for or now,
        )

    def get_public_page_data(self) -> dict[str, Any]:
        """Get data for the public status page."""
        active = self.get_active_incidents()
        recent = self.get_recent_incidents(days=14)
        scheduled = self.get_scheduled_maintenances()

        return {
            "active_incidents": [i.to_dict() for i in active],
            "recent_incidents": [i.to_dict() for i in recent if not i.is_active],
            "scheduled_maintenances": [i.to_dict() for i in scheduled],
            "has_active_incident": len(active) > 0,
            "current_severity": max((i.severity for i in active), key=lambda s: s.value if s else "", default=None),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_incident_timeline(self, incident_id: str) -> list[dict[str, Any]]:
        """Get the timeline of an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return []

        timeline: list[dict[str, Any]] = [
            {
                "timestamp": incident.created_at.isoformat(),
                "type": "created",
                "title": "Incident Created",
                "message": incident.title,
            }
        ]

        for update in incident.updates:
            timeline.append({
                "timestamp": update.posted_at.isoformat(),
                "type": "update",
                "status": update.status.value,
                "message": update.message,
            })

        if incident.resolved_at:
            timeline.append({
                "timestamp": incident.resolved_at.isoformat(),
                "type": "resolved",
                "title": "Incident Resolved",
                "duration_minutes": int(incident.duration_minutes) if incident.duration_minutes else None,
            })

        return timeline

    def _notify(
        self,
        event_type: str,
        incident: StatusIncident,
        update: IncidentUpdate | None = None,
    ) -> None:
        """Send notifications."""
        for handler in self._notification_handlers:
            try:
                handler(event_type, incident.to_dict(), update.to_dict() if update else None)
            except Exception as e:
                log.error("notification failed", extra={"error": str(e)})

    def add_notification_handler(self, handler: Any) -> None:
        """Add a notification handler."""
        self._notification_handlers.append(handler)


_manager: IncidentManager | None = None


def get_incident_manager() -> IncidentManager:
    """Get the default incident manager."""
    global _manager
    if _manager is None:
        _manager = IncidentManager()
    return _manager
