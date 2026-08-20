"""Runbook API Router.

REST API for runbook management and execution.

Three things about this module changed together, because fixing any one of them
alone would have left the API still lying about something:

*Storage.* Definitions and executions lived in two module-level dicts, seeded at
import from DEFAULT_RUNBOOKS. A runbook an operator authored was visible only to
the replica that served the POST and gone at the next deploy. Both now live in
Postgres, scoped per tenant (see `app/runbooks/store.py`).

*Execution.* `POST /{id}/execute` took a `BackgroundTasks` argument and never
scheduled anything on it. It wrote a record with status "running" that nothing
would ever advance, and `approve` flipped a status field to "running" with the
same effect — no step of any runbook was ever run through this API. Both now go
through `RunbookExecutor`, which is fail-closed: a step whose backend is not
configured reports failure rather than claiming an action it did not perform.

*Routing.* `GET /executions` was declared after `GET /{runbook_id}`, so FastAPI
matched it as a runbook id and it answered 404 "Runbook not found" — the
execution listing had never been reachable. Literal paths now precede the
parameterised one, and a test pins the order.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from app.runbooks import store
from app.runbooks.executor import RunbookExecutor
from app.runbooks.models import (
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepStatus,
    StepType,
)
from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.runbooks.api")

router = APIRouter(prefix="/runbooks", tags=["runbooks"])


class RunbookResponse(BaseModel):
    id: str
    name: str
    description: str
    service: str
    trigger: str
    execution_mode: str
    step_count: int
    labels: dict[str, str]
    enabled: bool


class RunbookDetailResponse(BaseModel):
    id: str
    name: str
    description: str
    service: str
    trigger: str
    execution_mode: str
    steps: list[dict[str, Any]]
    labels: dict[str, str]
    enabled: bool


class CreateRunbookRequest(BaseModel):
    name: str
    description: str
    service: str = "*"
    trigger: str = "manual"
    execution_mode: str = "requires_approval"
    steps: list[dict[str, Any]] = []
    labels: dict[str, str] = {}


class ExecuteRunbookRequest(BaseModel):
    service: str | None = None
    namespace: str | None = None
    params: dict[str, Any] = {}


class ExecutionResponse(BaseModel):
    id: str
    runbook_id: str
    runbook_name: str
    status: str
    triggered_by: str
    started_at: str
    completed_at: str | None
    steps: list[dict[str, Any]]


def _detail(rb: Runbook) -> RunbookDetailResponse:
    return RunbookDetailResponse(
        id=rb.id,
        name=rb.name,
        description=rb.description,
        service=rb.service,
        trigger=rb.trigger,
        execution_mode=rb.execution_mode.value,
        steps=[s.to_dict() for s in rb.steps],
        labels=rb.labels,
        enabled=rb.enabled,
    )


def _summary(rb: Runbook) -> RunbookResponse:
    return RunbookResponse(
        id=rb.id,
        name=rb.name,
        description=rb.description,
        service=rb.service,
        trigger=rb.trigger,
        execution_mode=rb.execution_mode.value,
        step_count=len(rb.steps),
        labels=rb.labels,
        enabled=rb.enabled,
    )


def _execution_response(execution: RunbookExecution) -> ExecutionResponse:
    return ExecutionResponse(
        id=execution.id,
        runbook_id=execution.runbook_id,
        runbook_name=execution.runbook_name,
        status=execution.status.value,
        triggered_by=execution.triggered_by,
        started_at=execution.started_at.isoformat(),
        completed_at=(
            execution.completed_at.isoformat() if execution.completed_at else None
        ),
        steps=[s.to_dict() for s in execution.steps],
    )


async def _require_runbook(org_id: str, runbook_id: str) -> Runbook:
    """Load a runbook or 404. Scoped: another tenant's id is simply not found."""
    await store.ensure_builtins(org_id)
    rb = await store.load_runbook(org_id, runbook_id)
    if rb is None:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return rb


