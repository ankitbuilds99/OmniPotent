"""DAG-based Task Execution Engine.

Advanced features:
1. Topological sort with parallel level detection
2. Dynamic replanning on failures
3. Conditional branching
4. Resource-aware scheduling
5. Checkpoint/resume capability
6. Priority queuing
7. Real integrations with Kubernetes, Prometheus, LLM
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import operator
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

log = logging.getLogger("omnipotent.coordinator.dag_executor")


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp written by `serialize`, tolerating None and junk.

    A stored execution written by an older version must not take the listing
    endpoint down with a ValueError. UTC is assumed when no offset is stored.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        log.warning("unparseable timestamp %r in stored DAG execution", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json_safe(value: Any) -> Any:
    """Coerce a task output to something a JSON column can hold.

    `TaskExecution.output` is typed `Any` and comes from a handler, so it is not
    guaranteed serialisable. Storing a repr of the odd exotic value keeps the
    rest of the execution readable; raising here would lose the whole record over
    one field nothing indexes.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"unserialisable": repr(value)[:500]}
    return value


_CONDITION_COMPARATORS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _eval_condition_node(node: ast.expr) -> Any:
    """Evaluate the AST of a task condition, admitting only literals,
    comparisons, and/or/not — anything else raises.

    Task conditions come from DAG definitions that may embed model- or
    request-supplied strings, so this is a security boundary: no names,
    attributes, subscripts or calls.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [_eval_condition_node(e) for e in node.elts]
        return set(items) if isinstance(node, ast.Set) else items
    if isinstance(node, ast.Dict):
        return {
            _eval_condition_node(k): _eval_condition_node(v)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_condition_node(node.operand)
        if isinstance(node.op, ast.USub):
            value = _eval_condition_node(node.operand)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return -value
        raise ValueError(f"disallowed unary op in condition: {ast.dump(node)}")
    if isinstance(node, ast.BoolOp):
        values = (_eval_condition_node(v) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_condition_node(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            fn = _CONDITION_COMPARATORS.get(type(op))
            if fn is None:
                raise ValueError(f"disallowed comparator in condition: {type(op).__name__}")
            right = _eval_condition_node(comparator)
            if not fn(left, right):
                return False
            left = right
        return True
    raise ValueError(f"disallowed expression in condition: {type(node).__name__}")


class ExecutionState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_CONDITION = "waiting_condition"


class TaskPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass
class ResourceRequirements:
    """Resource requirements for a task."""
    cpu_cores: float = 1.0
    memory_mb: int = 512
    gpu: bool = False
    network_bandwidth_mbps: int = 100
    exclusive: bool = False


@dataclass
class RetryPolicy:
    """Retry policy for failed tasks."""
    max_attempts: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    retry_on_exceptions: list[str] = field(default_factory=lambda: ["*"])

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        delay = self.initial_delay_seconds * (self.exponential_base ** attempt)
        return min(delay, self.max_delay_seconds)


@dataclass
class TaskNode:
    """A node in the task DAG."""
    id: str
    name: str
    handler: str

    depends_on: list[str] = field(default_factory=list)
    input_params: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 300
    retry_policy: RetryPolicy | None = None
    priority: TaskPriority = TaskPriority.NORMAL

    run_condition: str | None = None
    skip_condition: str | None = None

    on_success: str | None = None
    on_failure: str | None = None
    branches: dict[str, str] = field(default_factory=dict)

    resources: ResourceRequirements | None = None

    requires_approval: bool = False
    approval_timeout_minutes: int = 60
    is_reversible: bool = True
    rollback_handler: str | None = None

    description: str = ""
    tags: list[str] = field(default_factory=list)
    estimated_duration_seconds: int = 60
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "handler": self.handler,
            "depends_on": self.depends_on,
            "priority": self.priority.name,
            "timeout_seconds": self.timeout_seconds,
            "requires_approval": self.requires_approval,
            "description": self.description,
            "tags": self.tags,
        }


@dataclass
class TaskExecution:
    """Execution state and results for a task."""
    task_id: str
    state: ExecutionState = ExecutionState.PENDING

    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    attempt: int = 0
    max_attempts: int = 1

    output: Any = None
    error: str | None = None
    error_traceback: str | None = None

    chosen_branch: str | None = None

    duration_ms: int | None = None
    resource_usage: dict[str, float] | None = None

    @property
    def simulated(self) -> bool:
        """Whether this task reported that it did not really do anything.

        The remediation handlers set `simulated` in their output when no cluster
        is configured, and `to_dict()` dropped `output` entirely — so a scale
        that never happened was serialized as a task in state `completed`,
        indistinguishable from one that rolled out. That is the single most
        misleading thing this API could say, so it is lifted out of the output
        blob and reported in its own right.
        """
        return bool(isinstance(self.output, dict) and self.output.get("simulated"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "attempt": self.attempt,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "simulated": self.simulated,
        }

    def serialize(self) -> dict[str, Any]:
        """Full state, for storage. `to_dict` is the API response shape and is
        deliberately lossy — it omits outputs, tracebacks and resource usage."""
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "output": _json_safe(self.output),
            "error": self.error,
            "error_traceback": self.error_traceback,
            "chosen_branch": self.chosen_branch,
            "duration_ms": self.duration_ms,
            "resource_usage": self.resource_usage,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> TaskExecution:
        return cls(
            task_id=data["task_id"],
            state=ExecutionState(data.get("state", ExecutionState.PENDING.value)),
            queued_at=_parse_dt(data.get("queued_at")),
            started_at=_parse_dt(data.get("started_at")),
            completed_at=_parse_dt(data.get("completed_at")),
            attempt=data.get("attempt", 0),
            max_attempts=data.get("max_attempts", 1),
            output=data.get("output"),
            error=data.get("error"),
            error_traceback=data.get("error_traceback"),
            chosen_branch=data.get("chosen_branch"),
            duration_ms=data.get("duration_ms"),
            resource_usage=data.get("resource_usage"),
        )


@dataclass
class DAGExecution:
    """Complete DAG execution state."""
    id: str
    dag_id: str

    state: ExecutionState = ExecutionState.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None

    task_executions: dict[str, TaskExecution] = field(default_factory=dict)

    execution_levels: list[list[str]] = field(default_factory=list)
    current_level: int = 0

    initial_context: dict[str, Any] = field(default_factory=dict)
    accumulated_context: dict[str, Any] = field(default_factory=dict)

    checkpoint_id: str | None = None
    resumed_from: str | None = None

    # Why the run as a whole did not proceed — a rejected checkpoint, an
    # unreachable handler registry — as opposed to a task that ran and failed.
    # Without it, a DAG that never got off the ground was indistinguishable
    # from one still working, for as long as anyone cared to poll.
    error: str | None = None
    # Tenant scope used by the durable approval control path. It is runtime
    # metadata and is deliberately not serialized into checkpoint payloads.
    org_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dag_id": self.dag_id,
            "state": self.state.value,
            "error": self.error,
            "current_level": self.current_level,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "task_executions": {
                tid: te.to_dict() for tid, te in self.task_executions.items()
            },
            # A rollup so a caller reading the top level does not have to walk
            # every task to find out that nothing was actually changed.
            "simulated_tasks": sorted(
                tid for tid, te in self.task_executions.items() if te.simulated
            ),
            "checkpoint_id": self.checkpoint_id,
        }

    def serialize(self) -> dict[str, Any]:
        """Full state, for storage — including the accumulated context and level
        plan that `to_dict` omits and that a resume would need."""
        return {
            "id": self.id,
            "dag_id": self.dag_id,
            "state": self.state.value,
            # Carried through storage as well as `to_dict`: a run that died
            # before any task started has nothing else to explain itself with,
            # and dropping it here made the reason survive only until the
            # process that logged it went away.
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "task_executions": {
                tid: te.serialize() for tid, te in self.task_executions.items()
            },
            "execution_levels": self.execution_levels,
            "current_level": self.current_level,
            "initial_context": _json_safe(self.initial_context),
            "accumulated_context": _json_safe(self.accumulated_context),
            "checkpoint_id": self.checkpoint_id,
            "resumed_from": self.resumed_from,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> DAGExecution:
        return cls(
            id=data["id"],
            dag_id=data["dag_id"],
            state=ExecutionState(data.get("state", ExecutionState.PENDING.value)),
            error=data.get("error"),
            started_at=_parse_dt(data.get("started_at")),
            completed_at=_parse_dt(data.get("completed_at")),
            task_executions={
                tid: TaskExecution.deserialize(te)
                for tid, te in (data.get("task_executions") or {}).items()
            },
            execution_levels=[list(level) for level in data.get("execution_levels") or []],
            current_level=data.get("current_level", 0),
            initial_context=dict(data.get("initial_context") or {}),
            accumulated_context=dict(data.get("accumulated_context") or {}),
            checkpoint_id=data.get("checkpoint_id"),
            resumed_from=data.get("resumed_from"),
        )


class TaskDAG:
    """Directed Acyclic Graph of tasks with real dependency management."""

    def __init__(self, dag_id: str, name: str):
        self.id = dag_id
        self.name = name
        self.tasks: dict[str, TaskNode] = {}
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._reverse_adjacency: dict[str, set[str]] = defaultdict(set)

    def add_task(self, task: TaskNode) -> TaskDAG:
        """Add a task to the DAG.

        A dependency that is not in the graph *yet* is fine: the edge is
        recorded and `validate()` reports it if it is still missing once the
        graph is complete. This used to raise, which made insertion order part
        of the contract — a DAG is a set of nodes and edges, not a list, and
        nothing about `{"tasks": [...]}` in a request body says the caller
        sorted it topologically. It also broke the replanner, which copies
        downstream tasks into a recovery graph whose upstreams it never copies.

        Missing dependencies are still an error; `validate()` is where it is
        raised, in one place, with all of them listed rather than the first.
        """
        if task.id in self.tasks:
            raise ValueError(f"Task {task.id} already exists")

        self.tasks[task.id] = task

        for dep in task.depends_on:
            self._adjacency[dep].add(task.id)
            self._reverse_adjacency[task.id].add(dep)

        return self

    def add_dependency(self, from_task: str, to_task: str) -> TaskDAG:
        """Add a dependency between tasks."""
        if from_task not in self.tasks:
            raise ValueError(f"Task {from_task} not found")
        if to_task not in self.tasks:
            raise ValueError(f"Task {to_task} not found")

        self._adjacency[from_task].add(to_task)
        self._reverse_adjacency[to_task].add(from_task)

        if to_task not in self.tasks[to_task].depends_on:
            self.tasks[to_task].depends_on.append(from_task)

        if self._has_cycle():
            self._adjacency[from_task].discard(to_task)
            self._reverse_adjacency[to_task].discard(from_task)
            self.tasks[to_task].depends_on.remove(from_task)
            raise ValueError("Adding dependency would create a cycle")

        return self

    def validate(self) -> list[str]:
        """Validate the DAG structure."""
        errors = []

        if self._has_cycle():
            errors.append("DAG contains cycles")

        for task_id, task in self.tasks.items():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    errors.append(f"Task {task_id} depends on non-existent task {dep}")

        if self.tasks:
            root_tasks = [
                tid for tid, task in self.tasks.items()
                if not task.depends_on
            ]
            if not root_tasks:
                errors.append("No root tasks found (all tasks have dependencies)")

        return errors

    def _has_cycle(self) -> bool:
        """Check for cycles using DFS."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for neighbor in self._adjacency[node_id]:
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self.tasks:
            if node_id not in visited:
                if dfs(node_id):
                    return True

        return False

    def get_execution_levels(self) -> list[list[str]]:
        """Get tasks grouped by execution level using Kahn's algorithm."""
        in_degree: dict[str, int] = {tid: 0 for tid in self.tasks}

        for task_id, task in self.tasks.items():
            for dep in task.depends_on:
                if dep in self.tasks:
                    in_degree[task_id] += 1

        levels: list[list[str]] = []
        remaining = set(self.tasks.keys())

        while remaining:
            level = [
                tid for tid in remaining
                if in_degree[tid] == 0
            ]

            if not level:
                log.warning("Potential deadlock detected in DAG")
                break

            level.sort(key=lambda tid: (
                self.tasks[tid].priority.value,
                self.tasks[tid].name,
            ))

            levels.append(level)

            for tid in level:
                remaining.remove(tid)
                for successor in self._adjacency[tid]:
                    if successor in remaining:
                        in_degree[successor] -= 1

        return levels

    def get_critical_path(self) -> tuple[list[str], int]:
        """Find the critical path (longest execution time)."""
        if not self.tasks:
            return [], 0

        dist: dict[str, int] = {tid: 0 for tid in self.tasks}
        parent: dict[str, str | None] = {tid: None for tid in self.tasks}

        levels = self.get_execution_levels()
        for level in levels:
            for task_id in level:
                task = self.tasks[task_id]
                for successor in self._adjacency[task_id]:
                    new_dist = dist[task_id] + task.estimated_duration_seconds
                    if new_dist > dist[successor]:
                        dist[successor] = new_dist
                        parent[successor] = task_id

        end_task = max(dist.keys(), key=lambda tid: dist[tid])
        total_duration = dist[end_task] + self.tasks[end_task].estimated_duration_seconds

        path = []
        current: str | None = end_task
        while current is not None:
            path.append(current)
            current = parent[current]

        return list(reversed(path)), total_duration

    def get_affected_tasks(self, failed_task_id: str) -> set[str]:
        """Get all tasks that depend on the failed task."""
        affected: set[str] = set()
        queue = list(self._adjacency[failed_task_id])

        while queue:
            task_id = queue.pop(0)
            if task_id not in affected:
                affected.add(task_id)
                queue.extend(self._adjacency[task_id])

        return affected

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tasks": {tid: task.to_dict() for tid, task in self.tasks.items()},
            "execution_levels": self.get_execution_levels(),
        }


