"""Database-backed storage for coordinator DAG executions.

Replaces `app/coordinator/api_router.py:_active_executions`, a module-level dict.
That dict had two defects beyond being per-pod:

  * It was assigned only *after* `executor.execute()` returned, so
    `GET /coordinator/executions/{id}` answered 404 for the entire duration of
    the run — exactly the window in which a caller polls it — and started
    answering once the answer no longer mattered. Here a row is written when the
    run starts and updated when it finishes.
  * It was global, not per-tenant. The primary key is (id, org_id) so a lookup
    that omits the org cannot compile.

The whole `DAGExecution` goes into one JSON column; `dag_id`, `state` and
`current_level` are lifted out beside it because the list endpoint reads exactly
those three and should not deserialize every payload to do it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.coordinator.dag_executor import DAGExecution, ExecutionState
from app.database.engine import ensure_schema, get_session
from app.database.models import CoordinatorExecution

log = logging.getLogger("omnipotent.coordinator.store")

# Terminal states, i.e. the ones that set completed_at.
_FINISHED = {
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.CANCELLED,
    ExecutionState.SKIPPED,
}


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session against the configured database, schema guaranteed to exist."""
    await ensure_schema()
    async with get_session() as session:
        yield session


async def save_execution(org_id: str, execution: DAGExecution) -> None:
    """Insert or update one execution's stored state.

    Called both when a run starts and when it ends, so a poll during the run
    gets the real state instead of a 404.
    """
    payload = execution.serialize()
    finished = execution.state in _FINISHED
    completed_at = execution.completed_at
    if finished and completed_at is None:
        completed_at = datetime.now(timezone.utc)

    async with _session() as session:
        row = await session.get(
            CoordinatorExecution, {"id": execution.id, "org_id": org_id}
        )
        if row is None:
            session.add(
                CoordinatorExecution(
                    id=execution.id,
                    org_id=org_id,
                    dag_id=execution.dag_id,
                    state=execution.state.value,
                    current_level=execution.current_level,
                    execution=payload,
                    completed_at=completed_at,
                )
            )
        else:
            # Reassigned, not mutated in place: SQLAlchemy only marks a JSON
            # column dirty on assignment.
            # Approval decisions are control-plane state and must survive a
            # concurrent worker checkpoint write.
            prior_decisions = (row.execution or {}).get("approval_decisions", {})
            if prior_decisions:
                payload["approval_decisions"] = prior_decisions
            row.execution = payload
            row.dag_id = execution.dag_id
            row.state = execution.state.value
            row.current_level = execution.current_level
            row.completed_at = completed_at
        await session.commit()


async def record_approval_decision(
    org_id: str,
    execution_id: str,
    task_id: str,
    approved: bool,
    reason: str = "",
) -> bool:
    """Persist one idempotent approval decision for any worker to observe."""
    async with _session() as session:
        row = await session.get(
            CoordinatorExecution, {"id": execution_id, "org_id": org_id}
        )
        if row is None:
            return False
        payload = dict(row.execution or {})
        task_state = (payload.get("task_executions") or {}).get(task_id, {}).get("state")
        if row.state != "running" or task_state != "running":
            return False
        decisions = dict(payload.get("approval_decisions") or {})
        key = task_id
        if key in decisions:
            return True
        decisions[key] = {"approved": bool(approved), "reason": reason}
        payload["approval_decisions"] = decisions
        row.execution = payload
        await session.commit()
        return True


async def get_approval_decision(
    org_id: str, execution_id: str, task_id: str
) -> bool | None:
    """Read a durable approval decision, returning None while undecided."""
    async with _session() as session:
        row = await session.get(
            CoordinatorExecution, {"id": execution_id, "org_id": org_id}
        )
        if row is None:
            return None
        decision = (row.execution or {}).get("approval_decisions", {}).get(task_id)
        if decision is None:
            return None
        return bool(decision.get("approved", False))


async def request_cancel(org_id: str, execution_id: str) -> bool:
    """Persist a cancellation request for a worker running on any replica."""
    async with _session() as session:
        row = await session.get(CoordinatorExecution, {"id": execution_id, "org_id": org_id})
        if row is None or row.state not in {"pending", "running"}:
            return False
        payload = dict(row.execution or {})
        payload["cancel_requested"] = True
        row.execution = payload
        await session.commit()
        return True


