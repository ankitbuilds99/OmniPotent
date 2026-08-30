"""API Router for DAG Coordinator.

Exposes REST API for task graph management and execution.

Executions live in Postgres (`app/coordinator/store.py`), not in a module-level
dict. The dict this replaced was written only after `executor.execute()`
returned, so `GET /executions/{id}` answered 404 for the whole run — the exact
window a caller polls — and only started answering once the answer was stale.
Here a row is written before the DAG starts and updated when it ends, so a poll
mid-run sees the real state.

The executor, resource pool and replanner behind those routes are per-org
(`app/tenancy/scoping.py`) rather than one process-wide singleton: a shared
ResourcePool let one tenant's parallel DAGs starve everyone else's, and a shared
handler registry gave every tenant the same executor instance.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

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
from app.tenancy.scoping import scoped
from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.coordinator.api")

router = APIRouter(prefix="/coordinator", tags=["coordinator"])


class _HandlerLLM:
    """The ``await llm.complete(prompt=...)`` facade the coordinator handlers
    and replanner expect, over the app's chat-model factory (app/llm.py).

    The model is resolved per call so UI provider/model/key switches apply
    without a restart, and `invoke` runs in a thread because the resilient
    wrapper in app/llm.py is synchronous. `temperature` is accepted and
    ignored: the Anthropic path rejects sampling params (see app/llm.py).
    """

    async def complete(self, prompt: str, temperature: float = 0.0, **_: Any) -> str:
        import asyncio

        from app.llm import get_llm

        message = await asyncio.to_thread(get_llm().invoke, prompt)
        content = getattr(message, "content", message)
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content)


def _build_llm_client() -> _HandlerLLM | None:
    """An LLM client for the handlers, or None with a log line saying why not.

    None disables LLM hypothesis generation and replanning (the handlers fall
    back to heuristics and say so in their output). Multi-tenant deployments
    stay on None deliberately: handlers run in FastAPI background tasks after
    the tenant contextvar may have been reset, so a per-call key lookup could
    fall through to the host's key — the isolation rule in app/runtime.py.
    """
    settings = get_settings()
    if settings.copilot_multi_tenant:
        log.warning(
            "Coordinator LLM disabled: multi-tenant mode resolves per-tenant "
            "keys from request context, which background DAG tasks do not have"
        )
        return None

    from app import runtime

    if not runtime.provider_key():
        log.warning(
            "Coordinator LLM disabled: no API key configured for provider %r "
            "(set the provider key to enable LLM hypothesis generation and "
            "replanning; heuristics are used instead)",
            runtime.provider(),
        )
        return None
    return _HandlerLLM()


def _build_knowledge_graph() -> Any | None:
    """The knowledge graph for deployment correlation, or None saying why not.

    With this hardcoded to None, `DeploymentsHandler` could never read recent
    deployments and deployment correlation never fired. The client mirrors the
    per-org registry in app/knowledge_graph/api_router.py: with no
    COPILOT_NEO4J_URI it reads the org-scoped relational graph store, and with
    one it connects lazily on first use (`_ensure_connected`). Multi-tenant
    deployments keep None: DAG handlers run in background tasks without the
    tenant contextvar, so the relational store could not scope rows to the
    right org, and a shared live Neo4j is refused for tenants anyway
    (`_OrgGraph.require_isolated`).
    """
    settings = get_settings()
    if settings.copilot_multi_tenant:
        log.warning(
            "Coordinator deployment correlation disabled: the knowledge graph "
            "cannot be org-scoped from a background DAG task in multi-tenant mode"
        )
        return None

    from app.knowledge_graph import Neo4jKnowledgeGraph

    return Neo4jKnowledgeGraph(
        uri=settings.copilot_neo4j_uri,
        username=settings.copilot_neo4j_username,
        password=settings.copilot_neo4j_password,
        database=settings.copilot_neo4j_database,
    )


class _CoordinatorState:
    """One org's DAG executor, resource pool and replanner.

    Shared across the process these were a cross-tenant channel: a single
    ResourcePool meant one tenant's parallel DAGs starved everyone else's, and
    one executor meant one handler registry for every tenant. Execution records
    are not held here at all — they live in `app/coordinator/store.py`, keyed on
    (id, org_id), so they survive a restart and a poll mid-run.
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

        # These were hardcoded to None, so deployment correlation and LLM
        # hypothesis generation could never fire in the production wiring,
        # whatever the operator configured. The builders return None only when
        # the config genuinely is absent, and log the limitation when they do.
        knowledge_graph = _build_knowledge_graph()
        llm_client = _build_llm_client()

        investigation_handlers = create_investigation_handlers(
            prometheus_url=settings.prometheus_url or None,
            loki_url=settings.loki_url or None,
            elasticsearch_url=settings.elasticsearch_url or None,
            github_token=settings.github_token or None,
            knowledge_graph=knowledge_graph,
            llm_client=llm_client,
        )
        remediation_handlers = create_remediation_handlers(
            k8s_client=None,
            prometheus_url=settings.prometheus_url or None,
            knowledge_graph=knowledge_graph,
            llm_client=llm_client,
        )
        for name, handler in {**investigation_handlers, **remediation_handlers}.items():
            self.executor.register_handler(name, handler)

        self.replanner = DynamicReplanner(
            llm_client=llm_client, knowledge_graph=knowledge_graph
        )


