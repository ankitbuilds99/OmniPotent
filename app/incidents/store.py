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
    AgentState,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Investigation,
    SafetyGate,
    SafetyGateStatus,
    TimelineEntry,
)

# The confidence an investigation must reach before its recommendation is
# considered strong enough to act on unattended. Recorded as a real, measured
# safety gate when a run finishes.
CONFIDENCE_THRESHOLD = 0.8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: Any) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite — the default database — has no timezone type, so a
    `DateTime(timezone=True)` column round trips to a *naive* datetime. Every
    timestamp written here is UTC, but without this the API emits
    `triggered_at` with no offset while the timeline entries in the same
    response, which are JSON and keep their offset, carry one: two conventions
    in one payload, and a client that reads the offset-less one as local time
    is hours out. It is also a TypeError waiting to happen — any
    naive-vs-aware comparison against `_now()` raises.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _dump(model: Any) -> Any:
    """Pydantic → JSON-safe primitives, for the JSON document columns."""
    return model.model_dump(mode="json")


def _to_investigation(row: Any) -> Investigation:
    return Investigation(
        id=row.id,
        incident_id=row.incident_id,
        started_at=_utc(row.started_at),
        completed_at=_utc(row.completed_at),
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
        triggered_at=_utc(row.triggered_at),
        acknowledged_at=_utc(row.acknowledged_at),
        resolved_at=_utc(row.resolved_at),
        acknowledged_by=row.acknowledged_by,
        resolved_by=row.resolved_by,
        assignee=row.assignee,
        tags=list(row.tags or []),
        timeline=[TimelineEntry.model_validate(e) for e in (row.timeline or [])],
        investigation=_to_investigation(row.investigation) if row.investigation else None,
        related_alerts=[UUID(a) for a in (row.related_alerts or [])],
        impact=row.impact,
        customer_impact=row.customer_impact,
        dedupe_key=row.dedupe_key,
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

    async def _fetch(self, session: Any, incident_id: UUID, org: UUID | None = None) -> Any:
        """The incident row, scoped to `org` (default: the request's org).

        The explicit `org` is for the background investigation path: that code
        runs after the response, where the request-scoped tenant contextvar is
        no longer guaranteed, so the caller passes the org it captured.
        """
        from app.database.models import Incident as IncidentRow

        return (
            await session.execute(
                select(IncidentRow)
                .options(selectinload(IncidentRow.investigation))
                .where(IncidentRow.id == incident_id, IncidentRow.org_id == (org or org_uuid()))
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
        dedupe_key: str | None = None,
    ) -> Incident:
        from app.database.engine import get_session
        from app.database.models import Incident as IncidentRow

        async with get_session() as session:
            org = await ensure_org_row(session)
            row = IncidentRow(
                org_id=org,
                dedupe_key=dedupe_key,
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

    async def find_open_by_dedupe_key(self, dedupe_key: str) -> Incident | None:
        """The unresolved incident already tracking this alert, if there is one.

        Scoped to unresolved on purpose: an alert that fires, resolves, and
        fires again a week later is a new incident, not a reopening of the old
        one — reopening would silently rewrite the first incident's duration and
        its postmortem's timeline.
        """
        from app.database.engine import get_session
        from app.database.models import Incident as IncidentRow

        key = (dedupe_key or "").strip()
        if not key:
            return None

        async with get_session() as session:
            row = (
                await session.execute(
                    select(IncidentRow)
                    .options(selectinload(IncidentRow.investigation))
                    .where(
                        IncidentRow.org_id == org_uuid(),
                        IncidentRow.dedupe_key == key,
                        IncidentRow.status != IncidentStatus.RESOLVED.value,
                    )
                    .order_by(IncidentRow.triggered_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _to_incident(row) if row else None

    async def add_timeline_note(
        self, incident_id: UUID, message: str, user: str | None = None, kind: str = "note"
    ) -> Incident | None:
        """Record a free-text update against the incident.

        The status updates responders type during an incident are most of what a
        postmortem timeline is made of, and there was no way to get one onto the
        record: `/incident update` posted the text into the channel and dropped
        it. Scrollback is not a timeline — it is unsearchable, unexportable, and
        gone when the war room is archived.
        """
        from app.database.engine import get_session

        text = message.strip()
        if not text:
            return await self.get_incident(incident_id)

        async with get_session() as session:
            row = await self._fetch(session, incident_id)
            if not row:
                return None

            _append_timeline(
                row,
                TimelineEntry(timestamp=_now(), type=kind, message=text, user=user),
            )
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

    async def start_investigation(
        self, incident_id: UUID, org: UUID | None = None
    ) -> Investigation | None:
        from app.database.engine import get_session
        from app.database.models import IncidentInvestigation

        async with get_session() as session:
            row = await self._fetch(session, incident_id, org)
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
                # No agents and no gates until something real reports in. This
                # used to be written with six `AgentState`s parked at `idle` and
                # three `pending` safety gates, and nothing in the codebase ever
                # moved any of them: the six "agents" did not exist, and the
                # gates were never evaluated. The row now starts empty and is
                # filled by `record_investigation_result()` from the agent run.
                agents={},
                safety_gates=[],
                recommended_actions=[],
            )
            session.add(investigation)
            _append_timeline(
                row,
                TimelineEntry(
                    timestamp=_now(),
                    type="investigation_started",
                    message="Investigation queued — the agent run reports back here",
                ),
            )
            await session.commit()
            await session.refresh(investigation)
            return _to_investigation(investigation)

    async def record_investigation_result(
        self,
        incident_id: UUID,
        *,
        root_cause: str | None,
        recommended_actions: list[str],
        confidence: float,
        org: UUID | None = None,
    ) -> Investigation | None:
        """Close an investigation out with what the agent run actually produced.

        Nothing used to finish an investigation: `start_investigation()` wrote a
        row and the row stayed exactly as written — `completed_at` null,
        `root_cause` null, `overall_confidence` 0.0 — no matter how the run
        ended, because no code path ever wrote a result back. The webhook
        investigation path calls this when the run completes.
        """
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id, org)
            if not row or not row.investigation:
                return None

            investigation = row.investigation
            investigation.completed_at = _now()
            investigation.root_cause = root_cause
            investigation.recommended_actions = list(recommended_actions)
            investigation.overall_confidence = confidence
            # The one gate this codebase actually evaluates. The row used to be
            # created with three `pending` gates — confidence, policy, sandbox —
            # that nothing ever checked; the other two are gone rather than
            # displayed forever as "pending". This one is measured: the run's own
            # confidence against the threshold the UI draws.
            investigation.safety_gates = [
                _dump(
                    SafetyGate(
                        gate_type="confidence",
                        status=(
                            SafetyGateStatus.PASSED
                            if confidence >= CONFIDENCE_THRESHOLD
                            else SafetyGateStatus.FAILED
                        ),
                        threshold=CONFIDENCE_THRESHOLD,
                        current_value=confidence,
                        message=(
                            f"Investigation confidence {confidence:.0%} "
                            f"vs {CONFIDENCE_THRESHOLD:.0%} required for autonomous action"
                        ),
                        checked_at=_now(),
                    )
                )
            ]

            _append_timeline(
                row,
                TimelineEntry(
                    timestamp=_now(),
                    type="investigation_completed",
                    message=(
                        f"Investigation complete — root cause: {root_cause}"
                        if root_cause
                        else "Investigation complete — no root cause identified"
                    )[:1000],
                ),
            )
            if root_cause and row.status == IncidentStatus.TRIGGERED.value:
                row.status = IncidentStatus.IDENTIFIED.value

            await session.commit()
            await session.refresh(investigation)
            return _to_investigation(investigation)

    async def record_investigation_failure(
        self, incident_id: UUID, error: str, *, org: UUID | None = None
    ) -> Investigation | None:
        """Mark an investigation as finished without a verdict.

        A run that dies — bad API key, provider outage, a paused approval nobody
        answers — otherwise leaves a row that reads as still running forever.
        """
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._fetch(session, incident_id, org)
            if not row or not row.investigation:
                return None

            investigation = row.investigation
            investigation.completed_at = _now()
            _append_timeline(
                row,
                TimelineEntry(
                    timestamp=_now(),
                    type="investigation_failed",
                    message=f"Investigation did not complete: {error}"[:1000],
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


_STORE = IncidentStore()


def get_incident_store() -> IncidentStore:
    """The incident store. Stateless — the org comes from the request context."""
    return _STORE