async def is_cancel_requested(org_id: str, execution_id: str) -> bool:
    """Return whether a durable cancellation request has been recorded."""
    async with _session() as session:
        row = await session.get(CoordinatorExecution, {"id": execution_id, "org_id": org_id})
        return bool(row and (row.execution or {}).get("cancel_requested"))


async def mark_failed(
    org_id: str, execution_id: str, error: str | None = None
) -> bool:
    """Flip an existing row to FAILED, optionally recording why.

    Used when `DAGExecutor.execute()` raises: it re-raises after marking its own
    execution object failed, and that object is created inside `execute()` and
    never returned, so the caller has no per-task detail to write. Only the
    outcome and the reason are written — no task results are invented for work
    nobody observed.

    The payload's own `state`/`error`/`completed_at` are rewritten alongside the
    lifted columns because `load_execution` reads the payload: leaving it at
    RUNNING made `GET /executions/{id}` keep reporting a dead run as in flight
    while `GET /executions` already listed it as failed.
    """
    async with _session() as session:
        row = await session.get(
            CoordinatorExecution, {"id": execution_id, "org_id": org_id}
        )
        if row is None:
            return False
        completed_at = datetime.now(timezone.utc)
        row.state = ExecutionState.FAILED.value
        row.completed_at = completed_at
        # Reassigned, not mutated in place: SQLAlchemy only marks a JSON column
        # dirty on assignment.
        payload = dict(row.execution or {})
        payload["state"] = ExecutionState.FAILED.value
        payload["completed_at"] = completed_at.isoformat()
        if error is not None:
            payload["error"] = error
        row.execution = payload
        await session.commit()
        return True


async def load_execution(org_id: str, execution_id: str) -> DAGExecution | None:
    """Load one execution, or None if this org has none by that id."""
    async with _session() as session:
        row = await session.get(
            CoordinatorExecution, {"id": execution_id, "org_id": org_id}
        )
        if row is None:
            return None
        try:
            return DAGExecution.deserialize(row.execution)
        except Exception:  # noqa: BLE001 — a bad payload is a 404, not a 500
            log.exception(
                "could not deserialize coordinator execution %s (org=%s)",
                row.id,
                row.org_id,
            )
            return None


async def list_executions(org_id: str, limit: int = 100) -> list[dict[str, object]]:
    """Summaries of this org's executions, most recent first.

    Returns the lifted columns rather than whole `DAGExecution` objects: the
    listing endpoint shows id/dag/state/level, and deserializing every payload
    to produce four fields would make listing cost proportional to the size of
    every DAG ever run.
    """
    async with _session() as session:
        rows = (
            await session.execute(
                select(
                    CoordinatorExecution.id,
                    CoordinatorExecution.dag_id,
                    CoordinatorExecution.state,
                    CoordinatorExecution.current_level,
                    CoordinatorExecution.started_at,
                    CoordinatorExecution.completed_at,
                )
                .where(CoordinatorExecution.org_id == org_id)
                .order_by(CoordinatorExecution.started_at.desc())
                .limit(limit)
            )
        ).all()

    return [
        {
            "id": row.id,
            "dag_id": row.dag_id,
            "state": row.state,
            "current_level": row.current_level,
            "started_at": _iso(row.started_at),
            "completed_at": _iso(row.completed_at),
        }
        for row in rows
    ]


async def delete_execution(org_id: str, execution_id: str) -> bool:
    """Delete one execution. Returns whether a row was actually removed."""
    async with _session() as session:
        # A DELETE always yields a CursorResult; the declared Result type has no
        # rowcount, so the cast is what lets the caller be told whether the id
        # existed instead of always claiming success.
        result = cast(
            CursorResult[Any],
            await session.execute(
                delete(CoordinatorExecution).where(
                    CoordinatorExecution.id == execution_id,
                    CoordinatorExecution.org_id == org_id,
                )
            ),
        )
        await session.commit()
        return bool(result.rowcount)


async def clear_executions(org_id: str | None = None) -> None:
    """Delete stored executions. Test/support helper, deliberately explicit."""
    async with _session() as session:
        stmt = delete(CoordinatorExecution)
        if org_id is not None:
            stmt = stmt.where(CoordinatorExecution.org_id == org_id)
        await session.execute(stmt)
        await session.commit()


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 with UTC re-attached — SQLite has no timezone type, so a value
    round-tripped through it comes back naive and would serialize without the
    offset, reading as local time to any client that parses it."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