_state = scoped(_CoordinatorState)


async def _record_started(
    org_id: str,
    execution_id: str,
    dag: TaskDAG,
    context: dict[str, Any] | None = None,
) -> None:
    """Write the RUNNING placeholder row for a run that is about to begin.

    Called from `_start`, inside the request, so the row exists the moment the
    route answers with the id; also called at the top of `_run_and_record` so a
    caller that queues the task directly still gets it. `save_execution` is an
    upsert, so the second write is the same row.
    """
    await store.save_execution(
        org_id,
        DAGExecution(
            id=execution_id,
            dag_id=dag.id,
            state=ExecutionState.RUNNING,
            initial_context=dict(context or {}),
            accumulated_context=dict(context or {}),
            execution_levels=dag.get_execution_levels(),
        ),
    )


async def _run_and_record(
    executor: DAGExecutor,
    dag: TaskDAG,
    org_id: str,
    execution_id: str,
    context: dict[str, Any] | None = None,
    resume_from: str | None = None,
) -> None:
    """Run a DAG, recording its state before it starts and after it ends.

    The pre-run row is what makes polling work: previously nothing was written
    until `execute()` returned, so a DAG parked on a human approval — precisely
    when you need to find it — was invisible for as long as it was blocked.

    The `except` is not swallowing a bug: `execute()` re-raises after marking its
    own execution object FAILED, and this is a background task with no caller to
    receive the exception, so the failure has to be written down here or it is
    lost entirely — which is how `resume_from` against a missing checkpoint
    looked like a DAG that started and then never did anything at all.
    """
    await _record_started(org_id, execution_id, dag, context)

    try:
        execution = await executor.execute(
            dag,
            context=context,
            execution_id=execution_id,
            resume_from=resume_from,
        )
    except Exception as exc:
        # Outcome and reason only: the object `execute()` built is not returned
        # on the raising path, so there are no observed task results to store.
        # The message goes onto the record as well as into the log — a log line
        # on one pod is not something the caller polling this run can read.
        log.exception("DAG execution %s failed", execution_id)
        await store.mark_failed(org_id, execution_id, str(exc))
        return

    await store.save_execution(org_id, execution)


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


async def _start(
    background_tasks: BackgroundTasks,
    executor: DAGExecutor,
    dag: TaskDAG,
    context: dict[str, Any],
    resume_from: str | None = None,
) -> str:
    """Register an execution, queue the run, and return the id.

    Both routes used to do `execution = await executor.execute(...)` and only
    then record it, and neither returned an id at all, so there was nothing to
    poll with. The row is written *here*, in the request, rather than by the
    background task: a queued task does not start until the response has been
    sent, so recording it there left a window in which the route had handed out
    an id that `GET /executions/{id}` answered 404 for.

    `storage_org_id()` is resolved here for the same reason — a FastAPI
    BackgroundTask can run after the middleware has reset the tenant contextvar,
    which would file the execution under the wrong org.
    """
    org_id = storage_org_id()
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"
    await _record_started(org_id, execution_id, dag, context)

    background_tasks.add_task(
        _run_and_record, executor, dag, org_id, execution_id, context, resume_from
    )
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

    execution_id = await _start(
        background_tasks, executor, dag, execute_input.context, execute_input.resume_from
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

    execution_id = await _start(background_tasks, executor, dag, input.context)

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

    Acts on a live asyncio future inside the executor, so it only reaches a run
    started by *this* process. Approval cannot be delivered by writing a row —
    releasing a task waiting on another replica needs a control channel — which
    is why the executor's pending-approval state stays on the single-replica
    pin list even now that execution records are durable.
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
