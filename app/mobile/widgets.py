"""Mobile Widgets.

iOS/Android home screen widgets for at-a-glance status.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.mobile.widgets")


class WidgetSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class SystemStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OUTAGE = "outage"


@dataclass
class ServiceStatus:
    """Status of a service for widget display."""

    service_id: str
    name: str
    status: SystemStatus
    availability_percent: float = 99.9
    last_incident: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.service_id,
            "name": self.name,
            "status": self.status.value,
            "availability": self.availability_percent,
            "last_incident": self.last_incident.isoformat() if self.last_incident else None,
        }


@dataclass
class OnCallInfo:
    """On-call information for widget display."""

    user_id: str
    user_name: str
    shift_ends_at: datetime
    incidents_today: int = 0
    is_active_incident: bool = False

    @property
    def time_remaining(self) -> str:
        delta = self.shift_ends_at - datetime.now(timezone.utc)
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "shift_ends": self.shift_ends_at.isoformat(),
            "time_remaining": self.time_remaining,
            "incidents_today": self.incidents_today,
            "active_incident": self.is_active_incident,
        }


@dataclass
class WidgetData:
    """Data for a widget."""

    widget_id: str
    size: WidgetSize
    overall_status: SystemStatus
    services: list[ServiceStatus]
    oncall: OnCallInfo | None
    active_incidents: int
    last_incident_title: str | None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "widget_id": self.widget_id,
            "size": self.size.value,
            "status": self.overall_status.value,
            "services": [s.to_dict() for s in self.services],
            "oncall": self.oncall.to_dict() if self.oncall else None,
            "active_incidents": self.active_incidents,
            "last_incident": self.last_incident_title,
            "updated_at": self.updated_at.isoformat(),
        }

    def to_ios_widget(self) -> dict[str, Any]:
        """Format for iOS WidgetKit."""
        status_color = {
            SystemStatus.HEALTHY: "green",
            SystemStatus.DEGRADED: "yellow",
            SystemStatus.OUTAGE: "red",
        }

        entry: dict[str, Any] = {
            "date": self.updated_at.isoformat(),
            "status": self.overall_status.value,
            "statusColor": status_color.get(self.overall_status, "gray"),
            "activeIncidents": self.active_incidents,
        }

        if self.size in [WidgetSize.MEDIUM, WidgetSize.LARGE]:
            entry["services"] = [
                {
                    "name": s.name[:10],
                    "status": s.status.value,
                    "availability": f"{s.availability_percent:.1f}%",
                }
                for s in self.services[:4]
            ]

        if self.size == WidgetSize.LARGE and self.oncall:
            entry["oncall"] = {
                "name": self.oncall.user_name,
                "remaining": self.oncall.time_remaining,
            }

        return {
            "kind": "system_status",
            "family": self.size.value,
            "entries": [entry],
        }


@dataclass
class Widget:
    """A widget configuration."""

    widget_id: str
    user_id: str
    size: WidgetSize
    services_to_show: list[str]
    show_oncall: bool = True
    refresh_interval_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.widget_id,
            "user_id": self.user_id,
            "size": self.size.value,
            "services": self.services_to_show,
            "show_oncall": self.show_oncall,
            "refresh_interval": self.refresh_interval_seconds,
        }


class WidgetService:
    """Service for managing widgets."""

    def __init__(self) -> None:
        self._widgets: dict[str, Widget] = {}
        self._services: dict[str, ServiceStatus] = {}
        self._oncall: dict[str, OnCallInfo] = {}
        self._active_incidents: int = 0
        self._last_incident: str | None = None
        self._counter = 0

    def register_widget(
        self,
        user_id: str,
        size: WidgetSize,
        services: list[str] | None = None,
    ) -> Widget:
        """Register a widget for a user."""
        self._counter += 1
        widget_id = f"widget_{self._counter}"

        widget = Widget(
            widget_id=widget_id,
            user_id=user_id,
            size=size,
            services_to_show=services or [],
        )

        self._widgets[widget_id] = widget
        return widget

    def update_service_status(
        self,
        service_id: str,
        name: str,
        status: SystemStatus,
        availability: float = 99.9,
    ) -> ServiceStatus:
        """Update status of a service."""
        service = ServiceStatus(
            service_id=service_id,
            name=name,
            status=status,
            availability_percent=availability,
        )

        if status != SystemStatus.HEALTHY:
            service.last_incident = datetime.now(timezone.utc)

        self._services[service_id] = service
        return service

    def set_oncall(
        self,
        user_id: str,
        user_name: str,
        shift_ends_at: datetime,
        incidents_today: int = 0,
    ) -> OnCallInfo:
        """Set current on-call information."""
        oncall = OnCallInfo(
            user_id=user_id,
            user_name=user_name,
            shift_ends_at=shift_ends_at,
            incidents_today=incidents_today,
        )

        self._oncall[user_id] = oncall
        return oncall

    def record_incident(self, title: str) -> None:
        """Record an active incident."""
        self._active_incidents += 1
        self._last_incident = title

    def resolve_incident(self) -> None:
        """Resolve an active incident."""
        self._active_incidents = max(0, self._active_incidents - 1)

    def get_widget_data(self, widget_id: str) -> WidgetData | None:
        """Get data for a widget."""
        widget = self._widgets.get(widget_id)
        if not widget:
            return None

        if widget.services_to_show:
            services = [
                self._services[sid]
                for sid in widget.services_to_show
                if sid in self._services
            ]
        else:
            services = list(self._services.values())

        if widget.size == WidgetSize.SMALL:
            services = services[:1]
        elif widget.size == WidgetSize.MEDIUM:
            services = services[:3]
        else:
            services = services[:6]

        overall = self._calculate_overall_status(services)

        oncall = None
        if widget.show_oncall:
            oncall = self._oncall.get(widget.user_id)

        return WidgetData(
            widget_id=widget_id,
            size=widget.size,
            overall_status=overall,
            services=services,
            oncall=oncall,
            active_incidents=self._active_incidents,
            last_incident_title=self._last_incident,
        )

    def _calculate_overall_status(
        self,
        services: list[ServiceStatus],
    ) -> SystemStatus:
        """Calculate overall status from service statuses."""
        if not services:
            return SystemStatus.HEALTHY

        statuses = [s.status for s in services]

        if SystemStatus.OUTAGE in statuses:
            return SystemStatus.OUTAGE
        elif SystemStatus.DEGRADED in statuses:
            return SystemStatus.DEGRADED
        return SystemStatus.HEALTHY

    def get_widget_timeline(
        self,
        widget_id: str,
        entries: int = 5,
    ) -> list[WidgetData]:
        """Get timeline entries for widget (for iOS WidgetKit)."""
        current_data = self.get_widget_data(widget_id)
        if not current_data:
            return []

        timeline = [current_data]
        now = datetime.now(timezone.utc)

        for i in range(1, entries):
            future_time = now + timedelta(minutes=5 * i)
            future_data = WidgetData(
                widget_id=widget_id,
                size=current_data.size,
                overall_status=current_data.overall_status,
                services=current_data.services,
                oncall=current_data.oncall,
                active_incidents=current_data.active_incidents,
                last_incident_title=current_data.last_incident_title,
                updated_at=future_time,
            )
            timeline.append(future_data)

        return timeline

    def get_all_widgets(self, user_id: str) -> list[Widget]:
        """Get all widgets for a user."""
        return [
            w for w in self._widgets.values()
            if w.user_id == user_id
        ]


_service: WidgetService | None = None


def get_widget_service() -> WidgetService:
    """Get the default widget service."""
    global _service
    if _service is None:
        _service = WidgetService()
    return _service