async def _require_execution(org_id: str, execution_id: str) -> RunbookExecution:
    execution = await store.load_execution(org_id, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution


async def _run_and_record(
    org_id: str,
    runbook: Runbook,
    execution_id: str,
    context: dict[str, Any],
) -> None:
    """Run a runbook to completion, persisting after every step.

    A fresh executor per run, seeded with just this runbook: the module singleton
    keeps its definitions in a process-wide dict, and two tenants that both have
    a runbook called `rb-restart` would otherwise run whichever one was written
    last. Progress is written per step so a caller polling mid-run sees where it
    has got to, not just a terminal result.
    """

    async def persist(execution: RunbookExecution) -> None:
        await store.save_execution(org_id, execution)

    executor = RunbookExecutor()
    try:
        await executor.execute(
            runbook.id,
            context=context,
            runbook=runbook,
            execution_id=execution_id,
            on_progress=persist,
        )
    except Exception as exc:
        log.exception("Runbook execution %s failed", execution_id)
        await store.mark_failed(org_id, execution_id, str(exc))


async def _resume_and_record(
    org_id: str,
    runbook: Runbook,
    execution: RunbookExecution,
    from_step_id: str,
) -> None:
    """Continue an approved execution, persisting after every step."""

    async def persist(current: RunbookExecution) -> None:
        await store.save_execution(org_id, current)

    executor = RunbookExecutor()
    try:
        result = await executor.resume(
            execution, runbook, from_step_id, on_progress=persist
        )
        if result is None:
            await store.mark_failed(
                org_id,
                execution.id,
                f"cannot resume: step {from_step_id!r} is no longer part of "
                f"runbook {runbook.id!r}",
            )
    except Exception as exc:
        log.exception("Runbook execution %s failed on resume", execution.id)
        await store.mark_failed(org_id, execution.id, str(exc))


# ---------------------------------------------------------------------------
# Executions.
#
# Declared before `/{runbook_id}`: FastAPI matches routes in declaration order,
# so with the parameterised route first every request to `/runbooks/executions`
# was answered as a lookup of a runbook whose id happened to be "executions".
# ---------------------------------------------------------------------------


@router.get("/executions")
async def list_executions(
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ExecutionResponse]:
    """List runbook executions, newest first."""
    executions = await store.list_executions(
        storage_org_id(), runbook_id=runbook_id, status=status, limit=limit
    )
    return [_execution_response(e) for e in executions]


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str) -> ExecutionResponse:
    """Get one execution, including the result of every step that has run."""
    execution = await _require_execution(storage_org_id(), execution_id)
    return _execution_response(execution)


@router.post("/executions/{execution_id}/approve")
async def approve_execution(
    execution_id: str,
    background_tasks: BackgroundTasks,
) -> ExecutionResponse:
    """Approve a paused execution and continue running it.

    Resumption is scheduled rather than performed inline: the remaining steps can
    include waits and rollouts, and the approving request should not be held open
    for them. Poll `GET /executions/{id}` for the outcome.
    """
    org_id = storage_org_id()
    execution = await _require_execution(org_id, execution_id)

    if execution.status not in (StepStatus.WAITING_APPROVAL, StepStatus.PENDING):
        raise HTTPException(
            status_code=400, detail="Execution is not pending approval"
        )

    runbook = await store.load_runbook(org_id, execution.runbook_id)
    if runbook is None:
        raise HTTPException(
            status_code=409,
            detail="Runbook for this execution no longer exists",
        )

    # Resume at the step that asked for approval, or at the first step if the
    # run paused before recording one.
    paused_at = next(
        (s.step_id for s in execution.steps if s.status == StepStatus.WAITING_APPROVAL),
        None,
    )
    if paused_at is None:
        if not runbook.steps:
            raise HTTPException(status_code=409, detail="Runbook has no steps")
        paused_at = runbook.steps[0].id

    execution.status = StepStatus.RUNNING
    await store.save_execution(org_id, execution)
    background_tasks.add_task(
        _resume_and_record, org_id, runbook, execution, paused_at
    )

    return _execution_response(execution)


@router.post("/executions/{execution_id}/reject")
async def reject_execution(execution_id: str) -> ExecutionResponse:
    """Reject a pending execution.

    Recorded as SKIPPED, not FAILED: nothing was attempted and failed, a human
    declined it. The distinction matters when someone later asks how often this
    runbook goes wrong.
    """
    org_id = storage_org_id()
    execution = await _require_execution(org_id, execution_id)

    if execution.status not in (StepStatus.WAITING_APPROVAL, StepStatus.PENDING):
        raise HTTPException(
            status_code=400, detail="Execution is not pending approval"
        )

    execution.status = StepStatus.SKIPPED
    await store.save_execution(org_id, execution)
    return _execution_response(execution)


