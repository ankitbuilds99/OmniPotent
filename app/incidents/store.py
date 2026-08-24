"""Durable incident store.

This was a process-memory dict seeded with three fabricated incidents. Two
consequences: every incident a customer declared vanished on the next deploy or
restart, and the seeded rows appeared in every org's list as if they were real.
Scoping the dict per-org stopped the cross-tenant leak but not the loss.

It is now the `incidents` / `incident_investigations` tables. The org filter is
`org_uuid()` on every read and every write, so isolation no longer depends on
which in-process instance the caller happened to get, and the seeded demo
incidents are gone: a new org starts with an empty list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.orgs import ensure_org_row, org_uuid
from app.incidents.models import (
    AgentFinding,
    AgentState,
    AgentStatus,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Investigation,
    SafetyGate,
    TimelineEntry,
)

AGENT_TYPES = ["knowledge", "reasoning", "action", "learning", "collaboration", "ambient"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(model: Any) -> Any:
    """Pydantic → JSON-safe primitives, for the JSON document columns."""
    return model.model_dump(mode="json")


def _to_investigation(row: Any) -> Investigation:
    return Investigation(
        id=row.id,
        incident_id=row.incident_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        overall_confidence=row.overall_confidence,
        root_cause=row.root_cause,
        recommended_actions=list(row.recommended_actions or []),
        agents={
            name: AgentState.model_validate(state)
            for name, state in (row.agents or {}).items()
        },
        safety_gates=[SafetyGate.model_validate(g) for g in (row.safety_gates or [])],
    )


def _to_incident(row: Any) -> Incident:
    return Incident(
        id=row.id,
        title=row.title,
        description=row.description,
        severity=IncidentSeverity(row.severity),
        status=IncidentStatus(row.status),
        service=row.service,
        triggered_at=row.triggered_at,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
        acknowledged_by=row.acknowledged_by,
        resolved_by=row.resolved_by,
        assignee=row.assignee,
        tags=list(row.tags or []),
        timeline=[TimelineEntry.model_validate(e) for e in (row.timeline or [])],
        investigation=_to_investigation(row.investigation) if row.investigation else None,
        related_alerts=[UUID(a) for a in (row.related_alerts or [])],
        impact=row.impact,
        customer_impact=row.customer_impact,
    )


def _append_timeline(row: Any, entry: TimelineEntry) -> None:
    """Append to the JSON timeline.

    Rebinding the attribute rather than mutating the loaded list is what marks
    the column dirty — SQLAlchemy does not track in-place edits of a plain JSON
    value, so an `.append()` here would be silently dropped at flush.
    """
    row.timeline = [*(row.timeline or []), _dump(entry)]


class IncidentStore:
    """Incident persistence for the org on the current request.

    Stateless: every method opens its own session and filters on `org_uuid()`.
    """

    async def _fetch(self, session: Any, incident_id: UUID) -> Any:
        from app.database.models import Incident as IncidentRow

        return (
            await session.execute(
                select(IncidentRow)
                .options(selectinload(IncidentRow.investigation))
                .where(IncidentRow.id == incident_id, IncidentRow.org_id == org_uuid())
            )
        ).scalar_one_or_none()

    async def list_incidents(
        self,
        status: IncidentStatus | None = None,
        severity: IncidentSeverity | None = None,
        service: str | None = None,
        limit: int = 50,
    ) -> list[Incident]:
        from app.database.engine import get_session
        from app.database.models import Incident as IncidentRow

        async with get_session() as session:
            stmt = (
                select(IncidentRow)
                .options(selectinload(IncidentRow.investigation))
                .where(IncidentRow.org_id == org_uuid())
                .order_by(IncidentRow.triggered_at.desc())
            )
            if status:
                stmt = stmt.where(IncidentRow.status == status.value)
            if severity:
                stmt = stmt.where(IncidentRow.severity == severity.value)
            if service:
                stmt = stmt.where(IncidentRow.service == service)

            rows = (await session.execute(stmt.limit(max(1, limit)))).scalars().all()
            return [_to_incident(row) for row in rows]

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            return _to_incident(row) if row else None

    async def create_incident(
        self,
        title: str,
        severity: IncidentSeverity,
        service: str,
        description: str | None = None,
        tags: list[str] | None = None,
        impact: str | None = None,
        customer_impact: bool = False,
    ) -> Incident:
        from app.database.engine import get_session
        from app.database.models import Incident as IncidentRow

        async with get_session() as session:
            org = await ensure_org_row(session)
            row = IncidentRow(
                org_id=org,
                title=title,
                description=description,
                severity=severity.value,
                status=IncidentStatus.TRIGGERED.value,
                service=service,
                triggered_at=_now(),
                tags=list(tags or []),
                timeline=[
                    _dump(
                        TimelineEntry(
                            timestamp=_now(),
                            type="triggered",
                            message=f"Incident created: {title}",
                        )
                    )
                ],
                related_alerts=[],
                impact=impact,
                customer_impact=customer_impact,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row, ["investigation"])
            return _to_incident(row)

    async def update_incident(
        self, incident_id: UUID, updates: dict[str, Any]
    ) -> Incident | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if not row:
                return None

            if "status" in updates:
                new_status = IncidentStatus(updates["status"])
                if new_status.value != row.status:
                    row.status = new_status.value
                    _append_timeline(
                        row,
                        TimelineEntry(
                            timestamp=_now(),
                            type="status_change",
                            message=f"Status changed to {new_status.value}",
                        ),
                    )
                    if new_status == IncidentStatus.RESOLVED:
                        row.resolved_at = _now()

            if "severity" in updates:
                row.severity = IncidentSeverity(updates["severity"]).value
            if "assignee" in updates:
                row.assignee = updates["assignee"]
            if "title" in updates:
                row.title = updates["title"]
            if "description" in updates:
                row.description = updates["description"]

            await session.commit()
            return _to_incident(row)

    async def acknowledge_incident(self, incident_id: UUID, user: str) -> Incident | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if not row:
                return None

            now = _now()
            row.acknowledged_at = now
            row.acknowledged_by = user
            row.status = IncidentStatus.INVESTIGATING.value
            _append_timeline(
                row,
                TimelineEntry(
                    timestamp=now, type="acknowledged", message=f"Acknowledged by {user}", user=user
                ),
            )
            await session.commit()
            return _to_incident(row)

    async def resolve_incident(
        self, incident_id: UUID, user: str, notes: str | None = None
    ) -> Incident | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if not row:
                return None

            now = _now()
            row.resolved_at = now
            row.resolved_by = user
            row.status = IncidentStatus.RESOLVED.value
            message = f"Resolved by {user}" + (f": {notes}" if notes else "")
            _append_timeline(
                row,
                TimelineEntry(timestamp=now, type="resolved", message=message, user=user),
            )
            await session.commit()
            return _to_incident(row)

    async def start_investigation(self, incident_id: UUID) -> Investigation | None:
        from app.database.engine import get_session
        from app.database.models import IncidentInvestigation

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if not row:
                return None
            if row.investigation:
                # One investigation per incident (the FK is unique). A repeated
                # trigger — the auto-investigate webhook fires on every alert —
                # returns the running one instead of failing.
                return _to_investigation(row.investigation)

            investigation = IncidentInvestigation(
                org_id=row.org_id,
                incident_id=row.id,
                started_at=_now(),
                agents={
                    agent: _dump(AgentState(agent_type=agent, status=AgentStatus.IDLE))
                    for agent in AGENT_TYPES
                },
                safety_gates=[
                    _dump(SafetyGate(gate_type="confidence", threshold=0.8)),
                    _dump(SafetyGate(gate_type="policy")),
                    _dump(SafetyGate(gate_type="sandbox")),
                ],
                recommended_actions=[],
            )
            session.add(investigation)
            _append_timeline(
                row,
                TimelineEntry(
                    timestamp=_now(),
                    type="investigation_started",
                    message="6-agent parallel investigation initiated",
                ),
            )
            await session.commit()
            await session.refresh(investigation)
            return _to_investigation(investigation)

    async def get_investigation(self, investigation_id: UUID) -> Investigation | None:
        from app.database.engine import get_session
        from app.database.models import IncidentInvestigation

        async with get_session() as session:
            row = (
                await session.execute(
                    select(IncidentInvestigation).where(
                        IncidentInvestigation.id == investigation_id,
                        IncidentInvestigation.org_id == org_uuid(),
                    )
                )
            ).scalar_one_or_none()
            return _to_investigation(row) if row else None

    async def update_agent_status(
        self,
        investigation_id: UUID,
        agent_type: str,
        status: AgentStatus,
        progress: int | None = None,
        current_action: str | None = None,
        finding: AgentFinding | None = None,
    ) -> Investigation | None:
        from app.database.engine import get_session
        from app.database.models import IncidentInvestigation

        async with get_session() as session:
            row = (
                await session.execute(
                    select(IncidentInvestigation).where(
                        IncidentInvestigation.id == investigation_id,
                        IncidentInvestigation.org_id == org_uuid(),
                    )
                )
            ).scalar_one_or_none()
            if not row or agent_type not in (row.agents or {}):
                return None

            agents = {
                name: AgentState.model_validate(state)
                for name, state in row.agents.items()
            }
            agent = agents[agent_type]
            agent.status = status

            if progress is not None:
                agent.progress = progress
            if current_action is not None:
                agent.current_action = current_action
            if finding:
                agent.findings.append(finding)

            if status == AgentStatus.RUNNING and not agent.started_at:
                agent.started_at = _now()
            if status == AgentStatus.COMPLETE:
                agent.completed_at = _now()
                agent.progress = 100

            confidences = [
                f.confidence
                for a in agents.values()
                if a.status == AgentStatus.COMPLETE
                for f in a.findings
            ]
            if confidences:
                row.overall_confidence = sum(confidences) / len(confidences)

            row.agents = {name: _dump(state) for name, state in agents.items()}
            await session.commit()
            return _to_investigation(row)


_STORE = IncidentStore()


def get_incident_store() -> IncidentStore:
    """The incident store. Stateless — the org comes from the request context."""
    return _STORE
