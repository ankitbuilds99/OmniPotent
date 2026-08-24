"""Durable runbook and execution storage.

Runbooks and their execution history were two module-level dicts. A runbook is a
script that runs against production infrastructure, so losing them on restart
lost the remediation itself, and the execution history — which is the audit
trail for "who ran what against which service" — was never written down at all.
The `runbook_executions` table existed from the first migration with no reader
and no writer.

Everything here filters on `org_uuid()`. The built-in runbooks are seeded per
org on first use and then owned by that org: deleting one makes it stay deleted,
which the shared-dict version could not express.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, overload

from sqlalchemy import select

from app.database.orgs import ensure_org_row, org_uuid
from app.runbooks.models import (
    DEFAULT_RUNBOOKS,
    ExecutionMode,
    Runbook,
    RunbookExecution,
    StepExecution,
    StepStatus,
)

# Marker on the org's settings JSON. Seeding on "this org has no runbooks yet"
# would resurrect the built-ins every time an org deleted the last one.
_SEEDED_KEY = "runbooks_seeded"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: Any) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite — the default database — has no timezone type, so a
    `DateTime(timezone=True)` column round trips to a *naive* datetime. Without
    this an execution's two timestamps disagree within one response: the step
    timestamps are parsed from ISO strings in the JSON results and keep their
    offset, while `started_at` and `completed_at` come off columns and lose
    theirs. It also makes any comparison against `_now()` a TypeError.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _to_runbook(row: Any) -> Runbook:
    return Runbook.from_dict(
        {
            "id": row.runbook_id,
            "name": row.name,
            "description": row.description,
            "service": row.service,
            "trigger": row.trigger,
            "execution_mode": row.execution_mode,
            "steps": row.steps or [],
            "labels": row.labels or {},
            "enabled": row.enabled,
        }
    )


def _to_execution(row: Any) -> RunbookExecution:
    return RunbookExecution(
        id=str(row.id),
        runbook_id=row.runbook_id,
        runbook_name=row.runbook_name,
        status=StepStatus(row.status),
        triggered_by=row.triggered_by or "manual",
        started_at=_utc(row.started_at),
        completed_at=_utc(row.completed_at),
        steps=[
            StepExecution(
                step_id=s["step_id"],
                step_name=s["step_name"],
                status=StepStatus(s["status"]),
                started_at=datetime.fromisoformat(s["started_at"]),
                completed_at=(
                    datetime.fromisoformat(s["completed_at"]) if s.get("completed_at") else None
                ),
                output=s.get("output", ""),
                error=s.get("error", ""),
            )
            for s in (row.results or {}).get("steps", [])
        ],
        context=row.variables or {},
    )


async def _ensure_seeded(session: Any) -> None:
    """Give this org its own copy of the built-in runbooks, once."""
    from app.database.models import Organization, Runbook as RunbookRow

    org = await ensure_org_row(session)
    row = (
        await session.execute(select(Organization).where(Organization.id == org))
    ).scalar_one_or_none()
    if row is None or (row.settings or {}).get(_SEEDED_KEY):
        return

    for data in DEFAULT_RUNBOOKS:
        rb = Runbook.from_dict(data)
        session.add(
            RunbookRow(
                org_id=org,
                runbook_id=rb.id,
                name=rb.name,
                description=rb.description,
                service=rb.service,
                trigger=rb.trigger,
                execution_mode=rb.execution_mode.value,
                steps=[s.to_dict() for s in rb.steps],
                labels=rb.labels,
                enabled=rb.enabled,
            )
        )
    # Rebound, not mutated in place: SQLAlchemy does not track edits inside a
    # plain JSON value, so `row.settings[key] = ...` would never be flushed.
    row.settings = {**(row.settings or {}), _SEEDED_KEY: True}
    await session.commit()


async def list_runbooks(
    service: str | None = None, trigger: str | None = None, enabled: bool | None = None
) -> list[Runbook]:
    from app.database.engine import get_session
    from app.database.models import Runbook as RunbookRow

    async with get_session() as session:
        await _ensure_seeded(session)
        stmt = (
            select(RunbookRow)
            .where(RunbookRow.org_id == org_uuid())
            .order_by(RunbookRow.created_at)
        )
        if trigger:
            stmt = stmt.where(RunbookRow.trigger == trigger)
        if enabled is not None:
            stmt = stmt.where(RunbookRow.enabled == enabled)

        rows = (await session.execute(stmt)).scalars().all()

    # `service` is filtered here, not in SQL: "*" means "applies to every
    # service" and must match whatever the caller asked for.
    return [
        _to_runbook(row)
        for row in rows
        if not service or row.service in ("*", service)
    ]


async def get_runbook(runbook_id: str) -> Runbook | None:
    from app.database.engine import get_session
    from app.database.models import Runbook as RunbookRow

    async with get_session() as session:
        await _ensure_seeded(session)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org_uuid(),
                    RunbookRow.runbook_id == runbook_id,
                )
            )
        ).scalar_one_or_none()
        return _to_runbook(row) if row else None


async def create_runbook(rb: Runbook) -> Runbook:
    from app.database.engine import get_session
    from app.database.models import Runbook as RunbookRow

    async with get_session() as session:
        org = await ensure_org_row(session)
        session.add(
            RunbookRow(
                org_id=org,
                runbook_id=rb.id,
                name=rb.name,
                description=rb.description,
                service=rb.service,
                trigger=rb.trigger,
                execution_mode=rb.execution_mode.value,
                steps=[s.to_dict() for s in rb.steps],
                labels=rb.labels,
                enabled=rb.enabled,
            )
        )
        await session.commit()
    return rb


async def delete_runbook(runbook_id: str) -> bool:
    from app.database.engine import get_session
    from app.database.models import Runbook as RunbookRow

    async with get_session() as session:
        await _ensure_seeded(session)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org_uuid(),
                    RunbookRow.runbook_id == runbook_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def record_execution(
    rb: Runbook, status: StepStatus, context: dict[str, Any], triggered_by: str = "manual"
) -> RunbookExecution:
    from app.database.engine import get_session
    from app.database.models import RunbookExecution as ExecutionRow

    async with get_session() as session:
        org = await ensure_org_row(session)
        row = ExecutionRow(
            id=uuid.uuid4(),
            org_id=org,
            runbook_id=rb.id,
            runbook_name=rb.name,
            status=status.value,
            trigger=rb.trigger,
            triggered_by=triggered_by,
            variables=context,
            results={"steps": []},
            started_at=_now(),
        )
        session.add(row)
        await session.commit()
        return _to_execution(row)


async def list_executions(
    runbook_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[RunbookExecution]:
    from app.database.engine import get_session
    from app.database.models import RunbookExecution as ExecutionRow

    async with get_session() as session:
        stmt = (
            select(ExecutionRow)
            .where(ExecutionRow.org_id == org_uuid())
            .order_by(ExecutionRow.started_at.desc())
        )
        if runbook_id:
            stmt = stmt.where(ExecutionRow.runbook_id == runbook_id)
        if status:
            stmt = stmt.where(ExecutionRow.status == status)

        rows = (await session.execute(stmt.limit(max(1, limit)))).scalars().all()
        return [_to_execution(row) for row in rows]


@overload
async def set_execution_status(
    execution_id: str, status: StepStatus
) -> RunbookExecution | None: ...


@overload
async def set_execution_status(
    execution_id: str, status: StepStatus, *, expect: set[StepStatus]
) -> RunbookExecution | None | Literal["conflict"]: ...


async def set_execution_status(
    execution_id: str, status: StepStatus, *, expect: set[StepStatus] | None = None
) -> RunbookExecution | None | str:
    """Move an execution to `status`.

    Returns the updated execution, None if this org has no such execution, or
    the string "conflict" if `expect` is given and the current status is not in
    it — which is how the router tells 404 apart from 400.

    Overloaded because "conflict" is only reachable when `expect` is passed;
    callers that omit it would otherwise have to handle a case that cannot
    happen, and the router does exactly that in one place and not the other.
    """
    from app.database.engine import get_session
    from app.database.models import RunbookExecution as ExecutionRow

    try:
        key = uuid.UUID(execution_id)
    except ValueError:
        return None

    async with get_session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == key, ExecutionRow.org_id == org_uuid()
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if expect is not None and StepStatus(row.status) not in expect:
            return "conflict"

        row.status = status.value
        if status in (StepStatus.FAILED, StepStatus.SUCCESS, StepStatus.SKIPPED):
            row.completed_at = _now()
        await session.commit()
        return _to_execution(row)


def new_runbook_id() -> str:
    return f"rb-{uuid.uuid4().hex[:8]}"


def default_execution_status(mode: ExecutionMode) -> StepStatus:
    return (
        StepStatus.PENDING
        if mode == ExecutionMode.REQUIRES_APPROVAL
        else StepStatus.RUNNING
    )
