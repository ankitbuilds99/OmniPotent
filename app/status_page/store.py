"""Durable status page storage.

Everything the public status page shows lived in four dicts on an in-process
object: components, incidents, maintenance windows and subscribers. A restart
wiped all of it, so the page a customer's users refresh during an outage came
back reading "All Systems Operational" the moment the process that owned the
incident went away — and with more than one worker, which answer they got
depended on which worker they hit. The subscriber dict took the worst of it:
those are the customer's users' email addresses and webhook URLs, collected
through a public form and lost on every deploy.

Six tables now, all filtered by `org_uuid()`. The five built-in components are
seeded per org on first read and then belong to that org, so a component an org
deletes stays deleted.

Two things the dicts got wrong came along for the ride and are fixed here:

* Uptime was always 100%. `update_component` computed the interval as
  `now - component.updated_at` *after* setting `updated_at = now`, so it was
  always 0, the `if duration > 0` guard never passed, and no uptime record was
  ever written by anything. The 30- and 90-day figures on the page were the
  "no records, assume perfect" fallback, permanently. Intervals are now
  measured from `status_since` and written on every status transition, whether
  it comes from an admin, an incident or a maintenance window.
* Maintenance in progress vanished. `list_maintenances(upcoming_only=True)`
  asked for `status in (SCHEDULED, IN_PROGRESS)` *and* `scheduled_start >= now`
  — which an in-progress window can never satisfy, since it started in the
  past. The moment maintenance began it disappeared from the status page that
  announced it.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.database.orgs import ensure_org_row
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

# Marker on the org's settings JSON. Seeding on "this org has no components"
# would resurrect the built-ins every time an org deleted the last one.
_SEEDED_KEY = "status_components_seeded"

# (id, name, description, group, display_order)
DEFAULT_COMPONENTS: list[tuple[str, str, str, str, int]] = [
    ("api", "API", "Core REST API endpoints", "Core", 1),
    ("dashboard", "Dashboard", "Web dashboard application", "Core", 2),
    ("database", "Database", "Primary database cluster", "Infrastructure", 3),
    ("authentication", "Authentication", "Login and authentication services", "Core", 4),
    ("notifications", "Notifications", "Email and push notifications", "Services", 5),
]

# The longest window anything asks for is the 90-day uptime on the status page.
# Records are pruned past this rather than capped at a row count, so a component
# that flaps a few thousand times in an afternoon cannot push last month out.
_UPTIME_RETENTION = timedelta(days=90)

# PATCH bodies reach these calls as **kwargs. The dicts fed them to `setattr`
# behind a `hasattr` guard, which also accepted `id` and `created_at`.
_COMPONENT_FIELDS = frozenset({"name", "description", "status", "group", "display_order"})
_MAINTENANCE_FIELDS = frozenset(
    {"title", "description", "scheduled_start", "scheduled_end", "status"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    """Read a timestamp back as UTC-aware.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips to a naive datetime, and the response models would hand the frontend
    an ISO string with no offset.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize a timestamp to UTC before it is stored or compared.

    SQLAlchemy's SQLite DATETIME writes the wall clock and drops the offset, so
    a maintenance window scheduled for 09:00+02:00 would be stored as 09:00 and
    read back as 09:00 UTC — two hours late, and announced to subscribers that
    way.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _generate_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# --- row -> domain object ----------------------------------------------------


