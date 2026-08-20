"""Database-backed storage for runbook definitions and executions.

Replaces the two module-level dicts in `app/runbooks/router.py`, which lost every
authored runbook on restart and were visible only to the replica that served the
write. Both are tenant-scoped: ids are caller-supplied (a runbook id is chosen by
the server, but an execution id arrives in the URL), so every read and write
takes an `org_id` and filters on it. The composite primary key `(id, org_id)`
makes an unscoped lookup fail to compile rather than quietly return another
tenant's row.

The built-in DEFAULT_RUNBOOKS are seeded *per org* on first access rather than
shared. They are starting points an operator is expected to edit, disable or
delete, and one global copy would mean one tenant's edit rewriting everybody's.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import RunbookDefinition, RunbookRun
from app.runbooks.models import (
    DEFAULT_RUNBOOKS,
    Runbook,
    RunbookExecution,
    StepExecution,
    StepStatus,
)

log = logging.getLogger("omnipotent.runbooks.store")

# Statuses that mean the run is over. Used to decide whether `completed_at` gets
# a value: a row without one is still in flight, and the listing says so.
_FINISHED = {
    StepStatus.SUCCESS,
    StepStatus.FAILED,
    StepStatus.SKIPPED,
}


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session against a schema that is guaranteed to exist.

    Callers reach this outside the API lifespan (a worker, a script, a test), so
    the schema check cannot be assumed to have happened at startup.
    """
    await ensure_schema()
    async with get_session() as session:
        yield session


def _utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a datetime read back from SQLite.

    SQLite has no timezone type, so a value written as aware comes back naive and
    would serialize without an offset — which a client then reads as local time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# --------------------------------------------------------------------------
# Runbook definitions
# --------------------------------------------------------------------------