@router.delete("/executions/{execution_id}")
async def delete_execution(execution_id: str) -> dict[str, Any]:
    """Delete an execution record."""
    deleted = await store.delete_execution(storage_org_id(), execution_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"status": "deleted", "deleted": True}


# ---------------------------------------------------------------------------
# Runbook definitions
# ---------------------------------------------------------------------------


@router.get("")
async def list_runbooks(
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[RunbookResponse]:
    """List all runbooks with optional filtering."""
    org_id = storage_org_id()
    await store.ensure_builtins(org_id)
    runbooks = await store.list_runbooks(
        org_id, service=service, trigger=trigger, enabled=enabled
    )
    return [_summary(rb) for rb in runbooks]


@router.post("")
async def create_runbook(req: CreateRunbookRequest) -> RunbookDetailResponse:
    """Create a new runbook."""
    try:
        execution_mode = ExecutionMode(req.execution_mode)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown execution_mode {req.execution_mode!r}; expected one of "
                f"{sorted(m.value for m in ExecutionMode)}"
            ),
        )

    steps = []
    for i, step_data in enumerate(req.steps):
        raw_type = step_data.get("step_type", "command")
        try:
            step_type = StepType(raw_type)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Step {i}: unknown step_type {raw_type!r}; expected one of "
                    f"{sorted(t.value for t in StepType)}"
                ),
            )
        steps.append(
            RunbookStep(
                id=step_data.get("id", f"step-{i}"),
                name=step_data.get("name", f"Step {i + 1}"),
                description=step_data.get("description", ""),
                step_type=step_type,
                action=step_data.get("action", ""),
                params=step_data.get("params", {}),
                timeout_seconds=step_data.get("timeout_seconds", 300),
                continue_on_failure=step_data.get("continue_on_failure", False),
                requires_approval=step_data.get("requires_approval", False),
            )
        )

    rb = Runbook(
        id=f"rb-{uuid.uuid4().hex[:8]}",
        name=req.name,
        description=req.description,
        service=req.service,
        trigger=req.trigger,
        execution_mode=execution_mode,
        steps=steps,
        labels=req.labels,
        enabled=True,
    )
    await store.save_runbook(storage_org_id(), rb)
    return _detail(rb)


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str) -> RunbookDetailResponse:
    """Get a specific runbook by ID."""
    return _detail(await _require_runbook(storage_org_id(), runbook_id))


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str) -> dict[str, Any]:
    """Delete a runbook."""
    org_id = storage_org_id()
    await store.ensure_builtins(org_id)
    deleted = await store.delete_runbook(org_id, runbook_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return {"status": "deleted", "deleted": True}


@router.post("/{runbook_id}/execute")
async def execute_runbook(
    runbook_id: str,
    req: ExecuteRunbookRequest,
    background_tasks: BackgroundTasks,
) -> ExecutionResponse:
    """Execute a runbook.

    Returns as soon as the run is recorded and scheduled; poll
    `GET /runbooks/executions/{id}` for step results. A runbook in
    REQUIRES_APPROVAL mode stops at its first approval gate and waits there.
    """
    org_id = storage_org_id()
    rb = await _require_runbook(org_id, runbook_id)

    if not rb.enabled:
        raise HTTPException(status_code=400, detail="Runbook is disabled")

    execution_id = f"exec-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "service": req.service or "default",
        "namespace": req.namespace or "default",
        **req.params,
    }

    # Recorded before the run is scheduled, so the id this response returns
    # resolves on the very next request rather than 404ing until the run ends.
    execution = RunbookExecution(
        id=execution_id,
        runbook_id=rb.id,
        runbook_name=rb.name,
        status=StepStatus.PENDING,
        triggered_by="manual",
        started_at=datetime.now(timezone.utc),
        context=context,
    )
    await store.save_execution(org_id, execution)

    # org_id is resolved above and captured here: a BackgroundTask can run after
    # the middleware has reset the tenant contextvar.
    background_tasks.add_task(_run_and_record, org_id, rb, execution_id, context)

    return _execution_response(execution)
