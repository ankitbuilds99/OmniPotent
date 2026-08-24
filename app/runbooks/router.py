"""Runbook API Router.

REST API for runbook management and execution.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.runbooks import store
from app.runbooks.models import (
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepStatus,
    StepType,
)

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


def _execution(exec_: RunbookExecution) -> ExecutionResponse:
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


@router.get("")
async def list_runbooks(
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[RunbookResponse]:
    """List all runbooks with optional filtering."""
    runbooks = await store.list_runbooks(service=service, trigger=trigger, enabled=enabled)
    return [_summary(rb) for rb in runbooks]


# Declared before /{runbook_id}: FastAPI matches in registration order, so with
# the execution routes last every one of them was swallowed by the runbook-id
# route — `GET /runbooks/executions` answered 404 "Runbook not found".
@router.get("/executions")
async def list_executions(
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ExecutionResponse]:
    """List runbook executions."""
    executions = await store.list_executions(
        runbook_id=runbook_id, status=status, limit=limit
    )
    return [_execution(e) for e in executions]


@router.post("/executions/{execution_id}/approve")
async def approve_execution(execution_id: str) -> ExecutionResponse:
    """Approve a pending execution."""
    result = await store.set_execution_status(
        execution_id,
        StepStatus.RUNNING,
        expect={StepStatus.PENDING, StepStatus.WAITING_APPROVAL},
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if isinstance(result, str):  # the "conflict" sentinel; isinstance narrows, == does not
        raise HTTPException(status_code=400, detail="Execution is not pending approval")
    return _execution(result)


@router.post("/executions/{execution_id}/reject")
async def reject_execution(execution_id: str) -> ExecutionResponse:
    """Reject a pending execution."""
    result = await store.set_execution_status(execution_id, StepStatus.FAILED)
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _execution(result)


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str) -> RunbookDetailResponse:
    """Get a specific runbook by ID."""
    rb = await store.get_runbook(runbook_id)
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return _detail(rb)


@router.post("")
async def create_runbook(req: CreateRunbookRequest) -> RunbookDetailResponse:
    """Create a new runbook."""
    steps = [
        RunbookStep(
            id=step_data.get("id", f"step-{i}"),
            name=step_data.get("name", f"Step {i + 1}"),
            description=step_data.get("description", ""),
            step_type=StepType(step_data.get("step_type", "command")),
            action=step_data.get("action", ""),
            params=step_data.get("params", {}),
            timeout_seconds=step_data.get("timeout_seconds", 300),
            continue_on_failure=step_data.get("continue_on_failure", False),
            requires_approval=step_data.get("requires_approval", False),
        )
        for i, step_data in enumerate(req.steps)
    ]

    rb = Runbook(
        id=store.new_runbook_id(),
        name=req.name,
        description=req.description,
        service=req.service,
        trigger=req.trigger,
        execution_mode=ExecutionMode(req.execution_mode),
        steps=steps,
        labels=req.labels,
        enabled=True,
    )
    await store.create_runbook(rb)
    return _detail(rb)


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str) -> dict[str, str]:
    """Delete a runbook."""
    if not await store.delete_runbook(runbook_id):
        raise HTTPException(status_code=404, detail="Runbook not found")
    return {"status": "deleted"}


@router.post("/{runbook_id}/execute")
async def execute_runbook(
    runbook_id: str,
    req: ExecuteRunbookRequest,
    background_tasks: BackgroundTasks,
) -> ExecutionResponse:
    """Execute a runbook."""
    rb = await store.get_runbook(runbook_id)
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")

    if not rb.enabled:
        raise HTTPException(status_code=400, detail="Runbook is disabled")

    execution = await store.record_execution(
        rb,
        status=store.default_execution_status(rb.execution_mode),
        context={
            "service": req.service or "default",
            "namespace": req.namespace or "default",
            **req.params,
        },
    )
    return _execution(execution)
