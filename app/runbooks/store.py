"""Durable runbook and execution storage.

Runbooks and their execution history were two module-level dicts. A runbook is a
script that runs against production infrastructure, so losing them on restart
lost the remediation itself, and the execution history — which is the audit
trail for "who ran what against which service" — was never written down at all.
The `runbook_executions` table existed from the first migration with no reader
and no writer.

Everything here is scoped to one org. The built-in runbooks are seeded per org on
first use and then owned by that org: deleting one makes it stay deleted, which
the shared-dict version could not express.

Two calling conventions reach these rows, and both are supported deliberately:

* the *context-scoped* one (`get_runbook`, `list_executions`, ...) resolves the
  owner from the request's tenant contextvar. The API router and the MCP server
  use it, because they always run inside a resolved tenant;
* the *org-parameterised* one (`load_runbook`, `save_execution`, ...) takes the
  org id as its first argument. The Dramatiq worker uses it, because there is no
  request to inherit a tenant from, and a background task can outlive the
  middleware that set the contextvar.

They are two doors onto the same table, not two stores — a runbook authored
through the API is the one the worker runs.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Literal, overload

from sqlalchemy import select

from app.config import get_settings
from app.database.orgs import (
    SINGLE_TENANT_ORG,
    ensure_org,
    ensure_org_row,
    org_uuid,
    org_uuid_for,
)
from app.runbooks.models import (
    DEFAULT_RUNBOOKS,
    ExecutionMode,
    Runbook,
    RunbookExecution,
    StepExecution,
    StepStatus,
)
from app.tenant_context import DEFAULT_STORAGE_ORG

log = logging.getLogger("omnipotent.runbooks.store")

# Marker on the org's settings JSON. Seeding on "this org has no runbooks yet"
# would resurrect the built-ins every time an org deleted the last one.
_SEEDED_KEY = "runbooks_seeded"

# Statuses that mean the run is over. A row with no `completed_at` is still in
# flight, and the listing says so.
_FINISHED = {StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED}

# Key on an execution's results payload naming the step a parked run is waiting
# on. It is what makes an approval refer to a *step* rather than to an offset:
# resuming by position alone would run whatever now occupies that index if the
# runbook was edited while the run sat at the gate.
_WAITING_KEY = "waiting_on"

# Execution ids are caller-supplied on the worker path (`exec-1a2b3c4d`) and
# server-minted on the API path (a uuid4). The column is a UUID, so anything that
# is not already one is hashed into a stable one and the original is kept on the
# payload — otherwise the id a caller was handed would not resolve when it polled
# with it.
_EXTERNAL_ID_NAMESPACE = uuid.UUID("2f9d2a3e-5a1c-5f4a-9b3e-1d6c2a7e4b88")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def _session() -> AsyncGenerator[Any, None]:
    """A session against a schema that is guaranteed to exist.

    Callers reach this outside the API lifespan (the worker, a script, a test),
    so the startup schema check cannot be assumed to have happened. Imports are
    deferred to keep importing this module cheap and free of database setup.
    """
    from app.database.engine import ensure_schema, get_session

    await ensure_schema()
    async with get_session() as session:
        yield session


def _org(org_id: str | None = None) -> uuid.UUID:
    """The org UUID rows are filed under, from an explicit id or the request.

    `storage_org_id()` hands out DEFAULT_STORAGE_ORG when no tenant is resolved
    while `tenant_id()` hands out "-" for the same situation; both mean "this
    single-tenant deployment", so they have to land on the same rows or the
    worker would write somewhere the API cannot read.

    That equivalence only holds where "default" cannot also be a tenant's own org
    id. In a multi-tenant deployment it can, and folding it onto
    SINGLE_TENANT_ORG would hand that tenant every unattributed row in the
    database and vice versa — so there the literal string is hashed like any
    other tenant id.
    """
    if org_id is None:
        return org_uuid()
    if org_id in ("", "-"):
        return SINGLE_TENANT_ORG
    if org_id == DEFAULT_STORAGE_ORG and not get_settings().copilot_multi_tenant:
        return SINGLE_TENANT_ORG
    return org_uuid_for(org_id)


def _execution_key(execution_id: str) -> uuid.UUID:
    """The primary key for an execution id, whatever form the caller minted it in."""
    try:
        return uuid.UUID(str(execution_id))
    except (AttributeError, TypeError, ValueError):
        return uuid.uuid5(_EXTERNAL_ID_NAMESPACE, str(execution_id))


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


def _safe_runbook(row: Any) -> Runbook | None:
    """`_to_runbook`, logging rather than raising on a payload we cannot read.

    One unreadable row must not take out the whole listing: an operator who
    cannot see their other runbooks during an incident is worse off than one
    missing the broken one.
    """
    try:
        return _to_runbook(row)
    except Exception:
        log.exception(
            "Discarding unreadable runbook %s for org %s", row.runbook_id, row.org_id
        )
        return None


def _to_execution(row: Any) -> RunbookExecution:
    results = row.results or {}
    return RunbookExecution(
        # The id the caller knows this run by, which is only the column when the
        # caller did not supply one of its own.
        id=str(results.get("external_id") or row.id),
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
            for s in results.get("steps", [])
        ],
        context=row.variables or {},
    )


def _safe_execution(row: Any) -> RunbookExecution | None:
    """`_to_execution`, logging rather than raising on an unreadable payload."""
    try:
        return _to_execution(row)
    except Exception:
        log.exception("Discarding unreadable execution %s for org %s", row.id, row.org_id)
        return None


async def _ensure_seeded(session: Any, org: uuid.UUID | None = None) -> uuid.UUID:
    """Give this org its own copy of the built-in runbooks, once.

    The marker on the org row is what makes a deleted built-in stay deleted:
    seeding on "this org has no runbooks" would resurrect the whole shipped set
    the moment an operator cleared the last one.
    """
    from app.database.models import Organization, Runbook as RunbookRow

    org = await ensure_org(session, org) if org is not None else await ensure_org_row(session)
    row = (
        await session.execute(select(Organization).where(Organization.id == org))
    ).scalar_one_or_none()
    if row is None or (row.settings or {}).get(_SEEDED_KEY):
        return org

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
    return org


async def ensure_builtins(org_id: str | None = None) -> None:
    """Seed one org's copy of DEFAULT_RUNBOOKS, once.

    The public entry point onto `_ensure_seeded` for callers that have no request
    to inherit a tenant from — the worker seeds before it looks a runbook up,
    because an org whose first ever runbook call comes from a scheduled job would
    otherwise find nothing.
    """
    async with _session() as session:
        await _ensure_seeded(session, _org(org_id))


async def list_runbooks(
    org_id: str | None = None,
    *,
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[Runbook]:
    from app.database.models import Runbook as RunbookRow

    org = _org(org_id)
    async with _session() as session:
        await _ensure_seeded(session, org)
        stmt = (
            select(RunbookRow)
            .where(RunbookRow.org_id == org)
            .order_by(RunbookRow.created_at)
        )
        if trigger:
            stmt = stmt.where(RunbookRow.trigger == trigger)
        if enabled is not None:
            stmt = stmt.where(RunbookRow.enabled == enabled)

        rows = (await session.execute(stmt)).scalars().all()

    # `service` is filtered here, not in SQL: "*" means "applies to every
    # service" and must match whatever the caller asked for.
    runbooks = [
        _safe_runbook(row)
        for row in rows
        if not service or row.service in ("*", service)
    ]
    return [rb for rb in runbooks if rb is not None]


async def get_runbook(runbook_id: str, org_id: str | None = None) -> Runbook | None:
    from app.database.models import Runbook as RunbookRow

    org = _org(org_id)
    async with _session() as session:
        await _ensure_seeded(session, org)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org,
                    RunbookRow.runbook_id == runbook_id,
                )
            )
        ).scalar_one_or_none()
        return _safe_runbook(row) if row else None


async def load_runbook(org_id: str, runbook_id: str) -> Runbook | None:
    """`get_runbook` for a caller that names the org explicitly.

    Used by the worker, where there is no request context to scope by.
    """
    return await get_runbook(runbook_id, org_id=org_id)


async def create_runbook(rb: Runbook, org_id: str | None = None) -> Runbook:
    from app.database.models import Runbook as RunbookRow

    async with _session() as session:
        org = (
            await ensure_org(session, _org(org_id))
            if org_id is not None
            else await ensure_org_row(session)
        )
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


async def update_runbook(rb: Runbook, org_id: str | None = None) -> Runbook | None:
    """Overwrite an existing runbook in this org. Returns None if it does not exist.

    The Runbook Builder's Save button had nowhere to send an edit — there was no
    update path at all — so every change to a saved runbook was discarded.
    """
    from app.database.models import Runbook as RunbookRow

    org = _org(org_id)
    async with _session() as session:
        await _ensure_seeded(session, org)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org,
                    RunbookRow.runbook_id == rb.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        _apply_runbook(row, rb)
        await session.commit()
    return rb


async def save_runbook(
    org_id: str, runbook: Runbook, *, is_builtin: bool = False
) -> None:
    """Insert or update one runbook definition, naming the org explicitly.

    `is_builtin` is accepted for callers that distinguish shipped runbooks from
    authored ones; this schema does not carry the flag, because which rows came
    from the seed is recorded on the org (see `_SEEDED_KEY`) rather than per row.
    """
    from app.database.models import Runbook as RunbookRow

    org = _org(org_id)
    async with _session() as session:
        await ensure_org(session, org)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org,
                    RunbookRow.runbook_id == runbook.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = RunbookRow(org_id=org, runbook_id=runbook.id)
            session.add(row)
        _apply_runbook(row, runbook)
        await session.commit()


def _apply_runbook(row: Any, rb: Runbook) -> None:
    """Copy a Runbook onto its row. JSON columns are reassigned, not mutated:
    SQLAlchemy only marks them dirty on assignment."""
    row.name = rb.name
    row.description = rb.description
    row.service = rb.service
    row.trigger = rb.trigger
    row.execution_mode = rb.execution_mode.value
    row.steps = [s.to_dict() for s in rb.steps]
    row.labels = rb.labels
    row.enabled = rb.enabled


async def delete_runbook(runbook_id: str, org_id: str | None = None) -> bool:
    from app.database.models import Runbook as RunbookRow

    org = _org(org_id)
    async with _session() as session:
        await _ensure_seeded(session, org)
        row = (
            await session.execute(
                select(RunbookRow).where(
                    RunbookRow.org_id == org,
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
    rb: Runbook,
    status: StepStatus,
    context: dict[str, Any],
    triggered_by: str = "manual",
    org_id: str | None = None,
) -> RunbookExecution:
    from app.database.models import RunbookExecution as ExecutionRow

    async with _session() as session:
        org = (
            await ensure_org(session, _org(org_id))
            if org_id is not None
            else await ensure_org_row(session)
        )
        results: dict[str, Any] = {"steps": []}
        # A run recorded straight into the approval gate is parked on its first
        # step; naming it here is what lets the resume guard tell "the human
        # approved this step" from "the human approved whatever is at index 0
        # now".
        if status == StepStatus.WAITING_APPROVAL and rb.steps:
            results[_WAITING_KEY] = rb.steps[0].id
        row = ExecutionRow(
            id=uuid.uuid4(),
            org_id=org,
            runbook_id=rb.id,
            runbook_name=rb.name,
            status=status.value,
            trigger=rb.trigger,
            triggered_by=triggered_by,
            variables=context,
            results=results,
            started_at=_now(),
        )
        session.add(row)
        await session.commit()
        return _to_execution(row)


async def get_execution(
    execution_id: str, org_id: str | None = None
) -> RunbookExecution | None:
    """Read one execution of this org, or None."""
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        return _safe_execution(row) if row else None


async def load_execution(org_id: str, execution_id: str) -> RunbookExecution | None:
    """`get_execution` for a caller that names the org explicitly."""
    return await get_execution(execution_id, org_id=org_id)


async def save_execution(org_id: str, execution: RunbookExecution) -> None:
    """Insert or update one execution record, whole.

    Called before the first step runs and again after every step, so the row
    exists for the entire window in which a caller polls it and advances while
    the run is still going. The complement of `append_step_result`, for callers
    that hold the whole record in memory rather than driving it from the row.
    """
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    key = _execution_key(execution.id)
    async with _session() as session:
        await ensure_org(session, org)
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == key, ExecutionRow.org_id == org
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = ExecutionRow(
                id=key,
                org_id=org,
                runbook_id=execution.runbook_id,
                runbook_name=execution.runbook_name,
                started_at=execution.started_at or _now(),
            )
            session.add(row)

        row.runbook_id = execution.runbook_id
        row.runbook_name = execution.runbook_name
        row.status = execution.status.value
        row.triggered_by = execution.triggered_by
        row.variables = dict(execution.context or {})
        results: dict[str, Any] = {"steps": [s.to_dict() for s in execution.steps]}
        if str(key) != str(execution.id):
            results["external_id"] = execution.id
        row.results = results
        row.completed_at = (
            execution.completed_at
            if execution.completed_at is not None
            else (_now() if execution.status in _FINISHED else None)
        )
        await session.commit()


async def mark_failed(org_id: str | None, execution_id: str, error: str) -> bool:
    """Flip an existing run to FAILED without inventing step results.

    Used when the executor raises: the exception carries no per-step detail, and
    writing one would claim observations that were never made. The error text
    goes on the payload so the caller can see why, and nowhere else — the steps
    that really did run keep the status they really had.
    """
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.status = StepStatus.FAILED.value
        row.completed_at = _now()
        results = {**(row.results or {}), "error": error}
        results.pop(_WAITING_KEY, None)
        row.results = results
        await session.commit()
        return True


async def append_step_result(
    execution_id: str, step: StepExecution, org_id: str | None = None
) -> RunbookExecution | None:
    """Append one finished step to an execution's durable results.

    Written as each step completes rather than once at the end: the row is the
    audit trail for what ran against production, so a crash or a restart
    mid-runbook has to leave behind the steps that really executed. It also
    doubles as the resume point — the next step to run is always
    `len(execution.steps)`.
    """
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        results = dict(row.results or {})
        # Rebound, not appended in place: SQLAlchemy does not track mutation
        # inside a plain JSON value, so `results["steps"].append(...)` would be
        # dropped on flush and every step result would be lost.
        row.results = {**results, "steps": [*results.get("steps", []), step.to_dict()]}
        await session.commit()
        return _safe_execution(row)


async def list_executions(
    org_id: str | None = None,
    *,
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[RunbookExecution]:
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        stmt = (
            select(ExecutionRow)
            .where(ExecutionRow.org_id == org)
            .order_by(ExecutionRow.started_at.desc())
        )
        if runbook_id:
            stmt = stmt.where(ExecutionRow.runbook_id == runbook_id)
        if status:
            stmt = stmt.where(ExecutionRow.status == status)

        rows = (await session.execute(stmt.limit(max(1, limit)))).scalars().all()
        executions = [_safe_execution(row) for row in rows]
    return [e for e in executions if e is not None]


async def delete_execution(execution_id: str, org_id: str | None = None) -> bool:
    """Delete one execution record. Returns whether a row was actually removed."""
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


@overload
async def set_execution_status(
    execution_id: str,
    status: StepStatus,
    *,
    waiting_on: str | None = ...,
    org_id: str | None = ...,
) -> RunbookExecution | None: ...


@overload
async def set_execution_status(
    execution_id: str,
    status: StepStatus,
    *,
    expect: set[StepStatus],
    waiting_on: str | None = ...,
    org_id: str | None = ...,
) -> RunbookExecution | None | Literal["conflict"]: ...


async def set_execution_status(
    execution_id: str,
    status: StepStatus,
    *,
    expect: set[StepStatus] | None = None,
    waiting_on: str | None = None,
    org_id: str | None = None,
) -> RunbookExecution | None | str:
    """Move an execution to `status`, optionally naming the step it parks on.

    Returns the updated execution, None if this org has no such execution, or
    the string "conflict" if `expect` is given and the current status is not in
    it — which is how the router tells 404 apart from 400.

    `waiting_on` survives the move to RUNNING that an approval makes, because it
    is the resuming run that has to check it; it is cleared only once the run
    reaches a terminal state, so a finished record never claims to be parked.

    Overloaded because "conflict" is only reachable when `expect` is passed;
    callers that omit it would otherwise have to handle a case that cannot
    happen, and the router does exactly that in one place and not the other.
    """
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if expect is not None and StepStatus(row.status) not in expect:
            return "conflict"

        row.status = status.value
        if status in _FINISHED:
            row.completed_at = _now()
        if waiting_on is not None or status in _FINISHED:
            results = dict(row.results or {})
            if waiting_on is not None:
                results[_WAITING_KEY] = waiting_on
            else:
                results.pop(_WAITING_KEY, None)
            row.results = results
        await session.commit()
        return _to_execution(row)


async def execution_notes(
    execution_id: str, org_id: str | None = None
) -> dict[str, str]:
    """What the results payload records about a run beyond its steps.

    Two keys, both of which a `RunbookExecution` has nowhere to carry because it
    models steps that *ran*: `waiting_on`, the step at an approval gate, which
    has not run; and `error`, why a run died, which `mark_failed` writes
    precisely because there was no step result to attribute it to. Read
    separately so that a failure an operator has to act on is not confined to
    the server log.
    """
    from app.database.models import RunbookExecution as ExecutionRow

    org = _org(org_id)
    async with _session() as session:
        row = (
            await session.execute(
                select(ExecutionRow).where(
                    ExecutionRow.id == _execution_key(execution_id),
                    ExecutionRow.org_id == org,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {}
        results = row.results or {}
        return {
            key: str(results[key])
            for key in (_WAITING_KEY, "error")
            if results.get(key)
        }


async def clear_runbooks(org_id: str | None = None) -> None:
    """Delete runbook definitions — one org's, or every org's when org_id is None.

    For tests and for tearing down a tenant. Note the asymmetry with the rest of
    this module: `None` here means *all orgs*, not "the current one", because a
    teardown helper that silently scoped itself to the ambient tenant would leave
    the rows it was called to remove.
    """
    from sqlalchemy import delete

    from app.database.models import Runbook as RunbookRow

    async with _session() as session:
        stmt = delete(RunbookRow)
        if org_id is not None:
            stmt = stmt.where(RunbookRow.org_id == _org(org_id))
        await session.execute(stmt)
        await session.commit()


async def clear_executions(org_id: str | None = None) -> None:
    """Delete execution records — one org's, or every org's when org_id is None."""
    from sqlalchemy import delete

    from app.database.models import RunbookExecution as ExecutionRow

    async with _session() as session:
        stmt = delete(ExecutionRow)
        if org_id is not None:
            stmt = stmt.where(ExecutionRow.org_id == _org(org_id))
        await session.execute(stmt)
        await session.commit()


def new_runbook_id() -> str:
    return f"rb-{uuid.uuid4().hex[:8]}"


def default_execution_status(mode: ExecutionMode) -> StepStatus:
    """The status a freshly recorded run starts in.

    MERGE NOTE: a REQUIRES_APPROVAL run used to be recorded PENDING here, and
    origin/main instead recorded every run PENDING and scheduled it, letting the
    executor walk to the first step flagged `requires_approval` and park there.
    Neither is kept whole. Scheduling was origin/main's way of naming the step a
    human is blocking on, but it also ran every step *before* that flag without
    any sign-off — on a runbook whose mode says the run needs approval, and
    whose steps mostly do not repeat the flag, that is the whole gate. So
    nothing is scheduled (this branch's behaviour) and the status is
    WAITING_APPROVAL rather than PENDING, which is origin/main's point: mid
    incident "pending" reads as "queued, it will start", while
    "waiting_approval" says a human is the blocker — and the record names the
    step (see `_WAITING_KEY`).
    """
    return (
        StepStatus.WAITING_APPROVAL
        if mode == ExecutionMode.REQUIRES_APPROVAL
        else StepStatus.RUNNING
    )