async def ensure_builtins(org_id: str) -> None:
    """Seed this org's copy of DEFAULT_RUNBOOKS, once.

    Idempotent by id: a builtin the operator deleted stays deleted only until the
    next call, which is a deliberate trade — the alternative is a tombstone table
    to remember deletions of rows the product ships. Edits do survive, because
    the seed inserts only ids that are absent.
    """
    async with _session() as session:
        existing = set(
            (
                await session.execute(
                    select(RunbookDefinition.id).where(RunbookDefinition.org_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        added = False
        for data in DEFAULT_RUNBOOKS:
            runbook = Runbook.from_dict(data)
            if runbook.id in existing:
                continue
            session.add(
                RunbookDefinition(
                    id=runbook.id,
                    org_id=org_id,
                    name=runbook.name,
                    service=runbook.service,
                    trigger=runbook.trigger,
                    enabled=runbook.enabled,
                    is_builtin=True,
                    definition=runbook.to_dict(),
                )
            )
            added = True
        if added:
            await session.commit()


async def save_runbook(org_id: str, runbook: Runbook, *, is_builtin: bool = False) -> None:
    """Insert or update one runbook definition."""
    async with _session() as session:
        row = await session.get(RunbookDefinition, {"id": runbook.id, "org_id": org_id})
        if row is None:
            row = RunbookDefinition(id=runbook.id, org_id=org_id, is_builtin=is_builtin)
            session.add(row)
        row.name = runbook.name
        row.service = runbook.service
        row.trigger = runbook.trigger
        row.enabled = runbook.enabled
        # Reassigned, not mutated in place: SQLAlchemy only marks a JSON column
        # dirty on assignment, so an in-place edit is silently dropped.
        row.definition = runbook.to_dict()
        await session.commit()


async def load_runbook(org_id: str, runbook_id: str) -> Runbook | None:
    """Load one runbook, or None if this org has no such id."""
    async with _session() as session:
        row = await session.get(RunbookDefinition, {"id": runbook_id, "org_id": org_id})
        return _deserialize_runbook(row) if row is not None else None


async def list_runbooks(
    org_id: str,
    *,
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[Runbook]:
    """List this org's runbooks, newest first, with the router's filters applied.

    `service` matches a runbook scoped to that service *or* the wildcard "*",
    which is how a runbook declares it applies everywhere.
    """
    stmt = select(RunbookDefinition).where(RunbookDefinition.org_id == org_id)
    if service is not None:
        stmt = stmt.where(RunbookDefinition.service.in_([service, "*"]))
    if trigger is not None:
        stmt = stmt.where(RunbookDefinition.trigger == trigger)
    if enabled is not None:
        stmt = stmt.where(RunbookDefinition.enabled == enabled)
    stmt = stmt.order_by(RunbookDefinition.created_at.asc(), RunbookDefinition.id.asc())

    async with _session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    out = []
    for row in rows:
        runbook = _deserialize_runbook(row)
        if runbook is not None:
            out.append(runbook)
    return out


async def delete_runbook(org_id: str, runbook_id: str) -> bool:
    """Delete one runbook. Returns whether a row was actually removed."""
    async with _session() as session:
        result = await session.execute(
            delete(RunbookDefinition).where(
                RunbookDefinition.id == runbook_id,
                RunbookDefinition.org_id == org_id,
            )
        )
        await session.commit()
        return cast("CursorResult[Any]", result).rowcount > 0


def _deserialize_runbook(row: RunbookDefinition) -> Runbook | None:
    """Rebuild a Runbook, logging rather than raising on a payload we cannot read.

    One unreadable row must not take out the whole listing: an operator who
    cannot see their other runbooks during an incident is worse off than one
    missing the broken one.
    """
    try:
        return Runbook.from_dict(row.definition)
    except Exception:
        log.exception("Discarding unreadable runbook %s for org %s", row.id, row.org_id)
        return None


# --------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------


async def save_execution(org_id: str, execution: RunbookExecution) -> None:
    """Insert or update one execution record.

    Called before the first step runs and again when the run ends, so the row
    exists for the whole window in which a caller polls it.
    """
    async with _session() as session:
        row = await session.get(RunbookRun, {"id": execution.id, "org_id": org_id})
        if row is None:
            row = RunbookRun(id=execution.id, org_id=org_id)
            session.add(row)
        row.runbook_id = execution.runbook_id
        row.runbook_name = execution.runbook_name
        row.status = execution.status.value
        row.triggered_by = execution.triggered_by
        row.execution = execution.to_dict()
        row.completed_at = (
            execution.completed_at
            if execution.completed_at is not None
            else (datetime.now(timezone.utc) if execution.status in _FINISHED else None)
        )
        await session.commit()


async def mark_failed(org_id: str, execution_id: str, error: str) -> bool:
    """Flip an existing run to FAILED without inventing step results.

    Used when the executor raises: the exception carries no per-step detail, and
    writing one would claim observations that were never made. The error text
    goes on the payload so the caller can see why, and nowhere else.
    """
    async with _session() as session:
        row = await session.get(RunbookRun, {"id": execution_id, "org_id": org_id})
        if row is None:
            return False
        row.status = StepStatus.FAILED.value
        row.completed_at = datetime.now(timezone.utc)
        payload = dict(row.execution or {})
        payload["status"] = StepStatus.FAILED.value
        payload["error"] = error
        row.execution = payload
        await session.commit()
        return True


async def load_execution(org_id: str, execution_id: str) -> RunbookExecution | None:
    """Load one execution, or None if this org has no such id."""
    async with _session() as session:
        row = await session.get(RunbookRun, {"id": execution_id, "org_id": org_id})
        return _deserialize_execution(row) if row is not None else None


async def list_executions(
    org_id: str,
    *,
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[RunbookExecution]:
    """List this org's executions, newest first."""
    stmt = select(RunbookRun).where(RunbookRun.org_id == org_id)
    if runbook_id is not None:
        stmt = stmt.where(RunbookRun.runbook_id == runbook_id)
    if status is not None:
        stmt = stmt.where(RunbookRun.status == status)
    stmt = stmt.order_by(RunbookRun.started_at.desc(), RunbookRun.id.desc()).limit(limit)

    async with _session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    out = []
    for row in rows:
        execution = _deserialize_execution(row)
        if execution is not None:
            out.append(execution)
    return out


async def delete_execution(org_id: str, execution_id: str) -> bool:
    """Delete one execution record. Returns whether a row was removed."""
    async with _session() as session:
        result = await session.execute(
            delete(RunbookRun).where(
                RunbookRun.id == execution_id,
                RunbookRun.org_id == org_id,
            )
        )
        await session.commit()
        return cast("CursorResult[Any]", result).rowcount > 0


def _deserialize_execution(row: RunbookRun) -> RunbookExecution | None:
    """Rebuild a RunbookExecution from its stored payload.

    `RunbookExecution` has a `to_dict` but no `from_dict`, so the inverse lives
    here. The lifted columns (status, timestamps) are authoritative over the
    payload's copies: `mark_failed` updates the column and the payload together,
    but a payload written by an older revision may disagree.
    """
    payload = row.execution or {}
    try:
        steps = [
            StepExecution(
                step_id=s["step_id"],
                step_name=s["step_name"],
                status=StepStatus(s["status"]),
                started_at=datetime.fromisoformat(s["started_at"]),
                completed_at=(
                    datetime.fromisoformat(s["completed_at"])
                    if s.get("completed_at")
                    else None
                ),
                output=s.get("output", ""),
                error=s.get("error", ""),
            )
            for s in payload.get("steps", [])
        ]
        started = _utc(row.started_at) or datetime.now(timezone.utc)
        return RunbookExecution(
            id=row.id,
            runbook_id=row.runbook_id,
            runbook_name=row.runbook_name,
            status=StepStatus(row.status),
            triggered_by=row.triggered_by,
            started_at=started,
            completed_at=_utc(row.completed_at),
            steps=steps,
            context=payload.get("context", {}),
        )
    except Exception:
        log.exception("Discarding unreadable execution %s for org %s", row.id, row.org_id)
        return None


async def clear_runbooks(org_id: str | None = None) -> None:
    """Delete runbook definitions — one org's, or every org's when org_id is None.

    For tests and for tearing down a tenant.
    """
    async with _session() as session:
        stmt = delete(RunbookDefinition)
        if org_id is not None:
            stmt = stmt.where(RunbookDefinition.org_id == org_id)
        await session.execute(stmt)
        await session.commit()


async def clear_executions(org_id: str | None = None) -> None:
    """Delete execution records — one org's, or every org's when org_id is None."""
    async with _session() as session:
        stmt = delete(RunbookRun)
        if org_id is not None:
            stmt = stmt.where(RunbookRun.org_id == org_id)
        await session.execute(stmt)
        await session.commit()
