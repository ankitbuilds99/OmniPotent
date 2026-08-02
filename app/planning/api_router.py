"""Planning Layer API Router.

REST API for task graph management and execution.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

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

log = logging.getLogger("omnipotent.planning.api")

router = APIRouter(prefix="/planning", tags=["planning"])

_graphs: dict[str, TaskGraph] = {}
_execution_engine: ExecutionEngine | None = None
_goal_decomposer: GoalDecomposer | None = None


def get_execution_engine() -> ExecutionEngine:
    """Get or create execution engine."""
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
        for name, handler in create_default_tools().items():
            _execution_engine.register_tool(name, handler)
    return _execution_engine


def get_goal_decomposer() -> GoalDecomposer:
    """Get or create goal decomposer."""
    global _goal_decomposer
    if _goal_decomposer is None:
        _goal_decomposer = GoalDecomposer(use_templates=True)
    return _goal_decomposer


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
    graph = TaskGraph(input.graph_id)
    _graphs[graph.id] = graph
    return {
        "graph_id": graph.id,
        "created": True,
    }


@router.get("/graphs/{graph_id}")
async def get_graph(graph_id: str) -> dict[str, Any]:
    """Get a task graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")
    return _graphs[graph_id].to_dict()


@router.delete("/graphs/{graph_id}")
async def delete_graph(graph_id: str) -> dict[str, Any]:
    """Delete a task graph."""
    if graph_id in _graphs:
        del _graphs[graph_id]
    return {"deleted": True}


@router.get("/graphs")
async def list_graphs() -> dict[str, Any]:
    """List all task graphs."""
    return {
        "graphs": [
            {
                "id": g.id,
                "task_count": len(g.nodes),
                "progress": g.get_progress(),
            }
            for g in _graphs.values()
        ],
        "count": len(_graphs),
    }


@router.post("/graphs/{graph_id}/tasks")
async def add_task(graph_id: str, input: AddTaskInput) -> dict[str, Any]:
    """Add a task to a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    graph = _graphs[graph_id]

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
        return task.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/graphs/{graph_id}/tasks/{task_id}")
async def get_task(graph_id: str, task_id: str) -> dict[str, Any]:
    """Get a task from a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    task = _graphs[graph_id].get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.to_dict()


@router.delete("/graphs/{graph_id}/tasks/{task_id}")
async def remove_task(graph_id: str, task_id: str) -> dict[str, Any]:
    """Remove a task from a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    _graphs[graph_id].remove_task(task_id)
    return {"removed": True}


@router.post("/graphs/{graph_id}/dependencies")
async def add_dependency(graph_id: str, input: AddDependencyInput) -> dict[str, Any]:
    """Add a dependency between tasks."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        _graphs[graph_id].add_dependency(input.task_id, input.depends_on)
        return {"added": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/graphs/{graph_id}/plan")
async def get_execution_plan(graph_id: str) -> dict[str, Any]:
    """Get execution plan for a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    plan = _graphs[graph_id].get_execution_plan()
    return plan.to_dict()


@router.get("/graphs/{graph_id}/progress")
async def get_progress(graph_id: str) -> dict[str, Any]:
    """Get execution progress for a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    return _graphs[graph_id].get_progress()


@router.get("/graphs/{graph_id}/visualize")
async def visualize_graph(graph_id: str) -> dict[str, Any]:
    """Get ASCII visualization of a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    return {
        "visualization": _graphs[graph_id].visualize_dag(),
    }


@router.post("/graphs/{graph_id}/execute")
async def execute_graph(
    graph_id: str,
    input: ExecuteGraphInput,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Execute a task graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    engine = get_execution_engine()
    graph = _graphs[graph_id]

    async def run_execution() -> None:
        result = await engine.execute(
            graph,
            max_parallel=input.max_parallel,
            stop_on_failure=input.stop_on_failure,
            initial_context=input.context,
        )
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
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    engine = get_execution_engine()
    graph = _graphs[graph_id]

    result = await engine.execute(
        graph,
        max_parallel=input.max_parallel,
        stop_on_failure=input.stop_on_failure,
        initial_context=input.context,
    )

    return result.to_dict()


@router.post("/graphs/{graph_id}/checkpoint")
async def create_checkpoint(graph_id: str) -> dict[str, Any]:
    """Create a checkpoint for a graph."""
    if graph_id not in _graphs:
        raise HTTPException(status_code=404, detail="Graph not found")

    checkpoint = _graphs[graph_id].create_checkpoint()
    return checkpoint


@router.post("/goals/decompose")
async def decompose_goal(input: CreateGoalInput) -> dict[str, Any]:
    """Decompose a goal into a task graph."""
    try:
        category = GoalCategory(input.category)
    except ValueError:
        category = GoalCategory.INVESTIGATION

    try:
        priority = TaskPriority(input.priority)
    except ValueError:
        priority = TaskPriority.MEDIUM

    goal = Goal(
        id=f"goal_{len(_graphs)}",
        description=input.description,
        category=category,
        context=input.context,
        constraints=input.constraints,
        success_criteria=input.success_criteria,
        priority=priority,
    )

    decomposer = get_goal_decomposer()
    result = await decomposer.decompose(goal)

    _graphs[result.task_graph.id] = result.task_graph

    return result.to_dict()


@router.post("/incident-graph")
async def create_incident_graph(input: CreateIncidentGraphInput) -> dict[str, Any]:
    """Create an incident response task graph."""
    graph = create_incident_response_graph(
        incident_id=input.incident_id,
        affected_services=input.affected_services,
        severity=input.severity,
    )

    _graphs[graph.id] = graph

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
    """List active executions."""
    engine = get_execution_engine()
    executions = []

    for exec_id in engine._active_executions:
        status = engine.get_execution_status(exec_id)
        if status:
            executions.append(status)

    return {
        "executions": executions,
        "count": len(executions),
    }


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str) -> dict[str, Any]:
    """Cancel an active execution."""
    engine = get_execution_engine()
    cancelled = await engine.cancel(execution_id)
    return {"cancelled": cancelled}


@router.post("/executions/{execution_id}/pause")
async def pause_execution(execution_id: str) -> dict[str, Any]:
    """Pause an execution and get checkpoint."""
    engine = get_execution_engine()
    checkpoint = await engine.pause(execution_id)

    if checkpoint:
        return {
            "paused": True,
            "checkpoint": checkpoint.to_dict(),
        }
    return {"paused": False}
