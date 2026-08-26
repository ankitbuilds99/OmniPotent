"""API Router for DAG Coordinator.

Exposes REST API for task graph management and execution.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
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
    """The DAG executor for the org on the current request.

    Redis is attached here rather than in `_CoordinatorState.__init__` because
    obtaining a pool is async and that constructor is not. It was hardcoded to
    `None`, so checkpointing was off even where COPILOT_REDIS_URL was set, and
    `resume_from` could only ever raise `Checkpoint ... not found` — inside a
    background task, after the route had already answered "started".

    Guarded on the URL on purpose: with none configured `get_redis()` hands back
    an in-memory stand-in that has no `lpush`, so wiring it in unconditionally
    would trade a silent no-op for a crash mid-checkpoint.
    """
    state = _state.get()
    if state.executor.redis is None and get_settings().copilot_redis_url:
        from app.redis_pool import RedisPoolType, get_redis

        state.executor.redis = await get_redis(RedisPoolType.CACHE)
    return state.executor


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

    @field_validator("priority")
    @classmethod
    def _known_priority(cls, value: str) -> str:
        """Reject an unknown priority here rather than as a KeyError later.

        `TaskPriority[value.upper()]` in the route body raised KeyError, which
        the app's handler turns into a 500 whose message says the resource was
        not found — three different wrong answers to one typo.
        """
        if value.upper() not in TaskPriority.__members__:
            raise ValueError(
                f"unknown priority {value!r}; expected one of "
                f"{', '.join(TaskPriority.__members__)}"
            )
        return value.upper()


class DAGInput(BaseModel):
    """Input for creating a DAG."""
    id: str
    name: str
    tasks: list[TaskNodeInput]


def _build_dag(dag_input: DAGInput) -> TaskDAG:
    """A validated `TaskDAG` from request input, or an HTTP 400 saying why.

    Both DAG routes built this inline and neither guarded it, so a duplicate
    task id — a `ValueError` out of `add_task` — was a 500 rather than the 400
    it plainly is.
    """
    dag = TaskDAG(dag_input.id, dag_input.name)

    for task_input in dag_input.tasks:
        try:
            dag.add_task(TaskNode(
                id=task_input.id,
                name=task_input.name,
                handler=task_input.handler,
                depends_on=task_input.depends_on,
                input_params=task_input.input_params,
                timeout_seconds=task_input.timeout_seconds,
                priority=TaskPriority[task_input.priority.upper()],
                requires_approval=task_input.requires_approval,
                description=task_input.description,
            ))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid DAG: {exc}") from exc

    errors = dag.validate()
    if errors:
        raise HTTPException(status_code=400, detail=f"Invalid DAG: {errors}")

    unknown = sorted({t.handler for t in dag.tasks.values()} - set(_state.get().executor.handlers))
    if unknown:
        # Otherwise the DAG is accepted, runs, and every task fails at
        # dispatch — a background failure the caller never sees, for a mistake
        # that was visible at submission time.
        raise HTTPException(
            status_code=400,
            detail=f"Unknown handler(s): {', '.join(unknown)}. See GET /coordinator/handlers.",
        )

    return dag


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
    dag = _build_dag(dag_input)

    return {
        "dag": dag.to_dict(),
        "execution_levels": dag.get_execution_levels(),
        "critical_path": dag.get_critical_path(),
    }


def _start(
    background_tasks: BackgroundTasks,
    executor: DAGExecutor,
    dag: TaskDAG,
    context: dict[str, Any],
    resume_from: str | None = None,
) -> str:
    """Register an execution, queue it, and return its id.

    The id is minted here and the placeholder is in the registry *before* the
    run starts. Both routes used to do `execution = await executor.execute(...)`
    and only then register it, so nothing was listable until it had already
    finished — and a DAG parked on a human approval, which is precisely when you
    need to find it, was invisible for as long as it was blocked. The routes
    also returned no id at all, so there was nothing to poll with either.
    """
    executions = _active_executions()
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"
    executions[execution_id] = DAGExecution(
        id=execution_id,
        dag_id=dag.id,
        initial_context=dict(context),
        accumulated_context=dict(context),
        execution_levels=dag.get_execution_levels(),
    )

    async def run() -> None:
        try:
            execution = await executor.execute(
                dag,
                context=context,
                execution_id=execution_id,
                resume_from=resume_from,
            )
            executions[execution.id] = execution
        except Exception as exc:
            # A background failure with nowhere to surface is how `resume_from`
            # against a missing checkpoint looked like a DAG that started and
            # then never did anything at all.
            log.exception("DAG execution %s failed to run", execution_id)
            failed = executions.get(execution_id)
            if failed is not None:
                failed.state = ExecutionState.FAILED
                failed.error = str(exc)

    background_tasks.add_task(run)
    return execution_id


@router.post("/dags/{dag_id}/execute")
async def execute_dag(
    dag_id: str,
    dag_input: DAGInput,
    execute_input: ExecuteDAGInput,
    background_tasks: BackgroundTasks,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Execute a DAG."""
    dag = _build_dag(dag_input)

    if execute_input.resume_from and not executor.redis:
        # Checkpoints live in Redis and nowhere else, so without it `execute()`
        # raises `Checkpoint ... not found` in the background task and the route
        # has already answered "started". Say so up front instead.
        raise HTTPException(
            status_code=400,
            detail=(
                "resume_from needs checkpoint storage, which requires "
                "COPILOT_REDIS_URL to be configured."
            ),
        )

    execution_id = _start(
        background_tasks, executor, dag, execute_input.context, execute_input.resume_from
    )

    return {
        "status": "started",
        "dag_id": dag_id,
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

    execution_id = _start(background_tasks, executor, dag, input.context)

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


@router.get("/approvals")
async def list_pending_approvals(
    execution_id: str | None = None,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """The tasks currently blocked on a human decision."""
    pending = executor.pending_approvals(execution_id)
    return {"approvals": pending, "count": len(pending)}


@router.post("/executions/{execution_id}/tasks/{task_id}/approve")
async def approve_task(
    execution_id: str,
    task_id: str,
    approval: ApprovalInput,
    executor: DAGExecutor = Depends(get_executor),
) -> dict[str, Any]:
    """Approve or reject a pending task in one specific execution.

    `execution_id` was taken in the path and then never used: the approval was
    matched on task id alone, so approving `restart_service` in the DAG you were
    looking at also approved it in every other run of that DAG that happened to
    be parked on the same task.
    """
    success = await executor.approve_task(execution_id, task_id, approval.approved)

    if not success:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Task {task_id} is not waiting for approval in execution "
                f"{execution_id}. See GET /coordinator/approvals."
            ),
        )

    log.info(
        "Task %s in execution %s %s: %s",
        task_id, execution_id,
        "approved" if approval.approved else "rejected",
        approval.reason or "no reason given",
    )
    return {
        "status": "approved" if approval.approved else "rejected",
        "execution_id": execution_id,
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
