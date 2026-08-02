"""Component status tracking for the status page.

Tracks operational status of all system components with:
- Real-time status monitoring
- Component groups and dependencies
- Historical uptime tracking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.status_page.components")


class ComponentStatus(Enum):
    """Component operational status."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"

    @property
    def severity(self) -> int:
        """Get severity level (higher = worse)."""
        return {
            ComponentStatus.OPERATIONAL: 0,
            ComponentStatus.MAINTENANCE: 1,
            ComponentStatus.DEGRADED: 2,
            ComponentStatus.PARTIAL_OUTAGE: 3,
            ComponentStatus.MAJOR_OUTAGE: 4,
            ComponentStatus.UNKNOWN: 5,
        }.get(self, 5)


@dataclass
class StatusChange:
    """Record of a status change."""

    component_id: str
    old_status: ComponentStatus
    new_status: ComponentStatus
    changed_at: datetime
    reason: str | None = None
    incident_id: str | None = None


@dataclass
class Component:
    """A monitored component."""

    component_id: str
    name: str
    description: str
    status: ComponentStatus = ComponentStatus.OPERATIONAL
    group_id: str | None = None
    display_order: int = 100
    showcase: bool = True
    dependencies: list[str] = field(default_factory=list)
    last_checked: datetime | None = None
    uptime_history: list[tuple[datetime, ComponentStatus]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "group_id": self.group_id,
            "display_order": self.display_order,
            "showcase": self.showcase,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


@dataclass
class ComponentGroup:
    """A group of related components."""

    group_id: str
    name: str
    description: str
    display_order: int = 100
    collapsed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "description": self.description,
            "display_order": self.display_order,
            "collapsed": self.collapsed,
        }


