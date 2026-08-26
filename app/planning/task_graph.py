"""Task Graph for Planning Layer.

DAG-based task representation with dependency tracking,
parallel execution support, and checkpointing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("omnipotent.planning.task_graph")


class TaskStatus(str, Enum):
    """Status of a task."""
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """Priority levels for tasks."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class TaskType(str, Enum):
    """Types of tasks."""
    INVESTIGATION = "investigation"
    DIAGNOSIS = "diagnosis"
    MITIGATION = "mitigation"
    VERIFICATION = "verification"
    COMMUNICATION = "communication"
    DOCUMENTATION = "documentation"
    ROLLBACK = "rollback"
    MONITORING = "monitoring"
    CUSTOM = "custom"


@dataclass
class TaskNode:
    """A node in the task graph."""
    id: str
    name: str
    description: str = ""
    task_type: TaskType = TaskType.CUSTOM
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM

    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)

    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    executor: str = ""
    timeout_seconds: int = 300
    retries: int = 0
    max_retries: int = 3

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    error: str = ""
    progress: float = 0.0

    metadata: dict[str, Any] = field(default_factory=dict)

    _handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = field(
        default=None, repr=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "executor": self.executor,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "progress": self.progress,
            "metadata": self.metadata,
        }

    @property
    def duration_seconds(self) -> float | None:
        """Get task duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
        )


@dataclass
class ExecutionPlan:
    """An execution plan with ordered task levels."""
    id: str
    levels: list[list[str]]
    total_tasks: int
    estimated_duration_seconds: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "levels": self.levels,
            "total_tasks": self.total_tasks,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ExecutionResult:
    """Result of executing a task graph."""
    plan_id: str
    status: str
    completed_tasks: int
    failed_tasks: int
    skipped_tasks: int
    duration_seconds: float
    outputs: dict[str, Any]
    errors: list[dict[str, str]]
    started_at: datetime
    completed_at: datetime
    # Tasks that finished without doing any real work — every tool they needed
    # reported itself unconfigured, or they needed none. `status: "completed"`
    # with five of five tasks listed here is a very different result from
    # `status: "completed"` with none, and the caller could not tell them apart.
    simulated_tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "status": self.status,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "duration_seconds": self.duration_seconds,
            "outputs": self.outputs,
            "errors": self.errors,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "simulated_tasks": self.simulated_tasks,
            "all_simulated": bool(self.completed_tasks)
            and len(self.simulated_tasks) == self.completed_tasks,
        }


class TaskGraph:
    """DAG-based task graph with dependency tracking.

    Features:
    - Dependency validation and cycle detection
    - Topological ordering for execution
    - Parallel execution of independent tasks
    - Progress tracking and checkpointing
    """

    def __init__(self, graph_id: str | None = None):
        self.id = graph_id or str(uuid.uuid4())
        self.nodes: dict[str, TaskNode] = {}
        self._execution_callbacks: list[
            Callable[[str, TaskStatus, dict[str, Any]], Awaitable[None]]
        ] = []
        self._checkpoints: list[dict[str, Any]] = []

    def add_task(
        self,
        name: str,
        description: str = "",
        task_type: TaskType = TaskType.CUSTOM,
        priority: TaskPriority = TaskPriority.MEDIUM,
        dependencies: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
        executor: str = "",
        timeout_seconds: int = 300,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskNode:
        """Add a task to the graph."""
        task_id = task_id or str(uuid.uuid4())[:8]

        if task_id in self.nodes:
            raise ValueError(f"Task {task_id} already exists")

        for dep_id in dependencies or []:
            if dep_id not in self.nodes:
                raise ValueError(f"Dependency {dep_id} not found")

        task = TaskNode(
            id=task_id,
            name=name,
            description=description,
            task_type=task_type,
            priority=priority,
            dependencies=dependencies or [],
            inputs=inputs or {},
            executor=executor,
            timeout_seconds=timeout_seconds,
            _handler=handler,
            metadata=metadata or {},
        )

        self.nodes[task_id] = task

        for dep_id in task.dependencies:
            self.nodes[dep_id].dependents.append(task_id)

        self._update_task_status(task)

        return task

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Add a dependency between tasks."""
        if task_id not in self.nodes:
            raise ValueError(f"Task {task_id} not found")
        if depends_on not in self.nodes:
            raise ValueError(f"Task {depends_on} not found")

        if depends_on in self.nodes[task_id].dependencies:
            return

        self.nodes[task_id].dependencies.append(depends_on)
        self.nodes[depends_on].dependents.append(task_id)

        if self._has_cycle():
            self.nodes[task_id].dependencies.remove(depends_on)
            self.nodes[depends_on].dependents.remove(task_id)
            raise ValueError("Adding dependency would create a cycle")

        self._update_task_status(self.nodes[task_id])

    def remove_task(self, task_id: str) -> None:
        """Remove a task from the graph."""
        if task_id not in self.nodes:
            return

        task = self.nodes[task_id]

        for dep_id in task.dependencies:
            self.nodes[dep_id].dependents.remove(task_id)

        for dependent_id in task.dependents:
            self.nodes[dependent_id].dependencies.remove(task_id)
            self._update_task_status(self.nodes[dependent_id])

        del self.nodes[task_id]

    def get_task(self, task_id: str) -> TaskNode | None:
        """Get a task by ID."""
        return self.nodes.get(task_id)

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get all tasks that are ready to execute."""
        ready = []
        for task in self.nodes.values():
            if task.status == TaskStatus.READY:
                ready.append(task)
        ready.sort(key=lambda t: (t.priority.value, t.created_at))
        return ready

    def get_blocked_tasks(self) -> list[TaskNode]:
        """Get all tasks that are blocked by dependencies."""
        return [
            task for task in self.nodes.values()
            if task.status == TaskStatus.BLOCKED
        ]

    def get_execution_plan(self) -> ExecutionPlan:
        """Generate an execution plan with task levels."""
        levels: list[list[str]] = []
        remaining = set(self.nodes.keys())
        completed: set[str] = set()

        while remaining:
            level = []
            for task_id in list(remaining):
                task = self.nodes[task_id]
                if all(dep in completed for dep in task.dependencies):
                    level.append(task_id)

            if not level:
                level = list(remaining)[:1]

            level.sort(key=lambda tid: (
                self.nodes[tid].priority.value,
                self.nodes[tid].created_at,
            ))

            levels.append(level)
            for task_id in level:
                remaining.remove(task_id)
                completed.add(task_id)

        estimated_duration = sum(
            max(self.nodes[tid].timeout_seconds for tid in level)
            for level in levels
        )

        return ExecutionPlan(
            id=str(uuid.uuid4())[:8],
            levels=levels,
            total_tasks=len(self.nodes),
            estimated_duration_seconds=estimated_duration,
        )

    async def execute(
        self,
        max_parallel: int = 5,
        stop_on_failure: bool = False,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute all tasks in the graph."""
        started_at = datetime.now(timezone.utc)
        context = context or {}

        plan = self.get_execution_plan()

        completed_tasks = 0
        failed_tasks = 0
        skipped_tasks = 0
        errors: list[dict[str, str]] = []
        final_outputs: dict[str, Any] = {}

        for level_idx, level in enumerate(plan.levels):
            log.info("Executing level %d with %d tasks", level_idx, len(level))

            if stop_on_failure and failed_tasks > 0:
                for task_id in level:
                    task = self.nodes[task_id]
                    task.status = TaskStatus.SKIPPED
                    skipped_tasks += 1
                continue

            semaphore = asyncio.Semaphore(max_parallel)

            async def execute_with_semaphore(task_id: str) -> None:
                async with semaphore:
                    await self._execute_task(task_id, context)

            await asyncio.gather(
                *[execute_with_semaphore(task_id) for task_id in level],
                return_exceptions=True,
            )

            for task_id in level:
                task = self.nodes[task_id]
                if task.status == TaskStatus.COMPLETED:
                    completed_tasks += 1
                    final_outputs[task_id] = task.outputs
                elif task.status == TaskStatus.FAILED:
                    failed_tasks += 1
                    errors.append({"task_id": task_id, "error": task.error})
                elif task.status == TaskStatus.SKIPPED:
                    skipped_tasks += 1

        completed_at = datetime.now(timezone.utc)
        duration = (completed_at - started_at).total_seconds()

        status = "completed"
        if failed_tasks > 0:
            status = "partial_failure" if completed_tasks > 0 else "failed"

        return ExecutionResult(
            plan_id=plan.id,
            status=status,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            skipped_tasks=skipped_tasks,
            duration_seconds=duration,
            outputs=final_outputs,
            errors=errors,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _execute_task(
        self,
        task_id: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a single task."""
        task = self.nodes[task_id]

        if task.status != TaskStatus.READY:
            return

        for dep_id in task.dependencies:
            dep = self.nodes[dep_id]
            if dep.status != TaskStatus.COMPLETED:
                task.status = TaskStatus.BLOCKED
                return

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)

        await self._notify_callbacks(task_id, TaskStatus.RUNNING, {})

        try:
            inputs = {**task.inputs}
            for dep_id in task.dependencies:
                dep_outputs = self.nodes[dep_id].outputs
                inputs[f"dep_{dep_id}"] = dep_outputs

            inputs["context"] = context

            if task._handler:
                try:
                    result = await asyncio.wait_for(
                        task._handler(inputs),
                        timeout=task.timeout_seconds,
                    )
                    task.outputs = result or {}
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Task {task_id} timed out after {task.timeout_seconds}s")
            else:
                task.outputs = {"status": "no_handler"}

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            task.progress = 1.0

            await self._notify_callbacks(task_id, TaskStatus.COMPLETED, task.outputs)

            for dependent_id in task.dependents:
                self._update_task_status(self.nodes[dependent_id])

        except Exception as e:
            log.error("Task %s failed: %s", task_id, e)
            task.error = str(e)
            task.retries += 1

            if task.retries < task.max_retries:
                task.status = TaskStatus.READY
                log.info("Retrying task %s (attempt %d/%d)", task_id, task.retries + 1, task.max_retries)
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                await self._notify_callbacks(task_id, TaskStatus.FAILED, {"error": str(e)})

    def _update_task_status(self, task: TaskNode) -> None:
        """Update task status based on dependencies."""
        if task.is_terminal():
            return

        if task.status == TaskStatus.RUNNING:
            return

        all_deps_completed = all(
            self.nodes[dep_id].status == TaskStatus.COMPLETED
            for dep_id in task.dependencies
        )

        any_dep_failed = any(
            self.nodes[dep_id].status == TaskStatus.FAILED
            for dep_id in task.dependencies
        )

        if any_dep_failed:
            task.status = TaskStatus.BLOCKED
        elif all_deps_completed:
            task.status = TaskStatus.READY
        elif task.dependencies:
            task.status = TaskStatus.PENDING
        else:
            task.status = TaskStatus.READY

    def _has_cycle(self) -> bool:
        """Check if the graph has a cycle."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for dep_id in self.nodes[node_id].dependents:
                if dep_id not in visited:
                    if dfs(dep_id):
                        return True
                elif dep_id in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self.nodes:
            if node_id not in visited:
                if dfs(node_id):
                    return True

        return False

    def on_task_update(
        self,
        callback: Callable[[str, TaskStatus, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a callback for task updates."""
        self._execution_callbacks.append(callback)

    async def _notify_callbacks(
        self,
        task_id: str,
        status: TaskStatus,
        data: dict[str, Any],
    ) -> None:
        """Notify all registered callbacks."""
        for callback in self._execution_callbacks:
            try:
                await callback(task_id, status, data)
            except Exception as e:
                log.error("Callback error: %s", e)

    def create_checkpoint(self) -> dict[str, Any]:
        """Create a checkpoint of current state."""
        checkpoint = {
            "graph_id": self.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tasks": {
                task_id: {
                    "status": task.status.value,
                    "outputs": task.outputs,
                    "error": task.error,
                    "retries": task.retries,
                    "progress": task.progress,
                }
                for task_id, task in self.nodes.items()
            },
        }
        self._checkpoints.append(checkpoint)
        return checkpoint

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Restore state from a checkpoint."""
        for task_id, task_state in checkpoint.get("tasks", {}).items():
            if task_id in self.nodes:
                task = self.nodes[task_id]
                task.status = TaskStatus(task_state["status"])
                task.outputs = task_state.get("outputs", {})
                task.error = task_state.get("error", "")
                task.retries = task_state.get("retries", 0)
                task.progress = task_state.get("progress", 0.0)

    def get_progress(self) -> dict[str, Any]:
        """Get overall progress of the graph."""
        total = len(self.nodes)
        if total == 0:
            return {"progress": 0.0, "status": "empty"}

        completed = sum(
            1 for t in self.nodes.values()
            if t.status == TaskStatus.COMPLETED
        )
        failed = sum(
            1 for t in self.nodes.values()
            if t.status == TaskStatus.FAILED
        )
        running = sum(
            1 for t in self.nodes.values()
            if t.status == TaskStatus.RUNNING
        )

        progress = completed / total

        if failed > 0 and completed + failed == total:
            status = "failed"
        elif completed == total:
            status = "completed"
        elif running > 0:
            status = "running"
        else:
            status = "pending"

        return {
            "progress": progress,
            "status": status,
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": total - completed - failed - running,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert graph to dictionary."""
        return {
            "id": self.id,
            "tasks": {
                task_id: task.to_dict()
                for task_id, task in self.nodes.items()
            },
            "progress": self.get_progress(),
        }

    def visualize_dag(self) -> str:
        """Generate ASCII visualization of the DAG."""
        if not self.nodes:
            return "(empty graph)"

        lines = []
        plan = self.get_execution_plan()

        for level_idx, level in enumerate(plan.levels):
            lines.append(f"Level {level_idx}:")
            for task_id in level:
                task = self.nodes[task_id]
                status_icon = {
                    TaskStatus.PENDING: "⏳",
                    TaskStatus.READY: "🔵",
                    TaskStatus.RUNNING: "🔄",
                    TaskStatus.COMPLETED: "✅",
                    TaskStatus.FAILED: "❌",
                    TaskStatus.SKIPPED: "⏭️",
                    TaskStatus.BLOCKED: "🚫",
                    TaskStatus.CANCELLED: "🛑",
                }.get(task.status, "?")

                deps_str = ""
                if task.dependencies:
                    deps_str = f" <- [{', '.join(task.dependencies)}]"

                lines.append(f"  {status_icon} {task_id}: {task.name}{deps_str}")

        return "\n".join(lines)


def create_incident_response_graph(
    incident_id: str,
    affected_services: list[str],
    severity: str = "P2",
) -> TaskGraph:
    """Create a standard incident response task graph."""
    graph = TaskGraph(f"incident-{incident_id}")

    graph.add_task(
        task_id="triage",
        name="Initial Triage",
        description="Assess incident severity and scope",
        task_type=TaskType.INVESTIGATION,
        priority=TaskPriority.CRITICAL,
        inputs={
            "incident_id": incident_id,
            "affected_services": affected_services,
            "initial_severity": severity,
        },
    )

    graph.add_task(
        task_id="notify",
        name="Notify Stakeholders",
        description="Send initial notifications",
        task_type=TaskType.COMMUNICATION,
        priority=TaskPriority.HIGH,
        dependencies=["triage"],
    )

    for i, service in enumerate(affected_services[:5]):
        graph.add_task(
            task_id=f"investigate_{i}",
            name=f"Investigate {service}",
            description=f"Investigate issues in {service}",
            task_type=TaskType.INVESTIGATION,
            priority=TaskPriority.HIGH,
            dependencies=["triage"],
            inputs={"service": service},
        )

    investigation_ids = [f"investigate_{i}" for i in range(min(len(affected_services), 5))]

    graph.add_task(
        task_id="correlate",
        name="Correlate Findings",
        description="Correlate investigation results",
        task_type=TaskType.DIAGNOSIS,
        priority=TaskPriority.HIGH,
        dependencies=investigation_ids,
    )

    graph.add_task(
        task_id="root_cause",
        name="Identify Root Cause",
        description="Determine root cause from correlated data",
        task_type=TaskType.DIAGNOSIS,
        priority=TaskPriority.HIGH,
        dependencies=["correlate"],
    )

    graph.add_task(
        task_id="mitigation",
        name="Execute Mitigation",
        description="Apply mitigation strategy",
        task_type=TaskType.MITIGATION,
        priority=TaskPriority.CRITICAL,
        dependencies=["root_cause"],
    )

    graph.add_task(
        task_id="verify",
        name="Verify Resolution",
        description="Verify the incident is resolved",
        task_type=TaskType.VERIFICATION,
        priority=TaskPriority.HIGH,
        dependencies=["mitigation"],
    )

    graph.add_task(
        task_id="postmortem",
        name="Create Postmortem",
        description="Document incident and lessons learned",
        task_type=TaskType.DOCUMENTATION,
        priority=TaskPriority.MEDIUM,
        dependencies=["verify"],
    )

    return graph
