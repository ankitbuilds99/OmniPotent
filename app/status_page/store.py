"""Storage layer for status page data.

Provides in-memory storage with an interface that can be swapped for
database-backed storage in production. Includes LiveStatusStore that
integrates with real health checks.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from app.status_page.models import (
    Component,
    ComponentStatus,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentUpdate,
    Maintenance,
    MaintenanceStatus,
    Subscriber,
    SubscriptionType,
)

log = logging.getLogger("omnipotent.status_page.store")


class StatusStore(ABC):
    """Abstract base class for status page storage."""

    # --- Components ---

    @abstractmethod
    async def create_component(
        self,
        name: str,
        description: str = "",
        group: str | None = None,
        display_order: int = 100,
    ) -> Component:
        """Create a new component."""
        ...

    @abstractmethod
    async def get_component(self, component_id: str) -> Component | None:
        """Get a component by ID."""
        ...

    @abstractmethod
    async def list_components(
        self,
        group: str | None = None,
        status: ComponentStatus | None = None,
    ) -> list[Component]:
        """List components with optional filtering."""
        ...

    @abstractmethod
    async def update_component(
        self,
        component_id: str,
        **updates: Any,
    ) -> Component | None:
        """Update a component."""
        ...

    @abstractmethod
    async def delete_component(self, component_id: str) -> bool:
        """Delete a component."""
        ...

    # --- Incidents ---

    @abstractmethod
    async def create_incident(
        self,
        title: str,
        message: str = "",
        severity: IncidentSeverity = IncidentSeverity.MINOR,
        affected_components: list[str] | None = None,
    ) -> Incident:
        """Create a new incident."""
        ...

    @abstractmethod
    async def get_incident(self, incident_id: str) -> Incident | None:
        """Get an incident by ID."""
        ...

    @abstractmethod
    async def list_incidents(
        self,
        active_only: bool = False,
        limit: int = 50,
        days: int | None = None,
    ) -> list[Incident]:
        """List incidents."""
        ...

    @abstractmethod
    async def update_incident(
        self,
        incident_id: str,
        status: IncidentStatus | None = None,
        message: str | None = None,
        created_by: str | None = None,
    ) -> Incident | None:
        """Update an incident with a new status update."""
        ...

    @abstractmethod
    async def resolve_incident(
        self,
        incident_id: str,
        message: str = "",
        created_by: str | None = None,
    ) -> Incident | None:
        """Resolve an incident."""
        ...

    # --- Maintenance ---

    @abstractmethod
    async def create_maintenance(
        self,
        title: str,
        description: str = "",
        scheduled_start: datetime | None = None,
        scheduled_end: datetime | None = None,
        affected_components: list[str] | None = None,
    ) -> Maintenance:
        """Create a scheduled maintenance window."""
        ...

    @abstractmethod
    async def get_maintenance(self, maintenance_id: str) -> Maintenance | None:
        """Get a maintenance window by ID."""
        ...

    @abstractmethod
    async def list_maintenances(
        self,
        upcoming_only: bool = False,
        limit: int = 50,
    ) -> list[Maintenance]:
        """List maintenance windows."""
        ...

    @abstractmethod
    async def update_maintenance(
        self,
        maintenance_id: str,
        **updates: Any,
    ) -> Maintenance | None:
        """Update a maintenance window."""
        ...

    @abstractmethod
    async def start_maintenance(self, maintenance_id: str) -> Maintenance | None:
        """Mark maintenance as in progress."""
        ...

    @abstractmethod
    async def complete_maintenance(self, maintenance_id: str) -> Maintenance | None:
        """Mark maintenance as completed."""
        ...

    # --- Subscribers ---

    @abstractmethod
    async def create_subscriber(
        self,
        subscription_type: SubscriptionType,
        target: str,
        components: list[str] | None = None,
    ) -> Subscriber:
        """Create a new subscriber."""
        ...

    @abstractmethod
    async def get_subscriber(self, subscriber_id: str) -> Subscriber | None:
        """Get a subscriber by ID."""
        ...

    @abstractmethod
    async def get_subscriber_by_target(self, target: str) -> Subscriber | None:
        """Get a subscriber by their target (email/webhook URL)."""
        ...

    @abstractmethod
    async def verify_subscriber(
        self,
        subscriber_id: str,
        token: str,
    ) -> bool:
        """Verify a subscriber with their token."""
        ...

    @abstractmethod
    async def list_subscribers(
        self,
        verified_only: bool = True,
        component_id: str | None = None,
    ) -> list[Subscriber]:
        """List subscribers."""
        ...

    @abstractmethod
    async def delete_subscriber(self, subscriber_id: str) -> bool:
        """Delete a subscriber."""
        ...

    @abstractmethod
    async def update_subscriber_notified(self, subscriber_id: str) -> None:
        """Update last notified timestamp."""
        ...

    # --- Uptime ---

    @abstractmethod
    async def record_uptime(
        self,
        component_id: str,
        duration_minutes: float,
        was_operational: bool,
    ) -> None:
        """Record an uptime measurement for a component."""
        ...

    @abstractmethod
    async def calculate_uptime(
        self,
        component_id: str,
        days: int = 30,
    ) -> float:
        """Calculate uptime percentage for a component over the specified days."""
        ...

    # --- Status Summary ---

    @abstractmethod
    async def get_status_summary(self) -> dict[str, Any]:
        """Get the full public status page summary."""
        ...


class InMemoryStatusStore(StatusStore):
    """In-memory implementation of the status store.

    Note: This base class no longer sets up hardcoded default components.
    Use LiveStatusStore for real health check integration.
    """

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}
        self._incidents: dict[str, Incident] = {}
        self._maintenances: dict[str, Maintenance] = {}
        self._subscribers: dict[str, Subscriber] = {}

    def _generate_id(self, prefix: str = "") -> str:
        """Generate a unique ID."""
        return f"{prefix}{uuid.uuid4().hex[:12]}"

    # --- Components ---

    async def create_component(
        self,
        name: str,
        description: str = "",
        group: str | None = None,
        display_order: int = 100,
    ) -> Component:
        component_id = self._generate_id("comp_")
        component = Component(
            id=component_id,
            name=name,
            description=description,
            group=group,
            display_order=display_order,
        )
        self._components[component_id] = component
        log.info("component created", extra={"component_id": component_id, "name": name})
        return component

    async def get_component(self, component_id: str) -> Component | None:
        return self._components.get(component_id)

    async def list_components(
        self,
        group: str | None = None,
        status: ComponentStatus | None = None,
    ) -> list[Component]:
        components = list(self._components.values())
        if group:
            components = [c for c in components if c.group == group]
        if status:
            components = [c for c in components if c.status == status]
        return sorted(components, key=lambda c: (c.display_order, c.name))

    async def update_component(
        self,
        component_id: str,
        **updates: Any,
    ) -> Component | None:
        component = self._components.get(component_id)
        if not component:
            return None

        now = datetime.now(timezone.utc)
        old_status = component.status

        for key, value in updates.items():
            if hasattr(component, key):
                if key == "status" and isinstance(value, str):
                    value = ComponentStatus(value)
                setattr(component, key, value)

        component.updated_at = now

        # Record uptime if status changed
        if "status" in updates and old_status != component.status:
            was_operational = old_status == ComponentStatus.OPERATIONAL
            # Approximate duration since last update
            duration = (now - component.updated_at).total_seconds() / 60 if component.updated_at else 0
            if duration > 0:
                component.uptime_records.append((now, duration, was_operational))

        log.info("component updated", extra={"component_id": component_id, "updates": list(updates.keys())})
        return component

    async def delete_component(self, component_id: str) -> bool:
        if component_id in self._components:
            del self._components[component_id]
            log.info("component deleted", extra={"component_id": component_id})
            return True
        return False

    # --- Incidents ---

    async def create_incident(
        self,
        title: str,
        message: str = "",
        severity: IncidentSeverity = IncidentSeverity.MINOR,
        affected_components: list[str] | None = None,
    ) -> Incident:
        incident_id = self._generate_id("inc_")
        now = datetime.now(timezone.utc)

        incident = Incident(
            id=incident_id,
            title=title,
            message=message,
            severity=severity,
            affected_components=affected_components or [],
            created_at=now,
            updated_at=now,
        )

        # Add initial update
        update = IncidentUpdate(
            id=self._generate_id("upd_"),
            incident_id=incident_id,
            status=IncidentStatus.INVESTIGATING,
            message=message or f"Investigating: {title}",
            created_at=now,
        )
        incident.updates.append(update)

        self._incidents[incident_id] = incident

        # Update affected components
        for comp_id in incident.affected_components:
            if comp_id in self._components:
                self._components[comp_id].status = ComponentStatus.PARTIAL_OUTAGE
                self._components[comp_id].updated_at = now

        log.info("incident created", extra={"incident_id": incident_id, "title": title})
        return incident

    async def get_incident(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    async def list_incidents(
        self,
        active_only: bool = False,
        limit: int = 50,
        days: int | None = None,
    ) -> list[Incident]:
        incidents = list(self._incidents.values())

        if active_only:
            incidents = [i for i in incidents if i.is_active]

        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            incidents = [i for i in incidents if i.created_at >= cutoff]

        incidents = sorted(incidents, key=lambda i: i.created_at, reverse=True)
        return incidents[:limit]

    async def update_incident(
        self,
        incident_id: str,
        status: IncidentStatus | None = None,
        message: str | None = None,
        created_by: str | None = None,
    ) -> Incident | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        now = datetime.now(timezone.utc)

        if status:
            incident.status = status

        if message or status:
            update = IncidentUpdate(
                id=self._generate_id("upd_"),
                incident_id=incident_id,
                status=status or incident.status,
                message=message or f"Status updated to {incident.status.value}",
                created_at=now,
                created_by=created_by,
            )
            incident.updates.append(update)

        incident.updated_at = now
        log.info("incident updated", extra={"incident_id": incident_id, "status": incident.status.value})
        return incident

    async def resolve_incident(
        self,
        incident_id: str,
        message: str = "",
        created_by: str | None = None,
    ) -> Incident | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        now = datetime.now(timezone.utc)
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = now
        incident.updated_at = now

        update = IncidentUpdate(
            id=self._generate_id("upd_"),
            incident_id=incident_id,
            status=IncidentStatus.RESOLVED,
            message=message or "This incident has been resolved.",
            created_at=now,
            created_by=created_by,
        )
        incident.updates.append(update)

        # Restore affected components
        for comp_id in incident.affected_components:
            if comp_id in self._components:
                self._components[comp_id].status = ComponentStatus.OPERATIONAL
                self._components[comp_id].updated_at = now

        log.info("incident resolved", extra={"incident_id": incident_id})
        return incident

    # --- Maintenance ---

    async def create_maintenance(
        self,
        title: str,
        description: str = "",
        scheduled_start: datetime | None = None,
        scheduled_end: datetime | None = None,
        affected_components: list[str] | None = None,
    ) -> Maintenance:
        maintenance_id = self._generate_id("maint_")
        now = datetime.now(timezone.utc)

        maintenance = Maintenance(
            id=maintenance_id,
            title=title,
            description=description,
            scheduled_start=scheduled_start or now,
            scheduled_end=scheduled_end or (now + timedelta(hours=2)),
            affected_components=affected_components or [],
            created_at=now,
            updated_at=now,
        )

        self._maintenances[maintenance_id] = maintenance
        log.info("maintenance created", extra={"maintenance_id": maintenance_id, "title": title})
        return maintenance

    async def get_maintenance(self, maintenance_id: str) -> Maintenance | None:
        return self._maintenances.get(maintenance_id)

    async def list_maintenances(
        self,
        upcoming_only: bool = False,
        limit: int = 50,
    ) -> list[Maintenance]:
        maintenances = list(self._maintenances.values())

        if upcoming_only:
            now = datetime.now(timezone.utc)
            maintenances = [
                m for m in maintenances
                if m.status in (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)
                and m.scheduled_start >= now
            ]

        maintenances = sorted(maintenances, key=lambda m: m.scheduled_start)
        return maintenances[:limit]

    async def update_maintenance(
        self,
        maintenance_id: str,
        **updates: Any,
    ) -> Maintenance | None:
        maintenance = self._maintenances.get(maintenance_id)
        if not maintenance:
            return None

        for key, value in updates.items():
            if hasattr(maintenance, key):
                if key == "status" and isinstance(value, str):
                    value = MaintenanceStatus(value)
                setattr(maintenance, key, value)

        maintenance.updated_at = datetime.now(timezone.utc)
        log.info("maintenance updated", extra={"maintenance_id": maintenance_id})
        return maintenance

    async def start_maintenance(self, maintenance_id: str) -> Maintenance | None:
        maintenance = self._maintenances.get(maintenance_id)
        if not maintenance:
            return None

        now = datetime.now(timezone.utc)
        maintenance.status = MaintenanceStatus.IN_PROGRESS
        maintenance.actual_start = now
        maintenance.updated_at = now

        # Update affected components
        for comp_id in maintenance.affected_components:
            if comp_id in self._components:
                self._components[comp_id].status = ComponentStatus.MAINTENANCE
                self._components[comp_id].updated_at = now

        log.info("maintenance started", extra={"maintenance_id": maintenance_id})
        return maintenance

    async def complete_maintenance(self, maintenance_id: str) -> Maintenance | None:
        maintenance = self._maintenances.get(maintenance_id)
        if not maintenance:
            return None

        now = datetime.now(timezone.utc)
        maintenance.status = MaintenanceStatus.COMPLETED
        maintenance.actual_end = now
        maintenance.updated_at = now

        # Restore affected components
        for comp_id in maintenance.affected_components:
            if comp_id in self._components:
                self._components[comp_id].status = ComponentStatus.OPERATIONAL
                self._components[comp_id].updated_at = now

        log.info("maintenance completed", extra={"maintenance_id": maintenance_id})
        return maintenance

    # --- Subscribers ---

    async def create_subscriber(
        self,
        subscription_type: SubscriptionType,
        target: str,
        components: list[str] | None = None,
    ) -> Subscriber:
        subscriber_id = self._generate_id("sub_")
        verification_token = secrets.token_urlsafe(32)

        subscriber = Subscriber(
            id=subscriber_id,
            subscription_type=subscription_type,
            target=target,
            components=components or [],
            verification_token=verification_token,
        )

        self._subscribers[subscriber_id] = subscriber
        log.info("subscriber created", extra={"subscriber_id": subscriber_id, "type": subscription_type.value})
        return subscriber

    async def get_subscriber(self, subscriber_id: str) -> Subscriber | None:
        return self._subscribers.get(subscriber_id)

    async def get_subscriber_by_target(self, target: str) -> Subscriber | None:
        for subscriber in self._subscribers.values():
            if subscriber.target == target:
                return subscriber
        return None

    async def verify_subscriber(
        self,
        subscriber_id: str,
        token: str,
    ) -> bool:
        subscriber = self._subscribers.get(subscriber_id)
        if not subscriber:
            return False

        if subscriber.verification_token == token:
            subscriber.verified = True
            subscriber.verification_token = None
            log.info("subscriber verified", extra={"subscriber_id": subscriber_id})
            return True
        return False

    async def list_subscribers(
        self,
        verified_only: bool = True,
        component_id: str | None = None,
    ) -> list[Subscriber]:
        subscribers = list(self._subscribers.values())

        if verified_only:
            subscribers = [s for s in subscribers if s.verified]

        if component_id:
            subscribers = [
                s for s in subscribers
                if not s.components or component_id in s.components
            ]

        return subscribers

    async def delete_subscriber(self, subscriber_id: str) -> bool:
        if subscriber_id in self._subscribers:
            del self._subscribers[subscriber_id]
            log.info("subscriber deleted", extra={"subscriber_id": subscriber_id})
            return True
        return False

    async def update_subscriber_notified(self, subscriber_id: str) -> None:
        subscriber = self._subscribers.get(subscriber_id)
        if subscriber:
            subscriber.last_notified_at = datetime.now(timezone.utc)

    # --- Uptime ---

    async def record_uptime(
        self,
        component_id: str,
        duration_minutes: float,
        was_operational: bool,
    ) -> None:
        component = self._components.get(component_id)
        if component:
            component.uptime_records.append(
                (datetime.now(timezone.utc), duration_minutes, was_operational)
            )
            # Keep only last 10000 records to bound memory
            if len(component.uptime_records) > 10000:
                component.uptime_records = component.uptime_records[-10000:]

    async def calculate_uptime(
        self,
        component_id: str,
        days: int = 30,
    ) -> float:
        component = self._components.get(component_id)
        if not component:
            return 0.0

        if not component.uptime_records:
            # Assume 100% if no records (new component)
            return 100.0

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        relevant = [
            (ts, dur, op) for ts, dur, op in component.uptime_records
            if ts >= cutoff
        ]

        if not relevant:
            return 100.0

        total_minutes = sum(dur for _, dur, _ in relevant)
        operational_minutes = sum(dur for _, dur, op in relevant if op)

        if total_minutes == 0:
            return 100.0

        return round((operational_minutes / total_minutes) * 100, 2)

    # --- Status Summary ---

    async def get_status_summary(self) -> dict[str, Any]:
        """Get the full public status page summary."""
        components = await self.list_components()
        active_incidents = await self.list_incidents(active_only=True)
        recent_incidents = await self.list_incidents(active_only=False, limit=10, days=7)
        upcoming_maintenances = await self.list_maintenances(upcoming_only=True)

        # Calculate overall status
        overall_status = ComponentStatus.OPERATIONAL
        for comp in components:
            if comp.status.severity > overall_status.severity:
                overall_status = comp.status

        # Calculate uptimes
        uptimes_30d: dict[str, float] = {}
        uptimes_90d: dict[str, float] = {}
        for comp in components:
            uptimes_30d[comp.id] = await self.calculate_uptime(comp.id, days=30)
            uptimes_90d[comp.id] = await self.calculate_uptime(comp.id, days=90)

        # Group components
        groups: dict[str | None, list[dict[str, Any]]] = {}
        for comp in components:
            group = comp.group
            if group not in groups:
                groups[group] = []
            comp_data = comp.to_dict()
            comp_data["uptime_30d"] = uptimes_30d.get(comp.id, 100.0)
            comp_data["uptime_90d"] = uptimes_90d.get(comp.id, 100.0)
            groups[group].append(comp_data)

        return {
            "overall_status": overall_status.value,
            "overall_status_display": overall_status.display_name,
            "components": [c.to_dict() for c in components],
            "groups": groups,
            "uptimes_30d": uptimes_30d,
            "uptimes_90d": uptimes_90d,
            "active_incidents": [i.to_dict() for i in active_incidents],
            "recent_incidents": [i.to_dict() for i in recent_incidents if not i.is_active],
            "upcoming_maintenances": [m.to_dict() for m in upcoming_maintenances],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class LiveStatusStore(InMemoryStatusStore):
    """Status store that integrates with real health checks.

    Components are dynamically created from:
    - HealthChecker registered checks (database, redis, llm)
    - MCPHealthMonitor registered MCP servers

    Health status mapping:
    - HEALTHY -> OPERATIONAL
    - DEGRADED -> DEGRADED
    - UNHEALTHY -> MAJOR_OUTAGE
    """

    def __init__(
        self,
        poll_interval_seconds: float = 30.0,
        auto_start_polling: bool = True,
    ) -> None:
        super().__init__()
        self._poll_interval = poll_interval_seconds
        self._polling_task: asyncio.Task | None = None
        self._auto_start = auto_start_polling
        self._started = False

    async def start(self) -> None:
        """Start the health check polling loop."""
        if self._started:
            return
        self._started = True

        await self._sync_components_from_health_checks()

        if self._auto_start:
            self._polling_task = asyncio.create_task(self._poll_health_checks())
            log.info("LiveStatusStore started polling health checks every %.0fs", self._poll_interval)

    async def stop(self) -> None:
        """Stop the health check polling loop."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        self._started = False
        log.info("LiveStatusStore stopped polling")

    async def _sync_components_from_health_checks(self) -> None:
        """Sync components from registered health checks."""
        from app.health import get_health_checker
        from app.mcp.health import get_health_monitor

        now = datetime.now(timezone.utc)
        checker = get_health_checker()
        mcp_monitor = get_health_monitor()

        component_configs = [
            ("api", "API", "Core REST API endpoints", "Core", 1),
            ("database", "Database", "Primary database cluster", "Infrastructure", 2),
            ("redis", "Redis", "Cache and session storage", "Infrastructure", 3),
            ("llm", "LLM Provider", "AI model API connectivity", "Infrastructure", 4),
        ]

        for comp_id, name, desc, group, order in component_configs:
            if comp_id not in self._components:
                self._components[comp_id] = Component(
                    id=comp_id,
                    name=name,
                    description=desc,
                    group=group,
                    display_order=order,
                    created_at=now,
                    updated_at=now,
                )

        system_health = await checker.check_all()
        for comp_name, health in system_health.components.items():
            comp_id = comp_name.lower().replace(" ", "_")
            if comp_id in self._components:
                self._components[comp_id].status = self._map_health_status(health.status)
                self._components[comp_id].updated_at = now

        mcp_summary = await mcp_monitor.check_all()
        for server_health in mcp_summary.servers:
            comp_id = f"mcp_{server_health.name}"
            if comp_id not in self._components:
                self._components[comp_id] = Component(
                    id=comp_id,
                    name=f"MCP: {server_health.name.replace('_', ' ').title()}",
                    description=f"MCP server for {server_health.name}",
                    group="MCP Servers",
                    display_order=100 + len([c for c in self._components if c.startswith("mcp_")]),
                    created_at=now,
                    updated_at=now,
                )
            self._components[comp_id].status = self._map_mcp_status(server_health.status)
            self._components[comp_id].updated_at = now

        alive, _ = await checker.liveness()
        if "api" in self._components:
            self._components["api"].status = (
                ComponentStatus.OPERATIONAL if alive else ComponentStatus.MAJOR_OUTAGE
            )
            self._components["api"].updated_at = now

        log.debug("Synced %d components from health checks", len(self._components))

    def _map_health_status(self, status: Any) -> ComponentStatus:
        """Map HealthStatus to ComponentStatus."""
        from app.health import HealthStatus

        mapping = {
            HealthStatus.HEALTHY: ComponentStatus.OPERATIONAL,
            HealthStatus.DEGRADED: ComponentStatus.DEGRADED,
            HealthStatus.UNHEALTHY: ComponentStatus.MAJOR_OUTAGE,
        }
        return mapping.get(status, ComponentStatus.OPERATIONAL)

    def _map_mcp_status(self, status: Any) -> ComponentStatus:
        """Map MCPServerStatus to ComponentStatus."""
        from app.mcp.health import MCPServerStatus

        mapping = {
            MCPServerStatus.HEALTHY: ComponentStatus.OPERATIONAL,
            MCPServerStatus.DEGRADED: ComponentStatus.DEGRADED,
            MCPServerStatus.UNHEALTHY: ComponentStatus.MAJOR_OUTAGE,
            MCPServerStatus.UNKNOWN: ComponentStatus.DEGRADED,
            MCPServerStatus.STOPPED: ComponentStatus.MAJOR_OUTAGE,
        }
        return mapping.get(status, ComponentStatus.OPERATIONAL)

    async def _poll_health_checks(self) -> None:
        """Periodically poll health checks and update component status."""
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._sync_components_from_health_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Error polling health checks: %s", e)

    async def refresh(self) -> None:
        """Manually trigger a health check sync."""
        await self._sync_components_from_health_checks()


# Global store instance
_store: StatusStore | None = None
_live_store: LiveStatusStore | None = None


def get_status_store() -> StatusStore:
    """Get the default status store.

    Returns the LiveStatusStore for real health check integration.
    """
    global _store, _live_store
    if _store is None:
        _live_store = LiveStatusStore()
        _store = _live_store
    return _store


def get_live_status_store() -> LiveStatusStore | None:
    """Get the live status store if initialized."""
    return _live_store


async def start_live_status_polling() -> None:
    """Start the live status store polling (call during app startup)."""
    store = get_status_store()
    if isinstance(store, LiveStatusStore):
        await store.start()


async def stop_live_status_polling() -> None:
    """Stop the live status store polling (call during app shutdown)."""
    if _live_store:
        await _live_store.stop()


def set_status_store(store: StatusStore) -> None:
    """Set a custom status store (for testing or database-backed storage)."""
    global _store, _live_store
    _store = store
    if isinstance(store, LiveStatusStore):
        _live_store = store
    else:
        _live_store = None
