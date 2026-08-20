"""API Router for DAG Coordinator.

Exposes REST API for task graph management and execution.

Executions live in Postgres (`app/coordinator/store.py`), not in a module-level
dict. The dict this replaced was written only after `executor.execute()`
returned, so `GET /executions/{id}` answered 404 for the whole run — the exact
window a caller polls — and only started answering once the answer was stale.
Here a row is written before the DAG starts and updated when it ends, so a poll
mid-run sees the real state.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.coordinator import store
from app.coordinator.dag_executor import (
    DAGExecution,
    DAGExecutor,
    ExecutionState,
    TaskDAG,
    TaskNode,
    TaskPriority,
    create_investigation_dag,
)
from app.coordinator.handlers import (
    create_investigation_handlers,
    create_remediation_handlers,
)
from app.coordinator.replanner import DynamicReplanner
from app.coordinator.resource_pool import ResourcePool
from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.coordinator.api")

router = APIRouter(prefix="/coordinator", tags=["coordinator"])

_executor: DAGExecutor | None = None
_resource_pool: ResourcePool | None = None
_replanner: DynamicReplanner | None = None


async def _run_and_record(
    executor: DAGExecutor,
    dag: TaskDAG,
    org_id: str,
    execution_id: str,
    context: dict[str, Any] | None = None,
    resume_from: str | None = None,
) -> None:
    """Run a DAG, recording its state before it starts and after it ends.

    The pre-run row is what makes polling work. The `except` is not swallowing a
    bug: `execute()` re-raises after marking the execution FAILED, and this is a
    background task with no caller to receive the exception, so the failure has
    to be written down here or it is lost entirely.
    """
    await store.save_execution(
        org_id,
        DAGExecution(
            id=execution_id,
            dag_id=dag.id,
            state=ExecutionState.RUNNING,
            initial_context=dict(context or {}),
            execution_levels=dag.get_execution_levels(),
        ),
    )

    try:
        execution = await executor.execute(
            dag,
            context=context,
            execution_id=execution_id,
            resume_from=resume_from,
        )
    except Exception:
        log.exception("DAG execution %s failed", execution_id)
        await store.mark_failed(org_id, execution_id)
        return

    await store.save_execution(org_id, execution)


async def get_executor() -> DAGExecutor:
    """Get or create DAG executor."""
    global _executor, _resource_pool, _replanner

    if _executor is None:
        settings = get_settings()

        _resource_pool = ResourcePool(
            cpu_cores=8,
            memory_mb=16384,
            gpus=0,
        )

        _executor = DAGExecutor(
            handlers={},
            redis_client=None,
            max_parallel=10,
            resource_pool=_resource_pool,
        )

        investigation_handlers = create_investigation_handlers(
            prometheus_url=getattr(settings, "prometheus_url", None),
            loki_url=getattr(settings, "loki_url", None),
            knowledge_graph=None,
            llm_client=None,
        )

        remediation_handlers = create_remediation_handlers(
            k8s_client=None,
            prometheus_url=getattr(settings, "prometheus_url", None),
            knowledge_graph=None,
            llm_client=None,
        )

        for name, handler in investigation_handlers.items():
            _executor.register_handler(name, handler)

        for name, handler in remediation_handlers.items():
            _executor.register_handler(name, handler)

        _replanner = DynamicReplanner(
            llm_client=None,
            knowledge_graph=None,
        )

    return _executor


class TaskNodeInput(BaseModel):
    """Input for creating a task node."""
    id: str
    name: str
    handler: str
    depends_on: list[str] = Field(default_factory=list)
    input_params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300
    priority: str = "NORMAL"
    requires_approval: bool = False
    description: str = ""


class DAGInput(BaseModel):
    """Input for creating a DAG."""
    id: str
    name: str
    tasks: list[TaskNodeInput]


class ExecuteDAGInput(BaseModel):
    """Input for executing a DAG."""
    context: dict[str, Any] = Field(default_factory=dict)
    resume_from: str | None = None


class InvestigationInput(BaseModel):
    """Input for starting an investigation."""
    incident_id: str
    affected_services: list[str]
    severity: str = "P2"
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalInput(BaseModel):
    """Input for approving a task."""
    approved: bool = True
    reason: str = ""


@router.post("/dags")
async def create_dag(
    dag_input: DAGInput,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Create a new DAG."""
    dag = TaskDAG(dag_input.id, dag_input.name)

    for task_input in dag_input.tasks:
        priority = TaskPriority[task_input.priority.upper()]

        task = TaskNode(
            id=task_input.id,
            name=task_input.name,
            handler=task_input.handler,
            depends_on=task_input.depends_on,
            input_params=task_input.input_params,
            timeout_seconds=task_input.timeout_seconds,
            priority=priority,
            requires_approval=task_input.requires_approval,
            description=task_input.description,
        )
        dag.add_task(task)

    errors = dag.validate()
    if errors:
        raise HTTPException(status_code=400, detail=f"Invalid DAG: {errors}")

    return {
        "dag": dag.to_dict(),
        "execution_levels": dag.get_execution_levels(),
        "critical_path": dag.get_critical_path(),
    }


