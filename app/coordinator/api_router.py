"""API Router for DAG Coordinator.

Exposes REST API for task graph management and execution.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.coordinator.dag_executor import (
    DAGExecution,
    DAGExecutor,
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
from app.tenancy.scoping import scoped

log = logging.getLogger("omnipotent.coordinator.api")

router = APIRouter(prefix="/coordinator", tags=["coordinator"])


class _CoordinatorState:
    """One org's DAG executor, resource pool, replanner and execution history.

    Shared across the process these were a cross-tenant channel in both
    directions: executions carry investigation output (log lines, metric values,
    service names) that `GET /executions` handed to anyone, and a single
    ResourcePool meant one tenant's parallel DAGs starved everyone else's.
    """

    def __init__(self) -> None:
        settings = get_settings()

        self.resource_pool = ResourcePool(cpu_cores=8, memory_mb=16384, gpus=0)
        self.executor = DAGExecutor(
            handlers={},
            redis_client=None,
            max_parallel=10,
            resource_pool=self.resource_pool,
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
        for name, handler in {**investigation_handlers, **remediation_handlers}.items():
            self.executor.register_handler(name, handler)

        self.replanner = DynamicReplanner(llm_client=None, knowledge_graph=None)
        self.active_executions: dict[str, DAGExecution] = {}


_state = scoped(_CoordinatorState)


def _active_executions() -> dict[str, DAGExecution]:
    return _state.get().active_executions


async def get_executor() -> DAGExecutor:
    """The DAG executor for the org on the current request."""
    return _state.get().executor


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

    async def run_dag() -> None:
        execution = await executor.execute(
            dag,
            context=execute_input.context,
            resume_from=execute_input.resume_from,
        )
        _active_executions()[execution.id] = execution

    background_tasks.add_task(run_dag)

    return {
        "status": "started",
        "dag_id": dag_id,
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

    async def run_investigation() -> None:
        execution = await executor.execute(dag, context=input.context)
        _active_executions()[execution.id] = execution

    background_tasks.add_task(run_investigation)

    return {
        "status": "started",
        "dag_id": dag.id,
        "dag_name": dag.name,
        "tasks": len(dag.tasks),
        "execution_levels": dag.get_execution_levels(),
    }


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
) -> dict[str, Any]:
    """Get execution status."""
    execution = _active_executions().get(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution.to_dict()


@router.get("/executions")
async def list_executions() -> dict[str, Any]:
    """List all executions."""
    return {
        "executions": [
            {
                "id": exec_id,
                "dag_id": execution.dag_id,
                "state": execution.state.value,
                "current_level": execution.current_level,
            }
            for exec_id, execution in _active_executions().items()
        ],
        "count": len(_active_executions()),
    }


@router.post("/executions/{execution_id}/tasks/{task_id}/approve")
async def approve_task(
    execution_id: str,
    task_id: str,
    approval: ApprovalInput,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Approve or reject a pending task."""
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
    """Get current resource usage for the org on the current request."""
    return await _state.get().resource_pool.get_usage()


@router.get("/handlers")
async def list_handlers(
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """List available task handlers."""
    return {
        "handlers": list(executor.handlers.keys()),
        "count": len(executor.handlers),
    }