class DAGExecutor:
    """Executes task DAGs with advanced scheduling and real integrations."""

    def __init__(
        self,
        handlers: dict[str, Callable[[TaskNode, dict[str, Any]], Awaitable[Any]]] | None = None,
        redis_client: Any = None,
        max_parallel: int = 10,
        resource_pool: Any = None,
    ):
        self.handlers = handlers or {}
        self.redis = redis_client
        self.max_parallel = max_parallel
        self.resource_pool = resource_pool

        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._approval_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Keyed on (execution_id, task_id): a task id alone is shared by every
        # concurrent run of the same DAG. See `approve_task`.
        self._pending_approvals: dict[tuple[str, str], asyncio.Future[bool]] = {}

        self._on_task_start: (
            Callable[[TaskNode, DAGExecution], Awaitable[None]] | None
        ) = None
        self._on_task_complete: (
            Callable[[TaskNode, DAGExecution, Any], Awaitable[None]] | None
        ) = None
        self._on_task_failed: (
            Callable[[TaskNode, DAGExecution, str], Awaitable[None]] | None
        ) = None
        self._on_approval_needed: (
            Callable[[TaskNode, DAGExecution], Awaitable[None]] | None
        ) = None

    def register_handler(
        self,
        name: str,
        handler: Callable[[TaskNode, dict[str, Any]], Awaitable[Any]],
    ) -> None:
        """Register a task handler."""
        self.handlers[name] = handler
        log.info("Registered handler: %s", name)

    def on_task_start(
        self,
        callback: Callable[[TaskNode, DAGExecution], Awaitable[None]],
    ) -> DAGExecutor:
        self._on_task_start = callback
        return self

    def on_task_complete(
        self,
        callback: Callable[[TaskNode, DAGExecution, Any], Awaitable[None]],
    ) -> DAGExecutor:
        self._on_task_complete = callback
        return self

    def on_task_failed(
        self,
        callback: Callable[[TaskNode, DAGExecution, str], Awaitable[None]],
    ) -> DAGExecutor:
        self._on_task_failed = callback
        return self

    def on_approval_needed(
        self,
        callback: Callable[[TaskNode, DAGExecution], Awaitable[None]],
    ) -> DAGExecutor:
        self._on_approval_needed = callback
        return self

    async def execute(
        self,
        dag: TaskDAG,
        context: dict[str, Any] | None = None,
        execution_id: str | None = None,
        resume_from: str | None = None,
        org_id: str | None = None,
    ) -> DAGExecution:
        """Execute a DAG."""
        errors = dag.validate()
        if errors:
            raise ValueError(f"Invalid DAG: {errors}")

        if resume_from:
            execution = await self._load_checkpoint(resume_from)
            if not execution:
                raise ValueError(f"Checkpoint {resume_from} not found")
            self._restore_level_plan(dag, execution)
            if execution_id:
                # The caller that handed us an id has already written a record
                # under it and is polling it. Resumes used to keep the
                # checkpoint's own id, so the run was filed under the *old* id
                # and the row the caller was watching sat at "running" for ever.
                # `resumed_from` still says which checkpoint this continues.
                execution.id = execution_id
        else:
            execution = DAGExecution(
                id=execution_id or f"exec_{uuid.uuid4().hex[:8]}",
                dag_id=dag.id,
                initial_context=context or {},
                accumulated_context=dict(context or {}),
                execution_levels=dag.get_execution_levels(),
            )

        execution.state = ExecutionState.RUNNING
        execution.org_id = org_id
        execution.started_at = datetime.now(timezone.utc)

        try:
            await self._execute_dag(dag, execution)

            failed = any(
                te.state == ExecutionState.FAILED
                for te in execution.task_executions.values()
            )
            execution.state = ExecutionState.FAILED if failed else ExecutionState.COMPLETED

        except asyncio.CancelledError:
            execution.state = ExecutionState.CANCELLED
            raise
        except Exception as e:
            log.error("DAG execution failed: %s", e)
            execution.state = ExecutionState.FAILED
            raise
        finally:
            execution.completed_at = datetime.now(timezone.utc)
            await self._save_checkpoint(execution)

        return execution

    @staticmethod
    def _restore_level_plan(dag: TaskDAG, execution: DAGExecution) -> None:
        """Give a resumed execution back the level plan it was part-way through.

        Checkpoints never carried `execution_levels`, so a resumed run arrived
        with an empty plan and `_execute_dag`'s
        `range(current_level, len(execution_levels))` was an empty range: not one
        handler was called, every task kept whatever state it had when the run
        was interrupted, and the execution was recorded as COMPLETED. An operator
        resuming an interrupted remediation was told it had finished while
        nothing at all had been done.

        Checkpoints now persist the plan. Ones written before that carry none, so
        it is recomputed from the DAG supplied with the resume request — the same
        deterministic `get_execution_levels()` that produced it in the first
        place.

        A plan that does not describe that DAG is refused instead of run: it
        would either fail on a task id the graph does not have or, worse, quietly
        leave the tasks it omits unexecuted while the run still reported success.
        """
        if not execution.execution_levels:
            execution.execution_levels = dag.get_execution_levels()

        planned = {task_id for level in execution.execution_levels for task_id in level}
        known = set(dag.tasks)
        if planned != known:
            details = []
            if planned - known:
                details.append(
                    f"plan references tasks the DAG does not have: {sorted(planned - known)}"
                )
            if known - planned:
                details.append(
                    f"DAG tasks the plan would never run: {sorted(known - planned)}"
                )
            raise ValueError(
                f"Checkpoint {execution.resumed_from} does not match DAG "
                f"{dag.id}: " + "; ".join(details)
            )

        if not execution.execution_levels:
            raise ValueError(
                f"Checkpoint {execution.resumed_from} cannot be resumed: no "
                f"execution levels could be rebuilt for DAG {dag.id}, so the "
                "resumed run would do nothing"
            )

    async def _execute_dag(self, dag: TaskDAG, execution: DAGExecution) -> None:
        """Execute DAG level by level."""
        for level_idx in range(execution.current_level, len(execution.execution_levels)):
            if execution.org_id:
                from app.coordinator import store
                if await store.is_cancel_requested(execution.org_id, execution.id):
                    execution.state = ExecutionState.CANCELLED
                    execution.error = "Cancellation requested by operator"
                    return
            level = execution.execution_levels[level_idx]
            execution.current_level = level_idx

            pending_tasks = [
                task_id for task_id in level
                if task_id not in execution.task_executions
                or execution.task_executions[task_id].state not in [
                    ExecutionState.COMPLETED,
                    ExecutionState.SKIPPED,
                ]
            ]

            if not pending_tasks:
                continue

            log.info("Executing level %d with %d tasks", level_idx, len(pending_tasks))
            await self._execute_level(dag, execution, pending_tasks)

            if self._has_blocking_failure(dag, execution, level):
                for task_id in level:
                    task_exec = execution.task_executions.get(task_id)
                    if task_exec and task_exec.state == ExecutionState.FAILED:
                        affected = dag.get_affected_tasks(task_id)
                        for affected_id in affected:
                            if affected_id not in execution.task_executions:
                                execution.task_executions[affected_id] = TaskExecution(
                                    task_id=affected_id,
                                    state=ExecutionState.BLOCKED,
                                    error=f"Blocked by failed task: {task_id}",
                                )
                break

            await self._save_checkpoint(execution)

    async def _execute_level(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        task_ids: list[str],
    ) -> None:
        """Execute all tasks in a level with parallelism and priority."""
        sorted_tasks = sorted(
            task_ids,
            key=lambda t: dag.tasks[t].priority.value,
        )

        async_tasks = []
        for task_id in sorted_tasks:
            task_node = dag.tasks[task_id]

            if self._should_skip(task_node, execution):
                execution.task_executions[task_id] = TaskExecution(
                    task_id=task_id,
                    state=ExecutionState.SKIPPED,
                    completed_at=datetime.now(timezone.utc),
                )
                continue

            if not self._dependencies_satisfied(task_node, execution):
                execution.task_executions[task_id] = TaskExecution(
                    task_id=task_id,
                    state=ExecutionState.BLOCKED,
                    error="Dependencies not satisfied",
                )
                continue

            async_tasks.append(
                self._execute_task_with_semaphore(dag, execution, task_node)
            )

        if async_tasks:
            await asyncio.gather(*async_tasks, return_exceptions=True)

    async def _execute_task_with_semaphore(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        task: TaskNode,
    ) -> None:
        """Execute a task with concurrency control."""
        async with self._semaphore:
            resource_lock = None

            if task.resources and self.resource_pool:
                resource_lock = await self.resource_pool.acquire(
                    task.resources,
                    timeout=task.timeout_seconds,
                )
                if not resource_lock:
                    execution.task_executions[task.id] = TaskExecution(
                        task_id=task.id,
                        state=ExecutionState.FAILED,
                        error="Could not acquire required resources",
                    )
                    return

            try:
                await self._execute_task(dag, execution, task)
            finally:
                if resource_lock and self.resource_pool:
                    await self.resource_pool.release(resource_lock)

    async def _execute_task(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        task: TaskNode,
    ) -> None:
        """Execute a single task with retry logic."""
        task_execution = TaskExecution(
            task_id=task.id,
            state=ExecutionState.RUNNING,
            queued_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            max_attempts=task.retry_policy.max_attempts if task.retry_policy else 1,
        )
        execution.task_executions[task.id] = task_execution

        if task.requires_approval:
            approved = await self._request_approval(task, execution)
            if not approved:
                task_execution.state = ExecutionState.SKIPPED
                task_execution.error = "Approval denied or timed out"
                return

        if self._on_task_start:
            await self._on_task_start(task, execution)

        input_data = self._build_task_input(dag, execution, task)

        handler = self.handlers.get(task.handler)
        if not handler:
            task_execution.state = ExecutionState.FAILED
            task_execution.error = f"Handler not found: {task.handler}"
            return

        retry_policy = task.retry_policy or RetryPolicy(max_attempts=1)
        last_error = ""

        for attempt in range(retry_policy.max_attempts):
            task_execution.attempt = attempt + 1

            try:
                output = await asyncio.wait_for(
                    handler(task, input_data),
                    timeout=task.timeout_seconds,
                )

                task_execution.state = ExecutionState.COMPLETED
                task_execution.output = output
                task_execution.completed_at = datetime.now(timezone.utc)
                if task_execution.started_at:
                    task_execution.duration_ms = int(
                        (task_execution.completed_at - task_execution.started_at).total_seconds() * 1000
                    )

                if task.branches and isinstance(output, dict):
                    branch_key = output.get("_branch")
                    if branch_key and branch_key in task.branches:
                        task_execution.chosen_branch = branch_key

                execution.accumulated_context[f"task_{task.id}"] = output

                if self._on_task_complete:
                    await self._on_task_complete(task, execution, output)

                return

            except asyncio.TimeoutError:
                last_error = f"Timeout after {task.timeout_seconds}s"
            except Exception as e:
                last_error = str(e)
                log.warning("Task %s attempt %d failed: %s", task.id, attempt + 1, e)

                if not self._should_retry(e, retry_policy):
                    break

            if attempt < retry_policy.max_attempts - 1:
                delay = retry_policy.get_delay(attempt)
                log.info("Retrying task %s in %.1fs", task.id, delay)
                await asyncio.sleep(delay)

        task_execution.state = ExecutionState.FAILED
        task_execution.error = last_error
        task_execution.completed_at = datetime.now(timezone.utc)

        if self._on_task_failed:
            await self._on_task_failed(task, execution, last_error)

    def _build_task_input(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        task: TaskNode,
    ) -> dict[str, Any]:
        """Build input for a task from dependencies and context."""
        input_data = dict(execution.accumulated_context)
        input_data.update(task.input_params)

        for dep_id in task.depends_on:
            dep_execution = execution.task_executions.get(dep_id)
            if dep_execution and dep_execution.output:
                input_data[f"dep_{dep_id}"] = dep_execution.output

        return input_data

    def _should_skip(self, task: TaskNode, execution: DAGExecution) -> bool:
        """Check if task should be skipped."""
        if task.skip_condition:
            try:
                return self._evaluate_condition(
                    task.skip_condition,
                    execution.accumulated_context,
                )
            except Exception:
                return False
        return False

    def _should_retry(self, exception: Exception, policy: RetryPolicy) -> bool:
        """Check if exception should trigger retry."""
        if "*" in policy.retry_on_exceptions:
            return True

        exception_name = type(exception).__name__
        return exception_name in policy.retry_on_exceptions

    def _dependencies_satisfied(
        self,
        task: TaskNode,
        execution: DAGExecution,
    ) -> bool:
        """Check if all dependencies are satisfied."""
        for dep_id in task.depends_on:
            dep_exec = execution.task_executions.get(dep_id)
            if not dep_exec or dep_exec.state != ExecutionState.COMPLETED:
                return False
        return True

    def _has_blocking_failure(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        level: list[str],
    ) -> bool:
        """Check if any task in level has a blocking failure."""
        for task_id in level:
            task_exec = execution.task_executions.get(task_id)
            if task_exec and task_exec.state == ExecutionState.FAILED:
                if dag.get_affected_tasks(task_id):
                    return True
        return False

    def _evaluate_condition(self, condition: str, context: dict[str, Any]) -> bool:
        """Evaluate a condition expression without eval().

        Context values are spliced in as repr() literals and the result is
        parsed to an AST that only admits literals, comparisons and boolean
        operators — a condition (or a context value smuggled into one) can
        never reach attribute access, subscripts, calls or imports.
        """
        safe_context = {
            k: v for k, v in context.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }

        for key, value in safe_context.items():
            condition = condition.replace(f"${{{key}}}", repr(value))

        try:
            tree = ast.parse(condition, mode="eval")
            return bool(_eval_condition_node(tree.body))
        except Exception:
            return False

    async def _request_approval(
        self,
        task: TaskNode,
        execution: DAGExecution,
    ) -> bool:
        """Request human approval for a task."""
        if self._on_approval_needed:
            await self._on_approval_needed(task, execution)

        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending_approvals[(execution.id, task.id)] = future
        await self._save_checkpoint(execution)
        if execution.org_id:
            # Make the waiting task visible and approvable through the shared
            # database before the worker blocks on its local future.
            from app.coordinator import store
            await store.save_execution(execution.org_id, execution)

        try:
            # The future is a low-latency local fast path. Polling the durable
            # decision makes approval work when the API request lands on a
            # different replica than the worker running this task.
            deadline = asyncio.get_running_loop().time() + task.approval_timeout_minutes * 60
            while True:
                if future.done():
                    return future.result()
                if execution.org_id:
                    from app.coordinator import store
                    decision = await store.get_approval_decision(
                        execution.org_id, execution.id, task.id
                    )
                    if decision is not None:
                        return decision
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(asyncio.shield(future), min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_approvals.pop((execution.id, task.id), None)

    async def approve_task(
        self, execution_id: str, task_id: str, approved: bool = True
    ) -> bool:
        """Approve or reject one execution's pending task.

        Keyed on the execution as well as the task. Task ids come from the DAG
        definition, not from the run, so two concurrent executions of the same
        DAG — the normal case when two incidents hit the same service — both
        park on a task called `restart_service`, and a single approval of it
        used to release *both*. One human decision, two production restarts,
        one of which nobody was asked about.
        """
        future = self._pending_approvals.get((execution_id, task_id))
        if future and not future.done():
            future.set_result(approved)
            return True
        return False

    def pending_approvals(self, execution_id: str | None = None) -> list[dict[str, str]]:
        """The approvals currently waiting on a human, newest state as-is.

        Exposed because a DAG blocked on approval is otherwise only discoverable
        by knowing the task id in advance.
        """
        return [
            {"execution_id": exec_id, "task_id": task_id}
            for (exec_id, task_id), future in self._pending_approvals.items()
            if not future.done() and (execution_id is None or exec_id == execution_id)
        ]

    async def _save_checkpoint(self, execution: DAGExecution) -> None:
        """Save execution state for recovery."""
        ts = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        checkpoint_id = f"checkpoint_{execution.id}_{ts}"
        execution.checkpoint_id = checkpoint_id

        checkpoint_data = {
            "execution_id": execution.id,
            "dag_id": execution.dag_id,
            "state": execution.state.value,
            # The level plan is part of the resume state, not a derived detail:
            # a checkpoint without it restores an execution that has no work to
            # iterate over and therefore "completes" instantly without running
            # anything. `current_level` is only meaningful as an index into it.
            "execution_levels": [list(level) for level in execution.execution_levels],
            "current_level": execution.current_level,
            # The context the run was started with, kept for the same reason: a
            # resumed execution that restored an empty one overwrote the stored
            # record's context with `{}`, losing the incident it was about.
            "initial_context": self._serialize_context(execution.initial_context),
            "accumulated_context": self._serialize_context(execution.accumulated_context),
            "task_executions": {
                task_id: {
                    "state": te.state.value,
                    "output": self._serialize_context(te.output) if te.output else None,
                    "error": te.error,
                    "attempt": te.attempt,
                }
                for task_id, te in execution.task_executions.items()
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.redis:
            key = f"dag_checkpoint:{execution.id}"
            await self.redis.set(key, json.dumps(checkpoint_data))
            await self.redis.expire(key, 86400 * 7)

            await self.redis.lpush(
                f"dag_checkpoints:{execution.id}:history",
                checkpoint_id,
            )
            await self.redis.ltrim(f"dag_checkpoints:{execution.id}:history", 0, 99)

    async def _load_checkpoint(self, checkpoint_id: str) -> DAGExecution | None:
        """Load execution state from checkpoint."""
        if not self.redis:
            return None

        parts = checkpoint_id.split("_")
        if len(parts) >= 2:
            execution_id = "_".join(parts[1:-1])
        else:
            execution_id = checkpoint_id

        key = f"dag_checkpoint:{execution_id}"
        data = await self.redis.get(key)

        if not data:
            return None

        checkpoint = json.loads(data)

        execution = DAGExecution(
            id=checkpoint["execution_id"],
            dag_id=checkpoint["dag_id"],
            state=ExecutionState(checkpoint["state"]),
            # Restored rather than left to default to `[]`, which is what made a
            # resume iterate over nothing. Checkpoints written before the plan
            # was persisted have no key here; `_restore_level_plan` recomputes
            # it from the DAG for those.
            execution_levels=[
                list(level) for level in (checkpoint.get("execution_levels") or [])
            ],
            current_level=checkpoint["current_level"],
            initial_context=dict(checkpoint.get("initial_context") or {}),
            accumulated_context=checkpoint["accumulated_context"],
            resumed_from=checkpoint_id,
        )

        for task_id, te_data in checkpoint["task_executions"].items():
            execution.task_executions[task_id] = TaskExecution(
                task_id=task_id,
                state=ExecutionState(te_data["state"]),
                output=te_data.get("output"),
                error=te_data.get("error"),
                attempt=te_data.get("attempt", 0),
            )

        return execution

    def _serialize_context(self, obj: Any) -> Any:
        """Serialize context for JSON storage."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: self._serialize_context(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_context(v) for v in obj]
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return obj


def create_investigation_dag(
    incident_id: str,
    affected_services: list[str],
    severity: str = "P2",
) -> TaskDAG:
    """Create a standard investigation DAG for incidents."""
    dag = TaskDAG(f"investigation-{incident_id}", f"Investigation for {incident_id}")

    dag.add_task(TaskNode(
        id="collect_logs",
        name="Collect Logs",
        handler="collect_logs",
        priority=TaskPriority.CRITICAL,
        input_params={
            "incident_id": incident_id,
            "services": affected_services,
            "time_window_minutes": 60,
        },
    ))

    dag.add_task(TaskNode(
        id="collect_metrics",
        name="Collect Metrics",
        handler="collect_metrics",
        priority=TaskPriority.CRITICAL,
        input_params={
            "incident_id": incident_id,
            "services": affected_services,
            "time_window_minutes": 60,
        },
    ))

    dag.add_task(TaskNode(
        id="check_deployments",
        name="Check Recent Deployments",
        handler="check_deployments",
        priority=TaskPriority.HIGH,
        input_params={
            "services": affected_services,
            "hours": 24,
        },
    ))

    dag.add_task(TaskNode(
        id="correlate",
        name="Correlate Events",
        handler="correlate_events",
        depends_on=["collect_logs", "collect_metrics", "check_deployments"],
        priority=TaskPriority.HIGH,
    ))

    dag.add_task(TaskNode(
        id="hypothesize",
        name="Generate Hypotheses",
        handler="generate_hypotheses",
        depends_on=["correlate"],
        priority=TaskPriority.HIGH,
    ))

    dag.add_task(TaskNode(
        id="decision_gate",
        name="Decision Gate",
        handler="decision_gate",
        depends_on=["hypothesize"],
        priority=TaskPriority.CRITICAL,
        branches={
            "fix": "execute_fix",
            "investigate_more": "deep_dive",
            "escalate": "escalate_to_human",
        },
    ))

    dag.add_task(TaskNode(
        id="execute_fix",
        name="Execute Fix",
        handler="execute_remediation",
        depends_on=["decision_gate"],
        requires_approval=severity in ("P1", "P2"),
        priority=TaskPriority.CRITICAL,
    ))

    dag.add_task(TaskNode(
        id="verify_fix",
        name="Verify Fix",
        handler="verify_resolution",
        depends_on=["execute_fix"],
        priority=TaskPriority.HIGH,
    ))

    return dag
