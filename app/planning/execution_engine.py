"""Execution Engine for Planning Layer.

Executes task graphs with:
- Parallel execution support
- Checkpointing and recovery
- Progress tracking
- Tool integration
"""

from __future__ import annotations

import asyncio
import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from pathlib import Path
import uuid

from app.config import get_settings
from app.planning.task_graph import (
    TaskGraph,
    TaskNode,
    TaskStatus,
    ExecutionResult,
)

log = logging.getLogger("omnipotent.planning.execution_engine")


@dataclass
class Checkpoint:
    """A checkpoint for task graph execution."""
    id: str
    graph_id: str
    timestamp: datetime
    task_states: dict[str, dict[str, Any]]
    context: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "graph_id": self.graph_id,
            "timestamp": self.timestamp.isoformat(),
            "task_states": self.task_states,
            "context": self.context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            id=data["id"],
            graph_id=data["graph_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            task_states=data["task_states"],
            context=data["context"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class ExecutionContext:
    """Context for task execution."""
    execution_id: str
    graph: TaskGraph
    started_at: datetime
    tools: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]
    variables: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    max_parallel: int = 5
    stop_on_failure: bool = False
    checkpoint_interval: int = 60
    checkpoint_dir: Path | None = None

    def get_variable(self, name: str, default: Any = None) -> Any:
        return self.variables.get(name, default)

    def set_variable(self, name: str, value: Any) -> None:
        self.variables[name] = value


class ExecutionEngine:
    """Executes task graphs with advanced features.

    Features:
    - Parallel execution with configurable concurrency
    - Automatic checkpointing for long-running executions
    - Tool integration for task handlers
    - Progress callbacks and event streaming
    - Graceful cancellation and pause/resume
    """

    def __init__(
        self,
        checkpoint_dir: str | Path | None = None,
        default_timeout: int = 300,
    ):
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.default_timeout = default_timeout
        self._active_executions: dict[str, ExecutionContext] = {}
        self._tools: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {}
        self._progress_callbacks: list[
            Callable[[str, str, float, dict[str, Any]], Awaitable[None]]
        ] = []
        self._cancelled: set[str] = set()

    def register_tool(
        self,
        name: str,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        """Register a tool that can be used by tasks."""
        self._tools[name] = handler
        log.info("Registered tool: %s", name)

    def unregister_tool(self, name: str) -> None:
        """Unregister a tool."""
        self._tools.pop(name, None)

    def on_progress(
        self,
        callback: Callable[[str, str, float, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a progress callback.

        Callback receives: (execution_id, task_id, progress, data)
        """
        self._progress_callbacks.append(callback)

    async def execute(
        self,
        graph: TaskGraph,
        max_parallel: int = 5,
        stop_on_failure: bool = False,
        initial_context: dict[str, Any] | None = None,
        checkpoint_interval: int = 60,
    ) -> ExecutionResult:
        """Execute a task graph."""
        execution_id = str(uuid.uuid4())[:12]

        ctx = ExecutionContext(
            execution_id=execution_id,
            graph=graph,
            started_at=datetime.now(timezone.utc),
            tools=self._tools.copy(),
            variables=initial_context or {},
            max_parallel=max_parallel,
            stop_on_failure=stop_on_failure,
            checkpoint_interval=checkpoint_interval,
            checkpoint_dir=self.checkpoint_dir,
        )

        self._active_executions[execution_id] = ctx

        try:
            result = await self._execute_graph(ctx)
            return result
        finally:
            self._active_executions.pop(execution_id, None)

    async def execute_from_checkpoint(
        self,
        checkpoint: Checkpoint,
        graph: TaskGraph | None = None,
    ) -> ExecutionResult:
        """Resume execution from a checkpoint."""
        if graph is None:
            raise ValueError("Graph must be provided for checkpoint recovery")

        for task_id, state in checkpoint.task_states.items():
            if task_id in graph.nodes:
                task = graph.nodes[task_id]
                task.status = TaskStatus(state["status"])
                task.outputs = state.get("outputs", {})
                task.error = state.get("error", "")
                task.retries = state.get("retries", 0)

        ctx = ExecutionContext(
            execution_id=checkpoint.id + "_resumed",
            graph=graph,
            started_at=datetime.now(timezone.utc),
            tools=self._tools.copy(),
            variables=checkpoint.context,
            checkpoints=[checkpoint],
            checkpoint_dir=self.checkpoint_dir,
        )

        self._active_executions[ctx.execution_id] = ctx

        try:
            return await self._execute_graph(ctx)
        finally:
            self._active_executions.pop(ctx.execution_id, None)

    async def cancel(self, execution_id: str) -> bool:
        """Cancel an active execution."""
        if execution_id in self._active_executions:
            self._cancelled.add(execution_id)
            log.info("Cancellation requested for execution %s", execution_id)
            return True
        return False

    async def pause(self, execution_id: str) -> Checkpoint | None:
        """Pause an execution and return a checkpoint."""
        ctx = self._active_executions.get(execution_id)
        if not ctx:
            return None

        self._cancelled.add(execution_id)
        checkpoint = await self._create_checkpoint(ctx)
        return checkpoint

    def get_execution_status(self, execution_id: str) -> dict[str, Any] | None:
        """Get status of an active execution."""
        ctx = self._active_executions.get(execution_id)
        if not ctx:
            return None

        return {
            "execution_id": execution_id,
            "graph_id": ctx.graph.id,
            "started_at": ctx.started_at.isoformat(),
            "progress": ctx.graph.get_progress(),
            "checkpoints": len(ctx.checkpoints),
            "cancelled": execution_id in self._cancelled,
        }

    async def _execute_graph(self, ctx: ExecutionContext) -> ExecutionResult:
        """Execute the task graph."""
        graph = ctx.graph
        started_at = datetime.now(timezone.utc)

        completed_tasks = 0
        failed_tasks = 0
        skipped_tasks = 0
        errors: list[dict[str, str]] = []
        final_outputs: dict[str, Any] = {}

        last_checkpoint = datetime.now(timezone.utc)

        for task in graph.nodes.values():
            if task._handler is None and task.executor:
                tool = ctx.tools.get(task.executor)
                if tool:
                    task._handler = tool

        while True:
            if ctx.execution_id in self._cancelled:
                log.info("Execution %s cancelled", ctx.execution_id)
                break

            ready_tasks = graph.get_ready_tasks()

            if not ready_tasks:
                all_terminal = all(t.is_terminal() for t in graph.nodes.values())
                if all_terminal:
                    break

                any_running = any(
                    t.status == TaskStatus.RUNNING
                    for t in graph.nodes.values()
                )
                if not any_running:
                    log.warning("No ready tasks and nothing running - possible deadlock")
                    break

                await asyncio.sleep(0.1)
                continue

            if ctx.stop_on_failure and failed_tasks > 0:
                for task in ready_tasks:
                    task.status = TaskStatus.SKIPPED
                    skipped_tasks += 1
                continue

            semaphore = asyncio.Semaphore(ctx.max_parallel)

            async def execute_with_semaphore(task: TaskNode) -> None:
                async with semaphore:
                    await self._execute_task(ctx, task)

            batch = ready_tasks[:ctx.max_parallel * 2]
            await asyncio.gather(
                *[execute_with_semaphore(task) for task in batch],
                return_exceptions=True,
            )

            for task in batch:
                if task.status == TaskStatus.COMPLETED:
                    completed_tasks += 1
                    final_outputs[task.id] = task.outputs
                elif task.status == TaskStatus.FAILED:
                    failed_tasks += 1
                    errors.append({"task_id": task.id, "error": task.error})

            now = datetime.now(timezone.utc)
            if (now - last_checkpoint).total_seconds() >= ctx.checkpoint_interval:
                await self._create_checkpoint(ctx)
                last_checkpoint = now

        completed_at = datetime.now(timezone.utc)
        duration = (completed_at - started_at).total_seconds()

        status = "completed"
        if ctx.execution_id in self._cancelled:
            status = "cancelled"
        elif failed_tasks > 0:
            status = "partial_failure" if completed_tasks > 0 else "failed"

        self._cancelled.discard(ctx.execution_id)

        return ExecutionResult(
            plan_id=graph.id,
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
        ctx: ExecutionContext,
        task: TaskNode,
    ) -> None:
        """Execute a single task."""
        if task.status != TaskStatus.READY:
            return

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)

        await self._notify_progress(ctx.execution_id, task.id, 0.0, {"status": "started"})

        try:
            inputs = {**task.inputs}
            for dep_id in task.dependencies:
                if dep_id in ctx.graph.nodes:
                    inputs[f"dep_{dep_id}"] = ctx.graph.nodes[dep_id].outputs

            inputs["context"] = ctx.variables
            inputs["tools"] = list(ctx.tools.keys())

            if task._handler:
                try:
                    timeout = task.timeout_seconds or self.default_timeout
                    result = await asyncio.wait_for(
                        task._handler(inputs),
                        timeout=timeout,
                    )
                    task.outputs = result or {}
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Task timed out after {task.timeout_seconds}s")
            else:
                task.outputs = await self._execute_default_handler(ctx, task, inputs)

            # The default handler's tools fail closed: they return an error payload
            # instead of raising, so nothing above this point would notice. Without
            # this check a plan whose every tool refused still reported
            # status="completed", failed_tasks=0, errors=[] — the caller had no way
            # to tell "did the work" from "did nothing". Raise so the task takes the
            # normal failure path below, including retries.
            if isinstance(task.outputs, dict) and task.outputs.get("status") == "failed":
                failed = task.outputs.get("failed_tools") or []
                raise RuntimeError(
                    f"no tool executed for task {task.id}: "
                    f"{', '.join(failed) if failed else 'handler reported failure'}"
                )

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            task.progress = 1.0

            if task.outputs:
                for key, value in task.outputs.items():
                    ctx.variables[f"{task.id}_{key}"] = value

            await self._notify_progress(
                ctx.execution_id, task.id, 1.0,
                {"status": "completed", "outputs": task.outputs},
            )

            for dependent_id in task.dependents:
                if dependent_id in ctx.graph.nodes:
                    dep_task = ctx.graph.nodes[dependent_id]
                    all_deps_done = all(
                        ctx.graph.nodes[d].status == TaskStatus.COMPLETED
                        for d in dep_task.dependencies
                        if d in ctx.graph.nodes
                    )
                    if all_deps_done and dep_task.status == TaskStatus.PENDING:
                        dep_task.status = TaskStatus.READY

        except Exception as e:
            log.error("Task %s failed: %s", task.id, e)
            task.error = str(e)
            task.retries += 1

            if task.retries < task.max_retries:
                task.status = TaskStatus.READY
                log.info(
                    "Will retry task %s (attempt %d/%d)",
                    task.id, task.retries + 1, task.max_retries,
                )
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                await self._notify_progress(
                    ctx.execution_id, task.id, 0.0,
                    {"status": "failed", "error": str(e)},
                )

    async def _execute_default_handler(
        self,
        ctx: ExecutionContext,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Default handler for tasks without custom handlers."""
        required_tools = task.metadata.get("required_tools", [])

        results = {}

        for tool_name in required_tools:
            if tool_name in ctx.tools:
                tool = ctx.tools[tool_name]
                try:
                    result = await tool({
                        "task_id": task.id,
                        "task_type": task.task_type.value,
                        "inputs": inputs,
                    })
                    results[tool_name] = result
                except Exception as e:
                    log.error("Tool %s failed: %s", tool_name, e)
                    results[tool_name] = {"error": str(e)}

        # Tools that fail closed (see create_default_tools) hand back an error
        # payload rather than raising, so surface that here: a task whose every
        # tool refused to run was not "executed".
        failed_tools = [
            name
            for name, result in results.items()
            if isinstance(result, dict) and result.get("error")
        ]

        return {
            "status": "failed" if failed_tools and len(failed_tools) == len(results) else "executed",
            "task_type": task.task_type.value,
            "tool_results": results,
            "failed_tools": failed_tools,
            "inputs_received": list(inputs.keys()),
        }

    async def _create_checkpoint(self, ctx: ExecutionContext) -> Checkpoint:
        """Create a checkpoint of current execution state."""
        task_states = {}
        for task_id, task in ctx.graph.nodes.items():
            task_states[task_id] = {
                "status": task.status.value,
                "outputs": task.outputs,
                "error": task.error,
                "retries": task.retries,
                "progress": task.progress,
            }

        checkpoint = Checkpoint(
            id=f"cp_{ctx.execution_id}_{len(ctx.checkpoints)}",
            graph_id=ctx.graph.id,
            timestamp=datetime.now(timezone.utc),
            task_states=task_states,
            context=ctx.variables.copy(),
            metadata={
                "execution_id": ctx.execution_id,
                "checkpoint_number": len(ctx.checkpoints),
            },
        )

        ctx.checkpoints.append(checkpoint)

        if ctx.checkpoint_dir:
            await self._save_checkpoint(checkpoint, ctx.checkpoint_dir)

        log.info("Created checkpoint %s", checkpoint.id)
        return checkpoint

    async def _save_checkpoint(
        self,
        checkpoint: Checkpoint,
        directory: Path,
    ) -> None:
        """Save checkpoint to disk."""
        directory.mkdir(parents=True, exist_ok=True)
        filepath = directory / f"{checkpoint.id}.json"

        def write_checkpoint() -> None:
            with open(filepath, "w") as f:
                json.dump(checkpoint.to_dict(), f, indent=2)

        await asyncio.to_thread(write_checkpoint)

    async def load_checkpoint(self, filepath: Path) -> Checkpoint:
        """Load a checkpoint from disk."""
        def read_checkpoint() -> dict[str, Any]:
            with open(filepath) as f:
                return json.load(f)

        data = await asyncio.to_thread(read_checkpoint)
        return Checkpoint.from_dict(data)

    async def _notify_progress(
        self,
        execution_id: str,
        task_id: str,
        progress: float,
        data: dict[str, Any],
    ) -> None:
        """Notify progress callbacks."""
        for callback in self._progress_callbacks:
            try:
                await callback(execution_id, task_id, progress, data)
            except Exception as e:
                log.error("Progress callback error: %s", e)


def _tool_not_configured(tool: str, missing: str) -> dict[str, Any]:
    """Fail-closed payload for a default tool that has no backend behind it.

    These handlers are registered by the planning API (app/planning/api_router.py),
    so their output is what a caller sees for a task. "analyzed"/"sent"/"success"
    from a handler that contacted nothing is a plan step reported as done when
    it never ran, so outside demo mode they report the missing wiring instead.
    """
    log.warning("Planning tool '%s' not executed: %s", tool, missing)
    return {
        "status": "failed",
        "error": (
            f"{missing}; refusing to report success for work that was not performed"
        ),
    }


def create_default_tools() -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    """Create default tool handlers.

    None of these are wired to a real backend: they are placeholders so a task
    graph can be walked end to end. They only produce a result in demo mode.
    """

    def _demo() -> bool:
        return get_settings().copilot_demo_mode

    async def logs_tool(inputs: dict[str, Any]) -> dict[str, Any]:
        """Log analysis tool (no log source wired up)."""
        if not _demo():
            return _tool_not_configured("logs", "no log source is configured")
        log.warning("COPILOT_DEMO_MODE=true - returning a SIMULATED logs result")
        return {
            "status": "analyzed",
            "simulated": True,
            "log_entries": 0,
            "patterns_found": [],
            "time_range": "last_1h",
        }

    async def metrics_tool(inputs: dict[str, Any]) -> dict[str, Any]:
        """Metrics analysis tool (no metrics source wired up)."""
        if not _demo():
            return _tool_not_configured("metrics", "no metrics source is configured")
        log.warning("COPILOT_DEMO_MODE=true - returning a SIMULATED metrics result")
        return {
            "status": "analyzed",
            "simulated": True,
            "metrics_checked": [],
            "anomalies": [],
            "time_range": "last_1h",
        }

    async def traces_tool(inputs: dict[str, Any]) -> dict[str, Any]:
        """Trace analysis tool (no trace source wired up)."""
        if not _demo():
            return _tool_not_configured("traces", "no trace source is configured")
        log.warning("COPILOT_DEMO_MODE=true - returning a SIMULATED traces result")
        return {
            "status": "analyzed",
            "simulated": True,
            "traces_sampled": 0,
            "slow_spans": [],
            "error_spans": [],
        }

    async def ci_cd_tool(inputs: dict[str, Any]) -> dict[str, Any]:
        """CI/CD tool (no pipeline runner wired up)."""
        if not _demo():
            return _tool_not_configured("ci_cd", "no CI/CD provider is configured")
        log.warning("COPILOT_DEMO_MODE=true - returning a SIMULATED CI/CD result")
        return {
            "status": "executed",
            "simulated": True,
            "pipeline": "default",
            "result": "success (simulated)",
        }

    async def notification_tool(inputs: dict[str, Any]) -> dict[str, Any]:
        """Notification tool (no notifier wired up)."""
        if not _demo():
            return _tool_not_configured(
                "notification", "no notification channel is configured"
            )
        log.warning("COPILOT_DEMO_MODE=true - no notification was actually sent")
        return {
            "status": "sent",
            "simulated": True,
            "channels": ["slack"],
            "recipients": [],
        }

    return {
        "logs": logs_tool,
        "metrics": metrics_tool,
        "traces": traces_tool,
        "ci_cd": ci_cd_tool,
        "notification": notification_tool,
    }