def _to_component(row: Any) -> Component:
    return Component(
        id=row.component_id,
        name=row.name,
        description=row.description,
        status=ComponentStatus(row.status),
        group=row.group_name,
        display_order=row.display_order,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _to_update(row: Any) -> IncidentUpdate:
    return IncidentUpdate(
        id=row.update_id,
        incident_id=row.incident_id,
        status=IncidentStatus(row.status),
        message=row.message,
        created_at=_utc(row.created_at),
        created_by=row.created_by,
    )


def _to_incident(row: Any, updates: list[IncidentUpdate]) -> Incident:
    return Incident(
        id=row.incident_id,
        title=row.title,
        status=IncidentStatus(row.status),
        severity=IncidentSeverity(row.severity),
        message=row.message,
        affected_components=list(row.affected_components or []),
        updates=updates,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        resolved_at=_utc(row.resolved_at) if row.resolved_at else None,
    )


def _to_maintenance(row: Any) -> Maintenance:
    return Maintenance(
        id=row.maintenance_id,
        title=row.title,
        description=row.description,
        status=MaintenanceStatus(row.status),
        affected_components=list(row.affected_components or []),
        scheduled_start=_utc(row.scheduled_start),
        scheduled_end=_utc(row.scheduled_end),
        actual_start=_utc(row.actual_start) if row.actual_start else None,
        actual_end=_utc(row.actual_end) if row.actual_end else None,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _to_subscriber(row: Any) -> Subscriber:
    return Subscriber(
        id=row.subscriber_id,
        subscription_type=SubscriptionType(row.subscription_type),
        target=row.target,
        components=list(row.components or []),
        verified=row.verified,
        verification_token=row.verification_token,
        created_at=_utc(row.created_at),
        last_notified_at=_utc(row.last_notified_at) if row.last_notified_at else None,
        notify_on_incidents=row.notify_on_incidents,
        notify_on_maintenance=row.notify_on_maintenance,
        notify_on_resolved=row.notify_on_resolved,
    )


async def _ensure_seeded(session: Any) -> uuid.UUID:
    """Give this org its own copy of the built-in components, once."""
    from app.database.models import Organization, StatusComponent

    org = await ensure_org_row(session)
    row = (
        await session.execute(select(Organization).where(Organization.id == org))
    ).scalar_one_or_none()
    if row is None or (row.settings or {}).get(_SEEDED_KEY):
        return org

    now = _now()
    for comp_id, name, description, group, order in DEFAULT_COMPONENTS:
        session.add(
            StatusComponent(
                id=uuid.uuid4(),
                org_id=org,
                component_id=comp_id,
                name=name,
                description=description,
                status=ComponentStatus.OPERATIONAL.value,
                group_name=group,
                display_order=order,
                created_at=now,
                updated_at=now,
                status_since=now,
            )
        )
    # Rebound, not mutated in place: SQLAlchemy does not track edits inside a
    # plain JSON value, so `row.settings[key] = ...` would never be flushed.
    row.settings = {**(row.settings or {}), _SEEDED_KEY: True}
    await session.commit()
    return org


def _record_transition(session: Any, org: uuid.UUID, row: Any, now: datetime) -> None:
    """Close the interval a component has been sitting in its current status.

    Called just before the status changes. The record says how long the
    component spent in the status it is leaving, and whether that status counted
    as up — which is what `calculate_uptime` sums.
    """
    from app.database.models import StatusUptimeRecord

    duration = (now - _utc(row.status_since)).total_seconds() / 60
    if duration <= 0:
        return
    session.add(
        StatusUptimeRecord(
            id=uuid.uuid4(),
            org_id=org,
            component_id=row.component_id,
            recorded_at=now,
            duration_minutes=duration,
            was_operational=row.status == ComponentStatus.OPERATIONAL.value,
        )
    )


async def _set_status(
    session: Any,
    org: uuid.UUID,
    component_ids: list[str],
    status: ComponentStatus,
    now: datetime,
) -> None:
    """Move the named components to `status`, recording what they are leaving.

    Components that do not exist are skipped, the way the dicts skipped ids that
    were not keys.
    """
    from app.database.models import StatusComponent

    if not component_ids:
        return
    rows = (
        (
            await session.execute(
                select(StatusComponent).where(
                    StatusComponent.org_id == org,
                    StatusComponent.component_id.in_(component_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.status != status.value:
            _record_transition(session, org, row, now)
            row.status = status.value
            row.status_since = now
        row.updated_at = now


class StatusStore:
    """Status page storage for the org on the current request.

    Stateless: every method resolves the org from the request context when it
    runs, so one instance is shared by every request.
    """

    # --- Components ---

    async def create_component(
        self,
        name: str,
        description: str = "",
        group: str | None = None,
        display_order: int = 100,
    ) -> Component:
        from app.database.engine import get_session
        from app.database.models import StatusComponent

        component_id = _generate_id("comp_")
        now = _now()

        async with get_session() as session:
            org = await _ensure_seeded(session)
            session.add(
                StatusComponent(
                    id=uuid.uuid4(),
                    org_id=org,
                    component_id=component_id,
                    name=name,
                    description=description,
                    status=ComponentStatus.OPERATIONAL.value,
                    group_name=group,
                    display_order=display_order,
                    created_at=now,
                    updated_at=now,
                    status_since=now,
                )
            )
            await session.commit()

        # Not `"name"`: that is a LogRecord attribute, and logging raises
        # KeyError rather than shadowing it — so this line, not the store, was
        # what made POST /api/status/components a 500.
        log.info(
            "component created",
            extra={"component_id": component_id, "component_name": name},
        )
        return Component(
            id=component_id,
            name=name,
            description=description,
            group=group,
            display_order=display_order,
            created_at=now,
            updated_at=now,
        )

    async def get_component(self, component_id: str) -> Component | None:
        from app.database.engine import get_session
        from app.database.models import StatusComponent

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = (
                await session.execute(
                    select(StatusComponent).where(
                        StatusComponent.org_id == org,
                        StatusComponent.component_id == component_id,
                    )
                )
            ).scalar_one_or_none()
            return _to_component(row) if row else None

    async def list_components(
        self,
        group: str | None = None,
        status: ComponentStatus | None = None,
    ) -> list[Component]:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            return await self._list_components(session, org, group, status)

    async def _list_components(
        self,
        session: Any,
        org: uuid.UUID,
        group: str | None = None,
        status: ComponentStatus | None = None,
    ) -> list[Component]:
        from app.database.models import StatusComponent

        stmt = select(StatusComponent).where(StatusComponent.org_id == org)
        if group:
            stmt = stmt.where(StatusComponent.group_name == group)
        if status:
            stmt = stmt.where(StatusComponent.status == status.value)
        stmt = stmt.order_by(StatusComponent.display_order, StatusComponent.name)
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_component(row) for row in rows]

    async def update_component(
        self,
        component_id: str,
        **updates: Any,
    ) -> Component | None:
        from app.database.engine import get_session
        from app.database.models import StatusComponent

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = (
                await session.execute(
                    select(StatusComponent).where(
                        StatusComponent.org_id == org,
                        StatusComponent.component_id == component_id,
                    )
                )
            ).scalar_one_or_none()
            if not row:
                return None

            now = _now()
            for key, value in updates.items():
                if key not in _COMPONENT_FIELDS:
                    continue
                if key == "status":
                    new_status = (
                        value if isinstance(value, ComponentStatus) else ComponentStatus(value)
                    )
                    if row.status != new_status.value:
                        _record_transition(session, org, row, now)
                        row.status = new_status.value
                        row.status_since = now
                elif key == "group":
                    row.group_name = value
                else:
                    setattr(row, key, value)

            row.updated_at = now
            await session.commit()

            log.info(
                "component updated",
                extra={"component_id": component_id, "updates": list(updates.keys())},
            )
            return _to_component(row)

    async def delete_component(self, component_id: str) -> bool:
        from app.database.engine import get_session, rows_affected
        from app.database.models import StatusComponent, StatusUptimeRecord

        async with get_session() as session:
            org = await _ensure_seeded(session)
            result = await session.execute(
                delete(StatusComponent).where(
                    StatusComponent.org_id == org,
                    StatusComponent.component_id == component_id,
                )
            )
            if not rows_affected(result):
                await session.rollback()
                return False
            await session.execute(
                delete(StatusUptimeRecord).where(
                    StatusUptimeRecord.org_id == org,
                    StatusUptimeRecord.component_id == component_id,
                )
            )
            await session.commit()

        log.info("component deleted", extra={"component_id": component_id})
        return True

    # --- Incidents ---

    async def create_incident(
        self,
        title: str,
        message: str = "",
        severity: IncidentSeverity = IncidentSeverity.MINOR,
        affected_components: list[str] | None = None,
    ) -> Incident:
        from app.database.engine import get_session
        from app.database.models import StatusIncident, StatusIncidentUpdate

        incident_id = _generate_id("inc_")
        update_id = _generate_id("upd_")
        components = list(affected_components or [])
        now = _now()

        async with get_session() as session:
            org = await _ensure_seeded(session)
            session.add(
                StatusIncident(
                    id=uuid.uuid4(),
                    org_id=org,
                    incident_id=incident_id,
                    title=title,
                    status=IncidentStatus.INVESTIGATING.value,
                    severity=severity.value,
                    message=message,
                    affected_components=components,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                StatusIncidentUpdate(
                    id=uuid.uuid4(),
                    org_id=org,
                    update_id=update_id,
                    incident_id=incident_id,
                    status=IncidentStatus.INVESTIGATING.value,
                    message=message or f"Investigating: {title}",
                    created_at=now,
                    sequence=0,
                )
            )
            await _set_status(session, org, components, ComponentStatus.PARTIAL_OUTAGE, now)
            await session.commit()

        log.info("incident created", extra={"incident_id": incident_id, "title": title})
        return Incident(
            id=incident_id,
            title=title,
            severity=severity,
            message=message,
            affected_components=components,
            updates=[
                IncidentUpdate(
                    id=update_id,
                    incident_id=incident_id,
                    status=IncidentStatus.INVESTIGATING,
                    message=message or f"Investigating: {title}",
                    created_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    async def get_incident(self, incident_id: str) -> Incident | None:
        from app.database.engine import get_session
        from app.database.models import StatusIncident

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = (
                await session.execute(
                    select(StatusIncident).where(
                        StatusIncident.org_id == org,
                        StatusIncident.incident_id == incident_id,
                    )
                )
            ).scalar_one_or_none()
            if not row:
                return None
            updates = await self._updates_for(session, org, [incident_id])
            return _to_incident(row, updates.get(incident_id, []))

    async def _updates_for(
        self,
        session: Any,
        org: uuid.UUID,
        incident_ids: list[str],
    ) -> dict[str, list[IncidentUpdate]]:
        """Every incident's timeline in one query, keyed by incident id."""
        from app.database.models import StatusIncidentUpdate

        by_incident: dict[str, list[IncidentUpdate]] = {}
        if not incident_ids:
            return by_incident

        rows = (
            (
                await session.execute(
                    select(StatusIncidentUpdate)
                    .where(
                        StatusIncidentUpdate.org_id == org,
                        StatusIncidentUpdate.incident_id.in_(incident_ids),
                    )
                    .order_by(
                        StatusIncidentUpdate.created_at,
                        StatusIncidentUpdate.sequence,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            by_incident.setdefault(row.incident_id, []).append(_to_update(row))
        return by_incident

    async def list_incidents(
        self,
        active_only: bool = False,
        limit: int = 50,
        days: int | None = None,
    ) -> list[Incident]:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            return await self._list_incidents(session, org, active_only, limit, days)

    async def _list_incidents(
        self,
        session: Any,
        org: uuid.UUID,
        active_only: bool = False,
        limit: int = 50,
        days: int | None = None,
    ) -> list[Incident]:
        from app.database.models import StatusIncident

        stmt = select(StatusIncident).where(StatusIncident.org_id == org)
        if active_only:
            stmt = stmt.where(StatusIncident.status != IncidentStatus.RESOLVED.value)
        if days:
            stmt = stmt.where(
                StatusIncident.created_at >= _as_utc(_now() - timedelta(days=days))
            )
        # `incident_id` breaks ties: several incidents opened in the same second
        # would otherwise come back in an order the database is free to change
        # between two requests for the same page.
        stmt = stmt.order_by(
            StatusIncident.created_at.desc(), StatusIncident.incident_id
        ).limit(limit)

        rows = (await session.execute(stmt)).scalars().all()
        updates = await self._updates_for(session, org, [r.incident_id for r in rows])
        return [_to_incident(row, updates.get(row.incident_id, [])) for row in rows]

    async def _next_sequence(self, session: Any, org: uuid.UUID, incident_id: str) -> int:
        from app.database.models import StatusIncidentUpdate

        highest = (
            await session.execute(
                select(func.max(StatusIncidentUpdate.sequence)).where(
                    StatusIncidentUpdate.org_id == org,
                    StatusIncidentUpdate.incident_id == incident_id,
                )
            )
        ).scalar()
        return (highest or 0) + 1

    async def update_incident(
        self,
        incident_id: str,
        status: IncidentStatus | None = None,
        message: str | None = None,
        created_by: str | None = None,
    ) -> Incident | None:
        from app.database.engine import get_session
        from app.database.models import StatusIncident, StatusIncidentUpdate

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = (
                await session.execute(
                    select(StatusIncident).where(
                        StatusIncident.org_id == org,
                        StatusIncident.incident_id == incident_id,
                    )
                )
            ).scalar_one_or_none()
            if not row:
                return None

            now = _now()
            if status:
                row.status = status.value

            if message or status:
                session.add(
                    StatusIncidentUpdate(
                        id=uuid.uuid4(),
                        org_id=org,
                        update_id=_generate_id("upd_"),
                        incident_id=incident_id,
                        status=row.status,
                        message=message or f"Status updated to {row.status}",
                        created_at=now,
                        created_by=created_by,
                        sequence=await self._next_sequence(session, org, incident_id),
                    )
                )

            row.updated_at = now
            await session.commit()

            log.info(
                "incident updated",
                extra={"incident_id": incident_id, "status": row.status},
            )
            updates = await self._updates_for(session, org, [incident_id])
            return _to_incident(row, updates.get(incident_id, []))

    async def resolve_incident(
        self,
        incident_id: str,
        message: str = "",
        created_by: str | None = None,
    ) -> Incident | None:
        from app.database.engine import get_session
        from app.database.models import StatusIncident, StatusIncidentUpdate

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = (
                await session.execute(
                    select(StatusIncident).where(
                        StatusIncident.org_id == org,
                        StatusIncident.incident_id == incident_id,
                    )
                )
            ).scalar_one_or_none()
            if not row:
                return None

            now = _now()
            row.status = IncidentStatus.RESOLVED.value
            row.resolved_at = now
            row.updated_at = now
            session.add(
                StatusIncidentUpdate(
                    id=uuid.uuid4(),
                    org_id=org,
                    update_id=_generate_id("upd_"),
                    incident_id=incident_id,
                    status=IncidentStatus.RESOLVED.value,
                    message=message or "This incident has been resolved.",
                    created_at=now,
                    created_by=created_by,
                    sequence=await self._next_sequence(session, org, incident_id),
                )
            )
            await _set_status(
                session,
                org,
                list(row.affected_components or []),
                ComponentStatus.OPERATIONAL,
                now,
            )
            await session.commit()

            log.info("incident resolved", extra={"incident_id": incident_id})
            updates = await self._updates_for(session, org, [incident_id])
            return _to_incident(row, updates.get(incident_id, []))

    # --- Maintenance ---

    async def create_maintenance(
        self,
        title: str,
        description: str = "",
        scheduled_start: datetime | None = None,
        scheduled_end: datetime | None = None,
        affected_components: list[str] | None = None,
    ) -> Maintenance:
        from app.database.engine import get_session
        from app.database.models import StatusMaintenance

        maintenance_id = _generate_id("maint_")
        now = _now()
        start = _as_utc(scheduled_start) if scheduled_start else now
        end = _as_utc(scheduled_end) if scheduled_end else now + timedelta(hours=2)
        components = list(affected_components or [])

        async with get_session() as session:
            org = await _ensure_seeded(session)
            session.add(
                StatusMaintenance(
                    id=uuid.uuid4(),
                    org_id=org,
                    maintenance_id=maintenance_id,
                    title=title,
                    description=description,
                    status=MaintenanceStatus.SCHEDULED.value,
                    affected_components=components,
                    scheduled_start=start,
                    scheduled_end=end,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        log.info(
            "maintenance created",
            extra={"maintenance_id": maintenance_id, "title": title},
        )
        return Maintenance(
            id=maintenance_id,
            title=title,
            description=description,
            affected_components=components,
            scheduled_start=start,
            scheduled_end=end,
            created_at=now,
            updated_at=now,
        )

    async def get_maintenance(self, maintenance_id: str) -> Maintenance | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._maintenance_row(session, org, maintenance_id)
            return _to_maintenance(row) if row else None

    async def _maintenance_row(
        self, session: Any, org: uuid.UUID, maintenance_id: str
    ) -> Any:
        from app.database.models import StatusMaintenance

        return (
            await session.execute(
                select(StatusMaintenance).where(
                    StatusMaintenance.org_id == org,
                    StatusMaintenance.maintenance_id == maintenance_id,
                )
            )
        ).scalar_one_or_none()

    async def list_maintenances(
        self,
        upcoming_only: bool = False,
        limit: int = 50,
    ) -> list[Maintenance]:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            return await self._list_maintenances(session, org, upcoming_only, limit)

    async def _list_maintenances(
        self,
        session: Any,
        org: uuid.UUID,
        upcoming_only: bool = False,
        limit: int = 50,
    ) -> list[Maintenance]:
        from app.database.models import StatusMaintenance

        stmt = select(StatusMaintenance).where(StatusMaintenance.org_id == org)
        if upcoming_only:
            # Scheduled and still ahead, or already running. The second half is
            # what the dict version could not express: it required
            # `scheduled_start >= now` of everything, so a window disappeared
            # from the status page at the moment it started.
            stmt = stmt.where(
                or_(
                    and_(
                        StatusMaintenance.status == MaintenanceStatus.SCHEDULED.value,
                        StatusMaintenance.scheduled_start >= _as_utc(_now()),
                    ),
                    StatusMaintenance.status == MaintenanceStatus.IN_PROGRESS.value,
                )
            )
        stmt = stmt.order_by(
            StatusMaintenance.scheduled_start, StatusMaintenance.maintenance_id
        ).limit(limit)

        rows = (await session.execute(stmt)).scalars().all()
        return [_to_maintenance(row) for row in rows]

    async def update_maintenance(
        self,
        maintenance_id: str,
        **updates: Any,
    ) -> Maintenance | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._maintenance_row(session, org, maintenance_id)
            if not row:
                return None

            for key, value in updates.items():
                if key not in _MAINTENANCE_FIELDS:
                    continue
                if key == "status":
                    row.status = (
                        value.value
                        if isinstance(value, MaintenanceStatus)
                        else MaintenanceStatus(value).value
                    )
                elif key in ("scheduled_start", "scheduled_end"):
                    setattr(row, key, _as_utc(value))
                else:
                    setattr(row, key, value)

            row.updated_at = _now()
            await session.commit()

            log.info("maintenance updated", extra={"maintenance_id": maintenance_id})
            return _to_maintenance(row)

    async def start_maintenance(self, maintenance_id: str) -> Maintenance | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._maintenance_row(session, org, maintenance_id)
            if not row:
                return None

            now = _now()
            row.status = MaintenanceStatus.IN_PROGRESS.value
            row.actual_start = now
            row.updated_at = now
            await _set_status(
                session,
                org,
                list(row.affected_components or []),
                ComponentStatus.MAINTENANCE,
                now,
            )
            await session.commit()

            log.info("maintenance started", extra={"maintenance_id": maintenance_id})
            return _to_maintenance(row)

    async def complete_maintenance(self, maintenance_id: str) -> Maintenance | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._maintenance_row(session, org, maintenance_id)
            if not row:
                return None

            now = _now()
            row.status = MaintenanceStatus.COMPLETED.value
            row.actual_end = now
            row.updated_at = now
            await _set_status(
                session,
                org,
                list(row.affected_components or []),
                ComponentStatus.OPERATIONAL,
                now,
            )
            await session.commit()

            log.info("maintenance completed", extra={"maintenance_id": maintenance_id})
            return _to_maintenance(row)

    # --- Subscribers ---

    async def create_subscriber(
        self,
        subscription_type: SubscriptionType,
        target: str,
        components: list[str] | None = None,
        verified: bool = False,
        notify_on_incidents: bool = True,
        notify_on_maintenance: bool = True,
        notify_on_resolved: bool = True,
    ) -> Subscriber:
        """Create a subscription, or return the one this org already has.

        The preferences are arguments rather than fields the caller sets on the
        way out. The router used to create the subscriber and then assign
        `subscriber.notify_on_incidents` and `subscriber.verified` on the
        returned object, which worked only because that object was the one in
        the dict; against a table those assignments would land on a detached
        copy and never be stored, so every webhook subscription would have come
        back unverified and every preference would have been ignored.
        """
        from app.database.engine import get_session
        from app.database.models import StatusSubscriber

        subscriber_id = _generate_id("sub_")
        now = _now()
        subscriber = Subscriber(
            id=subscriber_id,
            subscription_type=subscription_type,
            target=target,
            components=list(components or []),
            verified=verified,
            verification_token=None if verified else secrets.token_urlsafe(32),
            created_at=now,
            notify_on_incidents=notify_on_incidents,
            notify_on_maintenance=notify_on_maintenance,
            notify_on_resolved=notify_on_resolved,
        )

        async with get_session() as session:
            org = await _ensure_seeded(session)
            session.add(
                StatusSubscriber(
                    id=uuid.uuid4(),
                    org_id=org,
                    subscriber_id=subscriber_id,
                    subscription_type=subscription_type.value,
                    target=target,
                    components=subscriber.components,
                    verified=verified,
                    verification_token=subscriber.verification_token,
                    notify_on_incidents=notify_on_incidents,
                    notify_on_maintenance=notify_on_maintenance,
                    notify_on_resolved=notify_on_resolved,
                    created_at=now,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # Two subscribe requests for the same address raced past the
                # router's check. One address, one subscription: the loser
                # returns the winner's rather than 500-ing at the caller, who
                # did nothing wrong.
                await session.rollback()
                existing = await self._subscriber_by_target(session, org, target)
                if existing is not None:
                    return _to_subscriber(existing)
                raise

        log.info(
            "subscriber created",
            extra={"subscriber_id": subscriber_id, "type": subscription_type.value},
        )
        return subscriber

    async def get_subscriber(self, subscriber_id: str) -> Subscriber | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._subscriber_row(session, org, subscriber_id)
            return _to_subscriber(row) if row else None

    async def _subscriber_row(
        self, session: Any, org: uuid.UUID, subscriber_id: str
    ) -> Any:
        from app.database.models import StatusSubscriber

        return (
            await session.execute(
                select(StatusSubscriber).where(
                    StatusSubscriber.org_id == org,
                    StatusSubscriber.subscriber_id == subscriber_id,
                )
            )
        ).scalar_one_or_none()

    async def _subscriber_by_target(
        self, session: Any, org: uuid.UUID, target: str
    ) -> Any:
        from app.database.models import StatusSubscriber

        return (
            await session.execute(
                select(StatusSubscriber).where(
                    StatusSubscriber.org_id == org,
                    StatusSubscriber.target == target,
                )
            )
        ).scalar_one_or_none()

    async def get_subscriber_by_target(self, target: str) -> Subscriber | None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._subscriber_by_target(session, org, target)
            return _to_subscriber(row) if row else None

    async def verify_subscriber(
        self,
        subscriber_id: str,
        token: str,
    ) -> bool:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._subscriber_row(session, org, subscriber_id)
            if not row or not row.verification_token or not token:
                return False
            # The token is a bearer credential arriving from a public link;
            # compared without short-circuiting on the first wrong byte.
            if not secrets.compare_digest(row.verification_token, token):
                return False

            row.verified = True
            row.verification_token = None
            await session.commit()

        log.info("subscriber verified", extra={"subscriber_id": subscriber_id})
        return True

    async def list_subscribers(
        self,
        verified_only: bool = True,
        component_id: str | None = None,
    ) -> list[Subscriber]:
        from app.database.engine import get_session
        from app.database.models import StatusSubscriber

        async with get_session() as session:
            org = await _ensure_seeded(session)
            stmt = select(StatusSubscriber).where(StatusSubscriber.org_id == org)
            if verified_only:
                stmt = stmt.where(StatusSubscriber.verified.is_(True))
            stmt = stmt.order_by(StatusSubscriber.created_at, StatusSubscriber.subscriber_id)
            rows = (await session.execute(stmt)).scalars().all()

        subscribers = [_to_subscriber(row) for row in rows]
        # `components` is a JSON array and an empty one means "everything", so
        # this filter cannot be pushed into a WHERE that works on both SQLite
        # and Postgres.
        if component_id:
            subscribers = [
                s for s in subscribers if not s.components or component_id in s.components
            ]
        return subscribers

    async def delete_subscriber(self, subscriber_id: str) -> bool:
        from app.database.engine import get_session, rows_affected
        from app.database.models import StatusSubscriber

        async with get_session() as session:
            org = await _ensure_seeded(session)
            result = await session.execute(
                delete(StatusSubscriber).where(
                    StatusSubscriber.org_id == org,
                    StatusSubscriber.subscriber_id == subscriber_id,
                )
            )
            if not rows_affected(result):
                await session.rollback()
                return False
            await session.commit()

        log.info("subscriber deleted", extra={"subscriber_id": subscriber_id})
        return True

    async def update_subscriber_notified(self, subscriber_id: str) -> None:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            row = await self._subscriber_row(session, org, subscriber_id)
            if row:
                row.last_notified_at = _now()
                await session.commit()

    # --- Uptime ---

    async def record_uptime(
        self,
        component_id: str,
        duration_minutes: float,
        was_operational: bool,
    ) -> None:
        from app.database.engine import get_session
        from app.database.models import StatusUptimeRecord

        now = _now()
        async with get_session() as session:
            org = await _ensure_seeded(session)
            session.add(
                StatusUptimeRecord(
                    id=uuid.uuid4(),
                    org_id=org,
                    component_id=component_id,
                    recorded_at=now,
                    duration_minutes=duration_minutes,
                    was_operational=was_operational,
                )
            )
            await session.execute(
                delete(StatusUptimeRecord).where(
                    StatusUptimeRecord.org_id == org,
                    StatusUptimeRecord.component_id == component_id,
                    StatusUptimeRecord.recorded_at < _as_utc(now - _UPTIME_RETENTION),
                )
            )
            await session.commit()

    async def calculate_uptime(
        self,
        component_id: str,
        days: int = 30,
    ) -> float:
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            uptimes = await self._uptimes(session, org, [component_id], days)
        return uptimes.get(component_id, 100.0)

    async def _uptimes(
        self,
        session: Any,
        org: uuid.UUID,
        component_ids: list[str],
        days: int,
    ) -> dict[str, float]:
        """Uptime percentage per component, in one aggregate.

        A component with no records is left out; every caller reads that as
        100%, which is what a component that has never left OPERATIONAL means.
        """
        from app.database.models import StatusUptimeRecord

        if not component_ids:
            return {}

        cutoff = _as_utc(_now() - timedelta(days=days))
        rows = await session.execute(
            select(
                StatusUptimeRecord.component_id,
                func.sum(StatusUptimeRecord.duration_minutes),
                func.sum(
                    case(
                        (
                            StatusUptimeRecord.was_operational.is_(True),
                            StatusUptimeRecord.duration_minutes,
                        ),
                        else_=0.0,
                    )
                ),
            )
            .where(
                StatusUptimeRecord.org_id == org,
                StatusUptimeRecord.component_id.in_(component_ids),
                StatusUptimeRecord.recorded_at >= cutoff,
            )
            .group_by(StatusUptimeRecord.component_id)
        )

        uptimes: dict[str, float] = {}
        for component_id, total, operational in rows:
            if not total:
                continue
            uptimes[component_id] = round((operational or 0.0) / total * 100, 2)
        return uptimes

    # --- Status Summary ---

    async def get_status_summary(self) -> dict[str, Any]:
        """The full public status page, in one session."""
        from app.database.engine import get_session

        async with get_session() as session:
            org = await _ensure_seeded(session)
            components = await self._list_components(session, org)
            active_incidents = await self._list_incidents(session, org, active_only=True)
            recent_incidents = await self._list_incidents(
                session, org, active_only=False, limit=10, days=7
            )
            upcoming = await self._list_maintenances(session, org, upcoming_only=True)

            component_ids = [c.id for c in components]
            uptimes_30d = await self._uptimes(session, org, component_ids, 30)
            uptimes_90d = await self._uptimes(session, org, component_ids, 90)

        uptimes_30d = {c.id: uptimes_30d.get(c.id, 100.0) for c in components}
        uptimes_90d = {c.id: uptimes_90d.get(c.id, 100.0) for c in components}

        overall_status = ComponentStatus.OPERATIONAL
        for comp in components:
            if comp.status.severity > overall_status.severity:
                overall_status = comp.status

        groups: dict[str | None, list[dict[str, Any]]] = {}
        for comp in components:
            comp_data = comp.to_dict()
            comp_data["uptime_30d"] = uptimes_30d[comp.id]
            comp_data["uptime_90d"] = uptimes_90d[comp.id]
            groups.setdefault(comp.group, []).append(comp_data)

        return {
            "overall_status": overall_status.value,
            "overall_status_display": overall_status.display_name,
            "components": [c.to_dict() for c in components],
            "groups": groups,
            "uptimes_30d": uptimes_30d,
            "uptimes_90d": uptimes_90d,
            "active_incidents": [i.to_dict() for i in active_incidents],
            "recent_incidents": [i.to_dict() for i in recent_incidents if not i.is_active],
            "upcoming_maintenances": [m.to_dict() for m in upcoming],
            "updated_at": _now().isoformat(),
        }


# Stateless — the org comes from the request context inside each call — so one
# instance serves every request. The dicts it replaced needed one object per org
# and got a shared one, which published every tenant's subscriber list to all of
# them until `scoped()` was put in front.
_store = StatusStore()


def get_status_store() -> StatusStore:
    """The status store for the org on the current request."""
    return _store
