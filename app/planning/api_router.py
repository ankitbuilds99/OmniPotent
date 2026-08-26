"""Planning Layer API Router.

REST API for task graph management and execution.

Graphs live in Postgres (`app/planning/store.py`), not in a module-level dict.
Two consequences worth knowing when reading the handlers below:

  * Every mutating route must `save_graph` before returning. The response tells
    the caller the change was made, so it has to be durable by then — there is
    no longer a shared object that later requests happen to observe.
  * Background execution mutates a graph the route loaded, so it saves the
    graph again when it finishes. Without that the run's results would be
    discarded and `GET /graphs/{id}/progress` would report 0% forever.

Concurrent mutations of one graph are last-write-wins: two requests adding a
task at the same instant each read, mutate and write the whole state, so one
task is lost. That is a real change from the shared-dict behaviour and is
accepted here because a graph is built by one operator issuing sequential calls.
Anything that needs concurrent writers wants per-task rows, not this table.

The execution engine and the goal decomposer are per-org too (`_engines` /
`_decomposers` below, via `app/tenancy/scoping.py`), and both are built from the
deployment's real configuration: the engine's default tools query the configured
Loki/Prometheus/Slack instead of returning constant success dicts, and the
decomposer gets an LLM client when a provider key is set.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.planning import store
from app.planning.task_graph import (
    TaskGraph,
    TaskPriority,
    TaskType,
    create_incident_response_graph,
)
from app.planning.goal_decomposer import (
    GoalDecomposer,
    Goal,
    GoalCategory,
)
from app.planning.execution_engine import (
    ExecutionEngine,
    create_default_tools,
)
from app.tenancy.scoping import scoped
from app.tenant_context import integration_secret, storage_org_id

log = logging.getLogger("omnipotent.planning.api")

router = APIRouter(prefix="/planning", tags=["planning"])


def _build_execution_engine() -> ExecutionEngine:
    """An engine whose default tools point at whatever this deployment has.

    The tools were built with no arguments, which meant every one of them was a
    stub returning a constant success dict. They now take the same backend URLs
    the rest of the app uses, and the ones with nothing behind them say so.
    """
    settings = get_settings()
    engine = ExecutionEngine()
    tools = create_default_tools(
        prometheus_url=settings.prometheus_url or None,
        loki_url=settings.loki_url or None,
        elasticsearch_url=settings.elasticsearch_url or None,
        traces_api_url=settings.traces_api_url or None,
        slack_token=integration_secret("SLACK_BOT_TOKEN", settings.slack_bot_token),
        slack_channel=integration_secret("SLACK_CHANNEL", settings.slack_channel),
    )
    for name, handler in tools.items():
        engine.register_tool(name, handler)
    return engine


class _LLMTextClient:
    """The `generate(prompt) -> str` interface `GoalDecomposer` expects, over the
    app's configured chat model.

    The decomposer was only ever constructed without one, so its LLM path could
    not be reached: every category without a built-in template produced the same
    four-step placeholder regardless of what the goal said.
    """

    async def generate(self, prompt: str) -> str:
        from app.llm import get_llm

        message = await get_llm(fast=True).ainvoke(prompt)
        content = getattr(message, "content", message)
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content)


def _build_goal_decomposer() -> GoalDecomposer:
    """Templates for the categories that have one, the LLM for the rest.

    `llm_client` stays None when no provider key is configured; the decomposer
    then reports its `generic` strategy and a confidence to match, rather than
    presenting the placeholder as a template it does not have.
    """
    from app.llm import active_api_key

    client = _LLMTextClient() if active_api_key() else None
    return GoalDecomposer(llm_client=client, use_templates=True)


# One engine and one decomposer per org. The engine holds a tenant's live
# execution results and its tools are built from that tenant's configured
# integrations, so a shared process global let any tenant read and run against
# another's. (The graphs themselves are no longer held in process at all — they
# are rows in planning_graphs, keyed on (id, org_id); see app/planning/store.py.)
_engines = scoped(_build_execution_engine)
_decomposers = scoped(_build_goal_decomposer)


def get_execution_engine() -> ExecutionEngine:
    """The execution engine for the org on the current request."""
    return _engines.get()


def get_goal_decomposer() -> GoalDecomposer:
    """The goal decomposer for the org on the current request."""
    return _decomposers.get()


async def _require_graph(org_id: str, graph_id: str) -> TaskGraph:
    """Load a graph or 404.

    Scoped by org: a graph id is caller-supplied, so an unscoped lookup would
    hand one tenant's graph to another that guessed (or reused) the same name.
    """
    graph = await store.load_graph(org_id, graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Graph not found")
    return graph


class CreateGraphInput(BaseModel):
    graph_id: str | None = None


class AddTaskInput(BaseModel):
    name: str
    description: str = ""
    task_type: str = "custom"
    priority: int = 2
    dependencies: list[str] = []
    inputs: dict[str, Any] = {}
    executor: str = ""
    timeout_seconds: int = 300
    task_id: str | None = None
    metadata: dict[str, Any] = {}


class AddDependencyInput(BaseModel):
    task_id: str
    depends_on: str


class ExecuteGraphInput(BaseModel):
    max_parallel: int = 5
    stop_on_failure: bool = False
    context: dict[str, Any] = {}


class CreateGoalInput(BaseModel):
    description: str
    category: str = "investigation"
    context: dict[str, Any] = {}
    constraints: list[str] = []
    success_criteria: list[str] = []
    priority: int = 2


class CreateIncidentGraphInput(BaseModel):
    incident_id: str
    affected_services: list[str]
    severity: str = "P2"


@router.post("/graphs")
async def create_graph(input: CreateGraphInput) -> dict[str, Any]:
    """Create a new task graph."""
    org_id = storage_org_id()
    graph = TaskGraph(input.graph_id)

    if input.graph_id and await store.load_graph(org_id, graph.id) is not None:
        # Silently overwriting would discard tasks the caller had already added
        # under that id.
        raise HTTPException(status_code=409, detail="Graph already exists")

    await store.save_graph(org_id, graph)
    return {
        "graph_id": graph.id,
        "created": True,
    }


@router.get("/graphs/{graph_id}")
async def get_graph(graph_id: str) -> dict[str, Any]:
    """Get a task graph."""
    graph = await _require_graph(storage_org_id(), graph_id)
    return graph.to_dict()


@router.delete("/graphs/{graph_id}")
async def delete_graph(graph_id: str) -> dict[str, Any]:
    """Delete a task graph."""
    deleted = await store.delete_graph(storage_org_id(), graph_id)
    # Reports what happened. The previous version returned deleted=True even
    # when no such graph existed, so a typo'd id read as a successful delete.
    return {"deleted": deleted}


@router.get("/graphs")
async def list_graphs() -> dict[str, Any]:
    """List all task graphs."""
    graphs = await store.list_graphs(storage_org_id())
    return {
        "graphs": [
            {
                "id": g.id,
                "task_count": len(g.nodes),
                "progress": g.get_progress(),
            }
            for g in graphs
        ],
        "count": len(graphs),
    }


@router.post("/graphs/{graph_id}/tasks")
async def add_task(graph_id: str, input: AddTaskInput) -> dict[str, Any]:
    """Add a task to a graph."""
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)

    try:
        task_type = TaskType(input.task_type)
    except ValueError:
        task_type = TaskType.CUSTOM

    try:
        priority = TaskPriority(input.priority)
    except ValueError:
        priority = TaskPriority.MEDIUM

    try:
        task = graph.add_task(
            name=input.name,
            description=input.description,
            task_type=task_type,
            priority=priority,
            dependencies=input.dependencies,
            inputs=input.inputs,
            executor=input.executor,
            timeout_seconds=input.timeout_seconds,
            task_id=input.task_id,
            metadata=input.metadata,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await store.save_graph(org_id, graph)
    return task.to_dict()


@router.get("/graphs/{graph_id}/tasks/{task_id}")
async def get_task(graph_id: str, task_id: str) -> dict[str, Any]:
    """Get a task from a graph."""
    graph = await _require_graph(storage_org_id(), graph_id)

    task = graph.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.to_dict()


@router.delete("/graphs/{graph_id}/tasks/{task_id}")
async def remove_task(graph_id: str, task_id: str) -> dict[str, Any]:
    """Remove a task from a graph."""
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)

    # remove_task returns None and is a no-op for an unknown id, so membership
    # has to be checked here to answer truthfully.
    existed = graph.get_task(task_id) is not None
    graph.remove_task(task_id)
    await store.save_graph(org_id, graph)
    return {"removed": existed}


@router.post("/graphs/{graph_id}/dependencies")
async def add_dependency(graph_id: str, input: AddDependencyInput) -> dict[str, Any]:
    """Add a dependency between tasks."""
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)

    try:
        graph.add_dependency(input.task_id, input.depends_on)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await store.save_graph(org_id, graph)
    return {"added": True}


@router.get("/graphs/{graph_id}/plan")
async def get_execution_plan(graph_id: str) -> dict[str, Any]:
    """Get execution plan for a graph."""
    graph = await _require_graph(storage_org_id(), graph_id)
    return graph.get_execution_plan().to_dict()


@router.get("/graphs/{graph_id}/progress")
async def get_progress(graph_id: str) -> dict[str, Any]:
    """Get execution progress for a graph."""
    graph = await _require_graph(storage_org_id(), graph_id)
    return graph.get_progress()


@router.get("/graphs/{graph_id}/visualize")
async def visualize_graph(graph_id: str) -> dict[str, Any]:
    """Get ASCII visualization of a graph."""
    graph = await _require_graph(storage_org_id(), graph_id)
    return {
        "visualization": graph.visualize_dag(),
    }


@router.post("/graphs/{graph_id}/execute")
async def execute_graph(
    graph_id: str,
    input: ExecuteGraphInput,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Execute a task graph."""
    # Resolved here, not inside run_execution: a BackgroundTask can run after the
    # middleware has reset the tenant contextvar, which would file the saved
    # result under the wrong org — and `get_execution_engine()` reads the same
    # contextvar, so the engine has to be picked here too.
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)
    engine = get_execution_engine()

    async def run_execution() -> None:
        result = await engine.execute(
            graph,
            max_parallel=input.max_parallel,
            stop_on_failure=input.stop_on_failure,
            initial_context=input.context,
        )
        # Execution mutates task states on the loaded copy. Saving is what makes
        # those results survive the background task; without it /progress would
        # report the graph as untouched forever.
        await store.save_graph(org_id, graph)
        log.info("Execution completed: %s", result.to_dict())

    background_tasks.add_task(run_execution)

    return {
        "status": "started",
        "graph_id": graph_id,
    }