class StatusRegistry:
    """Registry for component status tracking."""

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}
        self._groups: dict[str, ComponentGroup] = {}
        self._history: list[StatusChange] = []
        self._setup_default_components()

    def _setup_default_components(self) -> None:
        """Set up default components."""
        groups = [
            ComponentGroup("api", "API", "Core API services", display_order=1),
            ComponentGroup("data", "Data Services", "Database and storage", display_order=2),
            ComponentGroup("infra", "Infrastructure", "Cloud infrastructure", display_order=3),
        ]
        for g in groups:
            self._groups[g.group_id] = g

        components = [
            Component(
                "api",
                "API",
                "Core REST API endpoints",
                group_id="api",
                display_order=1,
            ),
            Component(
                "dashboard",
                "Dashboard",
                "Web dashboard application",
                group_id="api",
                display_order=2,
            ),
            Component(
                "checkout",
                "Checkout",
                "Payment and checkout flows",
                group_id="api",
                display_order=3,
            ),
            Component(
                "payments",
                "Payment Processing",
                "Payment gateway integration",
                group_id="api",
                display_order=4,
            ),
            Component(
                "notifications",
                "Notifications",
                "Email and push notifications",
                group_id="api",
                display_order=5,
            ),
            Component(
                "reporting",
                "Reporting",
                "Analytics and reports",
                group_id="data",
                display_order=1,
            ),
            Component(
                "database",
                "Database",
                "Primary database cluster",
                group_id="data",
                display_order=2,
                showcase=False,
            ),
            Component(
                "cdn",
                "CDN",
                "Content delivery network",
                group_id="infra",
                display_order=1,
            ),
        ]

        for c in components:
            self._components[c.component_id] = c

    def register_component(self, component: Component) -> None:
        """Register a new component."""
        self._components[component.component_id] = component
        log.info("component registered", extra={"component_id": component.component_id})

    def register_group(self, group: ComponentGroup) -> None:
        """Register a component group."""
        self._groups[group.group_id] = group

    def get_component(self, component_id: str) -> Component | None:
        """Get a component by ID."""
        return self._components.get(component_id)

    def list_components(
        self,
        group_id: str | None = None,
        showcase_only: bool = False,
    ) -> list[Component]:
        """List components with optional filtering."""
        components = list(self._components.values())

        if group_id:
            components = [c for c in components if c.group_id == group_id]

        if showcase_only:
            components = [c for c in components if c.showcase]

        return sorted(components, key=lambda c: (c.display_order, c.name))

    def list_groups(self) -> list[ComponentGroup]:
        """List all component groups."""
        return sorted(self._groups.values(), key=lambda g: g.display_order)

    def update_status(
        self,
        component_id: str,
        status: ComponentStatus,
        reason: str | None = None,
        incident_id: str | None = None,
    ) -> bool:
        """Update a component's status."""
        component = self._components.get(component_id)
        if not component:
            return False

        old_status = component.status
        if old_status == status:
            return True

        now = datetime.now(timezone.utc)

        change = StatusChange(
            component_id=component_id,
            old_status=old_status,
            new_status=status,
            changed_at=now,
            reason=reason,
            incident_id=incident_id,
        )
        self._history.append(change)

        component.status = status
        component.last_checked = now
        component.uptime_history.append((now, status))

        if len(component.uptime_history) > 1000:
            component.uptime_history = component.uptime_history[-1000:]

        log.info(
            "component status updated",
            extra={
                "component_id": component_id,
                "old_status": old_status.value,
                "new_status": status.value,
            },
        )

        return True

    def get_overall_status(self) -> ComponentStatus:
        """Get the overall system status."""
        showcase_components = self.list_components(showcase_only=True)
        if not showcase_components:
            return ComponentStatus.UNKNOWN

        worst = ComponentStatus.OPERATIONAL
        for comp in showcase_components:
            if comp.status.severity > worst.severity:
                worst = comp.status

        return worst

    def calculate_uptime(
        self,
        component_id: str,
        days: int = 30,
    ) -> float:
        """Calculate uptime percentage for a component."""
        component = self._components.get(component_id)
        if not component:
            return 0.0

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        relevant = [
            (ts, status) for ts, status in component.uptime_history
            if ts >= start
        ]

        if not relevant:
            return 100.0

        total_minutes = days * 24 * 60
        down_minutes: float = 0

        for i, (ts, status) in enumerate(relevant):
            if status in (ComponentStatus.MAJOR_OUTAGE, ComponentStatus.PARTIAL_OUTAGE):
                if i + 1 < len(relevant):
                    duration = (relevant[i + 1][0] - ts).total_seconds() / 60
                else:
                    duration = (now - ts).total_seconds() / 60
                down_minutes += duration

        uptime: float = ((total_minutes - down_minutes) / total_minutes) * 100
        return max(0, min(100, uptime))

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of all component statuses."""
        components = self.list_components(showcase_only=True)

        by_status: dict[str, int] = {}
        for comp in components:
            status = comp.status.value
            by_status[status] = by_status.get(status, 0) + 1

        overall = self.get_overall_status()

        return {
            "overall_status": overall.value,
            "total_components": len(components),
            "by_status": by_status,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def get_public_status(self) -> dict[str, Any]:
        """Get status data for public status page."""
        groups_data = []

        for group in self.list_groups():
            components = self.list_components(group_id=group.group_id, showcase_only=True)
            if components:
                groups_data.append({
                    **group.to_dict(),
                    "components": [c.to_dict() for c in components],
                })

        ungrouped = [c for c in self.list_components(showcase_only=True) if not c.group_id]
        if ungrouped:
            groups_data.append({
                "group_id": None,
                "name": "Other",
                "components": [c.to_dict() for c in ungrouped],
            })

        uptimes = {
            comp.component_id: round(self.calculate_uptime(comp.component_id), 2)
            for comp in self.list_components(showcase_only=True)
        }

        return {
            "overall_status": self.get_overall_status().value,
            "groups": groups_data,
            "uptimes_30d": uptimes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_history(
        self,
        component_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get status change history."""
        history = self._history

        if component_id:
            history = [h for h in history if h.component_id == component_id]

        history = sorted(history, key=lambda h: h.changed_at, reverse=True)[:limit]

        return [
            {
                "component_id": h.component_id,
                "old_status": h.old_status.value,
                "new_status": h.new_status.value,
                "changed_at": h.changed_at.isoformat(),
                "reason": h.reason,
                "incident_id": h.incident_id,
            }
            for h in history
        ]


_registry: StatusRegistry | None = None


def get_status_registry() -> StatusRegistry:
    """Get the default status registry."""
    global _registry
    if _registry is None:
        _registry = StatusRegistry()
    return _registry