@router.post("/dags/{dag_id}/execute")
async def execute_dag(
    dag_id: str,
    dag_input: DAGInput,
    execute_input: ExecuteDAGInput,
    background_tasks: BackgroundTasks,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Execute a DAG."""
    dag = TaskDAG(dag_input.id, dag_input.name)

    for task_input in dag_input.tasks:
        priority = TaskPriority[task_input.priority.upper()]

        task = TaskNode(
            id=task_input.id,
            name=task_input.name,
            handler=task_input.handler,
            depends_on=task_input.depends_on,
            input_params=task_input.input_params,
            timeout_seconds=task_input.timeout_seconds,
            priority=priority,
            requires_approval=task_input.requires_approval,
            description=task_input.description,
        )
        dag.add_task(task)

    errors = dag.validate()
    if errors:
        raise HTTPException(status_code=400, detail=f"Invalid DAG: {errors}")

    # Resolved here, not inside the background task: a BackgroundTask can run
    # after the middleware has reset the tenant contextvar, which would file the
    # execution under the wrong org.
    org_id = storage_org_id()
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"

    background_tasks.add_task(
        _run_and_record,
        executor,
        dag,
        org_id,
        execution_id,
        execute_input.context,
        execute_input.resume_from,
    )

    return {
        "status": "started",
        "dag_id": dag_id,
        # Returned so the caller can poll /executions/{id}. The previous version
        # withheld it, leaving the listing endpoint as the only way to find a run.
        "execution_id": execution_id,
        "message": "DAG execution started in background",
    }


@router.post("/investigations")
async def start_investigation(
    input: InvestigationInput,
    background_tasks: BackgroundTasks,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Start an incident investigation workflow."""
    dag = create_investigation_dag(
        incident_id=input.incident_id,
        affected_services=input.affected_services,
        severity=input.severity,
    )

    org_id = storage_org_id()
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"

    background_tasks.add_task(
        _run_and_record, executor, dag, org_id, execution_id, input.context
    )

    return {
        "status": "started",
        "dag_id": dag.id,
        "dag_name": dag.name,
        "execution_id": execution_id,
        "tasks": len(dag.tasks),
        "execution_levels": dag.get_execution_levels(),
    }


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
) -> dict[str, Any]:
    """Get execution status."""
    # Scoped by org: execution ids are short random hex, and an unscoped lookup
    # would let one tenant read another's DAG results by guessing or replaying one.
    execution = await store.load_execution(storage_org_id(), execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution.to_dict()


@router.delete("/executions/{execution_id}")
async def delete_execution(execution_id: str) -> dict[str, Any]:
    """Delete a stored execution record.

    Executions are durable now, so there has to be a way to remove one; the
    dict version was cleared by every restart.
    """
    deleted = await store.delete_execution(storage_org_id(), execution_id)
    return {"deleted": deleted}


@router.get("/executions")
async def list_executions(limit: int = 100) -> dict[str, Any]:
    """List this organization's executions, most recent first."""
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")

    executions = await store.list_executions(storage_org_id(), limit=limit)
    return {
        "executions": executions,
        "count": len(executions),
    }


@router.post("/executions/{execution_id}/tasks/{task_id}/approve")
async def approve_task(
    execution_id: str,
    task_id: str,
    approval: ApprovalInput,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Approve or reject a pending task.

    Acts on a live asyncio event inside the executor, so it only reaches a run
    started by *this* process. Approval cannot be delivered by writing a row —
    releasing a task waiting on another replica needs a control channel — which
    is why the executor's pending-approval state stays on the single-replica
    pin list even now that execution records are durable.
    """
    success = await executor.approve_task(task_id, approval.approved)

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Task not found or not waiting for approval",
        )

    return {
        "status": "approved" if approval.approved else "rejected",
        "task_id": task_id,
        "reason": approval.reason,
    }


@router.get("/resources")
async def get_resource_usage() -> dict[str, Any]:
    """Get current resource usage."""
    global _resource_pool

    if _resource_pool is None:
        return {"error": "Resource pool not initialized"}

    return await _resource_pool.get_usage()


@router.get("/handlers")
async def list_handlers(
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """List available task handlers."""
    return {
        "handlers": list(executor.handlers.keys()),
        "count": len(executor.handlers),
    }