@router.post("/graphs/{graph_id}/execute/sync")
async def execute_graph_sync(
    graph_id: str,
    input: ExecuteGraphInput,
) -> dict[str, Any]:
    """Execute a task graph synchronously."""
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)
    engine = get_execution_engine()

    result = await engine.execute(
        graph,
        max_parallel=input.max_parallel,
        stop_on_failure=input.stop_on_failure,
        initial_context=input.context,
    )

    await store.save_graph(org_id, graph)
    return result.to_dict()


@router.post("/graphs/{graph_id}/checkpoint")
async def create_checkpoint(graph_id: str) -> dict[str, Any]:
    """Create a checkpoint for a graph."""
    org_id = storage_org_id()
    graph = await _require_graph(org_id, graph_id)

    checkpoint = graph.create_checkpoint()
    await store.save_graph(org_id, graph)
    return checkpoint


@router.post("/goals/decompose")
async def decompose_goal(input: CreateGoalInput) -> dict[str, Any]:
    """Decompose a goal into a task graph."""
    org_id = storage_org_id()

    try:
        category = GoalCategory(input.category)
    except ValueError:
        category = GoalCategory.INVESTIGATION

    try:
        priority = TaskPriority(input.priority)
    except ValueError:
        priority = TaskPriority.MEDIUM

    goal = Goal(
        id=f"goal_{await store.count_graphs(org_id)}",
        description=input.description,
        category=category,
        context=input.context,
        constraints=input.constraints,
        success_criteria=input.success_criteria,
        priority=priority,
    )

    decomposer = get_goal_decomposer()
    result = await decomposer.decompose(goal)

    await store.save_graph(org_id, result.task_graph)

    return result.to_dict()


