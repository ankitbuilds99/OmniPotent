"""Runbook API Router.

REST API for runbook management and execution.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.runbooks.models import (
    Runbook,
    RunbookStep,
    RunbookExecution,
    ExecutionMode,
    StepStatus,
    StepType,
    DEFAULT_RUNBOOKS,
)
from app.tenancy.scoping import scoped

log = logging.getLogger("omnipotent.runbooks.api")

router = APIRouter(prefix="/runbooks", tags=["runbooks"])


class _RunbookState:
    """One org's runbooks and execution history (in memory; DB in production).

    Previously two module-level dicts shared by the whole process, which meant a
    runbook is a script one tenant can run against another tenant's
    infrastructure — anyone could edit or delete a competitor's remediation
    steps, and read every execution's output.
    """

    def __init__(self) -> None:
        self.runbooks: dict[str, Runbook] = {}
        self.executions: dict[str, RunbookExecution] = {}
        for rb_data in DEFAULT_RUNBOOKS:
            rb = Runbook.from_dict(rb_data)
            self.runbooks[rb.id] = rb


_state = scoped(_RunbookState)


def _runbooks() -> dict[str, Runbook]:
    return _state.get().runbooks


def _executions() -> dict[str, RunbookExecution]:
    return _state.get().executions


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


@router.get("")
async def list_runbooks(
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[RunbookResponse]:
    """List all runbooks with optional filtering."""
    result = []
    for rb in _runbooks().values():
        if service and rb.service != "*" and rb.service != service:
            continue
        if trigger and rb.trigger != trigger:
            continue
        if enabled is not None and rb.enabled != enabled:
            continue
        result.append(RunbookResponse(
            id=rb.id,
            name=rb.name,
            description=rb.description,
            service=rb.service,
            trigger=rb.trigger,
            execution_mode=rb.execution_mode.value,
            step_count=len(rb.steps),
            labels=rb.labels,
            enabled=rb.enabled,
        ))
    return result


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str) -> RunbookDetailResponse:
    """Get a specific runbook by ID."""
    rb = _runbooks().get(runbook_id)
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
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


@router.post("")
async def create_runbook(req: CreateRunbookRequest) -> RunbookDetailResponse:
    """Create a new runbook."""
    runbook_id = f"rb-{uuid.uuid4().hex[:8]}"

    steps = []
    for i, step_data in enumerate(req.steps):
        step = RunbookStep(
            id=step_data.get("id", f"step-{i}"),
            name=step_data.get("name", f"Step {i+1}"),
            description=step_data.get("description", ""),
            step_type=StepType(step_data.get("step_type", "command")),
            action=step_data.get("action", ""),
            params=step_data.get("params", {}),
            timeout_seconds=step_data.get("timeout_seconds", 300),
            continue_on_failure=step_data.get("continue_on_failure", False),
            requires_approval=step_data.get("requires_approval", False),
        )
        steps.append(step)

    rb = Runbook(
        id=runbook_id,
        name=req.name,
        description=req.description,
        service=req.service,
        trigger=req.trigger,
        execution_mode=ExecutionMode(req.execution_mode),
        steps=steps,
        labels=req.labels,
        enabled=True,
    )
    _runbooks()[runbook_id] = rb

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


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str) -> dict[str, str]:
    """Delete a runbook."""
    if runbook_id not in _runbooks():
        raise HTTPException(status_code=404, detail="Runbook not found")
    del _runbooks()[runbook_id]
    return {"status": "deleted"}


@router.post("/{runbook_id}/execute")
async def execute_runbook(
    runbook_id: str,
    req: ExecuteRunbookRequest,
    background_tasks: BackgroundTasks,
) -> ExecutionResponse:
    """Execute a runbook."""
    rb = _runbooks().get(runbook_id)
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")

    if not rb.enabled:
        raise HTTPException(status_code=400, detail="Runbook is disabled")

    execution_id = f"exec-{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()

    execution = RunbookExecution(
        id=execution_id,
        runbook_id=runbook_id,
        runbook_name=rb.name,
        status=StepStatus.PENDING if rb.execution_mode == ExecutionMode.REQUIRES_APPROVAL else StepStatus.RUNNING,
        triggered_by="manual",
        started_at=now,
        context={
            "service": req.service or "default",
            "namespace": req.namespace or "default",
            **req.params,
        },
    )
    _executions()[execution_id] = execution

    return ExecutionResponse(
        id=execution.id,
        runbook_id=execution.runbook_id,
        runbook_name=execution.runbook_name,
        status=execution.status.value,
        triggered_by=execution.triggered_by,
        started_at=execution.started_at.isoformat(),
        completed_at=execution.completed_at.isoformat() if execution.completed_at else None,
        steps=[s.to_dict() for s in execution.steps],
    )


@router.get("/executions")
async def list_executions(
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ExecutionResponse]:
    """List runbook executions."""
    result = []
    for exec_ in sorted(_executions().values(), key=lambda e: e.started_at, reverse=True):
        if runbook_id and exec_.runbook_id != runbook_id:
            continue
        if status and exec_.status.value != status:
            continue
        result.append(ExecutionResponse(
            id=exec_.id,
            runbook_id=exec_.runbook_id,
            runbook_name=exec_.runbook_name,
            status=exec_.status.value,
            triggered_by=exec_.triggered_by,
            started_at=exec_.started_at.isoformat(),
            completed_at=exec_.completed_at.isoformat() if exec_.completed_at else None,
            steps=[s.to_dict() for s in exec_.steps],
        ))
        if len(result) >= limit:
            break
    return result


@router.post("/executions/{execution_id}/approve")
async def approve_execution(execution_id: str) -> ExecutionResponse:
    """Approve a pending execution."""
    exec_ = _executions().get(execution_id)
    if not exec_:
        raise HTTPException(status_code=404, detail="Execution not found")

    if exec_.status != StepStatus.WAITING_APPROVAL and exec_.status != StepStatus.PENDING:
        raise HTTPException(status_code=400, detail="Execution is not pending approval")

    exec_.status = StepStatus.RUNNING

    return ExecutionResponse(
        id=exec_.id,
        runbook_id=exec_.runbook_id,
        runbook_name=exec_.runbook_name,
        status=exec_.status.value,
        triggered_by=exec_.triggered_by,
        started_at=exec_.started_at.isoformat(),
        completed_at=exec_.completed_at.isoformat() if exec_.completed_at else None,
        steps=[s.to_dict() for s in exec_.steps],
    )


@router.post("/executions/{execution_id}/reject")
async def reject_execution(execution_id: str) -> ExecutionResponse:
    """Reject a pending execution."""
    exec_ = _executions().get(execution_id)
    if not exec_:
        raise HTTPException(status_code=404, detail="Execution not found")

    exec_.status = StepStatus.FAILED
    exec_.completed_at = datetime.utcnow()

    return ExecutionResponse(
        id=exec_.id,
        runbook_id=exec_.runbook_id,
        runbook_name=exec_.runbook_name,
        status=exec_.status.value,
        triggered_by=exec_.triggered_by,
        started_at=exec_.started_at.isoformat(),
        completed_at=exec_.completed_at.isoformat() if exec_.completed_at else None,
        steps=[s.to_dict() for s in exec_.steps],
    )