@router.post("/incident-graph")
async def create_incident_graph(input: CreateIncidentGraphInput) -> dict[str, Any]:
    """Create an incident response task graph."""
    org_id = storage_org_id()
    graph = create_incident_response_graph(
        incident_id=input.incident_id,
        affected_services=input.affected_services,
        severity=input.severity,
    )

    await store.save_graph(org_id, graph)

    return graph.to_dict()


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """List registered tools."""
    engine = get_execution_engine()
    return {
        "tools": list(engine._tools.keys()),
    }


@router.get("/executions")
async def list_executions() -> dict[str, Any]:
    """List executions running *on this process*.

    Not backed by the database, and deliberately so: an in-flight execution is a
    live asyncio task, and /cancel and /pause below act on that object. A row in
    Postgres cannot be cancelled — stopping work on another replica needs a
    control channel, not a table. So these three endpoints see only the runs this
    pod started, which is why the single-replica pin still names this store.
    """
    engine = get_execution_engine()
    executions = []

    for exec_id in engine._active_executions:
        status = engine.get_execution_status(exec_id)
        if status:
            executions.append(status)

    return {
        "executions": executions,
        "count": len(executions),
        "scope": "this process only",
    }


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str) -> dict[str, Any]:
    """Cancel an active execution. Only affects runs started by this process."""
    engine = get_execution_engine()
    cancelled = await engine.cancel(execution_id)
    return {"cancelled": cancelled}


@router.post("/executions/{execution_id}/pause")
async def pause_execution(execution_id: str) -> dict[str, Any]:
    """Pause an execution and get checkpoint. This process's runs only."""
    engine = get_execution_engine()
    checkpoint = await engine.pause(execution_id)

    if checkpoint:
        return {
            "paused": True,
            "checkpoint": checkpoint.to_dict(),
        }
    return {"paused": False}
