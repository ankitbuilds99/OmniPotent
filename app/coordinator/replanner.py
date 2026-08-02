"""Dynamic Replanning for Task DAGs.

Handles task failures with:
1. Retry with backoff
2. Skip and continue
3. Execute alternative paths
4. Inject recovery tasks
5. Rollback completed tasks
6. LLM-based dynamic replanning
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.coordinator.dag_executor import (
    DAGExecution,
    ExecutionState,
    TaskDAG,
    TaskExecution,
    TaskNode,
    TaskPriority,
)

log = logging.getLogger("omnipotent.coordinator.replanner")


class ReplanStrategy(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    ALTERNATIVE = "alternative"
    RECOVERY = "recovery"
    ROLLBACK = "rollback"
    ABORT = "abort"
    LLM_REPLAN = "llm_replan"


@dataclass
class ReplanDecision:
    """Decision made by the replanner."""
    strategy: ReplanStrategy
    new_dag: TaskDAG | None = None
    skip_downstream: bool = False
    rollback_tasks: list[str] = field(default_factory=list)
    explanation: str = ""
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "skip_downstream": self.skip_downstream,
            "rollback_tasks": self.rollback_tasks,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "has_new_dag": self.new_dag is not None,
        }


class DynamicReplanner:
    """Handles dynamic replanning when task failures occur.

    Integrates with LLM for intelligent recovery planning.
    """

    def __init__(
        self,
        llm_client: Any = None,
        knowledge_graph: Any = None,
        max_replan_attempts: int = 3,
    ):
        self.llm = llm_client
        self.graph = knowledge_graph
        self.max_replan_attempts = max_replan_attempts
        self._replan_history: dict[str, list[ReplanDecision]] = {}

    async def replan(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        failed_task_id: str,
        error: str,
    ) -> ReplanDecision:
        """Generate a recovery plan after failure."""
        failed_task = dag.tasks.get(failed_task_id)
        if not failed_task:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                explanation=f"Task {failed_task_id} not found in DAG",
            )

        replan_count = len(self._replan_history.get(failed_task_id, []))
        if replan_count >= self.max_replan_attempts:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                explanation=f"Max replan attempts ({self.max_replan_attempts}) exceeded",
            )

        if failed_task.on_failure:
            decision = await self._activate_failure_path(dag, failed_task)
        elif failed_task.rollback_handler:
            decision = await self._create_rollback_plan(dag, execution, failed_task_id)
        elif self.llm:
            decision = await self._llm_replan(dag, execution, failed_task_id, error)
        else:
            decision = await self._heuristic_replan(dag, execution, failed_task_id, error)

        if failed_task_id not in self._replan_history:
            self._replan_history[failed_task_id] = []
        self._replan_history[failed_task_id].append(decision)

        return decision

    async def _activate_failure_path(
        self,
        dag: TaskDAG,
        failed_task: TaskNode,
    ) -> ReplanDecision:
        """Activate the predefined failure path."""
        if not failed_task.on_failure or failed_task.on_failure not in dag.tasks:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                explanation=f"Failure handler {failed_task.on_failure} not found",
            )

        new_dag = TaskDAG(
            dag_id=f"{dag.id}_failure_recovery",
            name=f"{dag.name} - Failure Recovery",
        )

        recovery_task = dag.tasks[failed_task.on_failure]
        new_dag.add_task(TaskNode(
            id=recovery_task.id,
            name=recovery_task.name,
            handler=recovery_task.handler,
            input_params={
                **recovery_task.input_params,
                "failed_task_id": failed_task.id,
                "failure_reason": "predefined_handler",
            },
            priority=TaskPriority.CRITICAL,
        ))

        for task_id, task in dag.tasks.items():
            if failed_task.on_failure in task.depends_on:
                task_copy = TaskNode(
                    id=task.id,
                    name=task.name,
                    handler=task.handler,
                    depends_on=[
                        dep if dep != failed_task.id else failed_task.on_failure
                        for dep in task.depends_on
                    ],
                    input_params=task.input_params,
                    priority=task.priority,
                )
                new_dag.add_task(task_copy)

        return ReplanDecision(
            strategy=ReplanStrategy.ALTERNATIVE,
            new_dag=new_dag,
            explanation=f"Activated failure handler: {failed_task.on_failure}",
            confidence=0.9,
        )

    async def _create_rollback_plan(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        failed_task_id: str,
    ) -> ReplanDecision:
        """Create a DAG to rollback completed tasks."""
        rollback_dag = TaskDAG(
            dag_id=f"{dag.id}_rollback",
            name=f"{dag.name} - Rollback",
        )

        completed_tasks = [
            task_id
            for task_id, task_exec in execution.task_executions.items()
            if task_exec.state == ExecutionState.COMPLETED
            and dag.tasks.get(task_id)
            and dag.tasks[task_id].is_reversible
            and dag.tasks[task_id].rollback_handler
        ]

        if not completed_tasks:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                explanation="No reversible completed tasks to rollback",
            )

        completed_in_order = []
        for level in dag.get_execution_levels():
            for task_id in level:
                if task_id in completed_tasks:
                    completed_in_order.append(task_id)

        prev_task_id: str | None = None
        for task_id in reversed(completed_in_order):
            original_task = dag.tasks[task_id]
            task_exec = execution.task_executions.get(task_id)

            rollback_task = TaskNode(
                id=f"rollback_{task_id}",
                name=f"Rollback: {original_task.name}",
                handler=original_task.rollback_handler or "noop",
                depends_on=[prev_task_id] if prev_task_id else [],
                input_params={
                    "original_task_id": task_id,
                    "original_output": task_exec.output if task_exec else None,
                },
                priority=TaskPriority.CRITICAL,
            )
            rollback_dag.add_task(rollback_task)
            prev_task_id = rollback_task.id

        return ReplanDecision(
            strategy=ReplanStrategy.ROLLBACK,
            new_dag=rollback_dag,
            rollback_tasks=list(reversed(completed_in_order)),
            explanation=f"Rolling back {len(completed_in_order)} completed tasks",
            confidence=0.85,
        )

    async def _llm_replan(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        failed_task_id: str,
        error: str,
    ) -> ReplanDecision:
        """Use LLM to generate a recovery plan."""
        failed_task = dag.tasks[failed_task_id]

        completed_outputs = {
            task_id: te.output
            for task_id, te in execution.task_executions.items()
            if te.state == ExecutionState.COMPLETED
        }

        remaining_tasks = [
            dag.tasks[tid].name
            for tid in dag.tasks
            if tid not in execution.task_executions
        ]

        prompt = f"""A task in a DevOps workflow has failed. Analyze and suggest recovery.

## Workflow: {dag.name}

## Failed Task
- ID: {failed_task.id}
- Name: {failed_task.name}
- Handler: {failed_task.handler}
- Error: {error}

## Completed Tasks
{json.dumps(list(completed_outputs.keys()), indent=2)}

## Remaining Tasks
{remaining_tasks}

## Available Recovery Strategies
1. RETRY - Retry the failed task (if transient error)
2. SKIP - Skip the failed task and continue (if non-critical)
3. ALTERNATIVE - Execute alternative recovery path
4. ROLLBACK - Rollback completed tasks and abort
5. ABORT - Stop execution entirely

Analyze the error and context, then recommend a strategy.

Respond with JSON:
{{
  "strategy": "RETRY|SKIP|ALTERNATIVE|ROLLBACK|ABORT",
  "confidence": 0.0-1.0,
  "explanation": "Why this strategy",
  "recovery_steps": ["step1", "step2"] // if ALTERNATIVE
}}
"""

        try:
            response = await self.llm.complete(prompt=prompt, temperature=0.2)

            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(response[start:end])

                strategy = ReplanStrategy[plan.get("strategy", "ABORT").upper()]

                if strategy == ReplanStrategy.ALTERNATIVE and plan.get("recovery_steps"):
                    new_dag = await self._build_recovery_dag(
                        dag, failed_task_id, plan["recovery_steps"]
                    )
                else:
                    new_dag = None

                return ReplanDecision(
                    strategy=strategy,
                    new_dag=new_dag,
                    explanation=plan.get("explanation", "LLM-suggested recovery"),
                    confidence=float(plan.get("confidence", 0.5)),
                )

        except Exception as e:
            log.error("LLM replan failed: %s", e)

        return await self._heuristic_replan(dag, execution, failed_task_id, error)

    async def _heuristic_replan(
        self,
        dag: TaskDAG,
        execution: DAGExecution,
        failed_task_id: str,
        error: str,
    ) -> ReplanDecision:
        """Heuristic-based replanning without LLM."""
        error_lower = error.lower()

        if any(kw in error_lower for kw in ["timeout", "rate limit", "connection", "temporary"]):
            return ReplanDecision(
                strategy=ReplanStrategy.RETRY,
                explanation="Transient error detected, recommending retry",
                confidence=0.7,
            )

        failed_task = dag.tasks.get(failed_task_id)
        if failed_task:
            downstream = dag.get_affected_tasks(failed_task_id)
            if not downstream:
                return ReplanDecision(
                    strategy=ReplanStrategy.SKIP,
                    skip_downstream=False,
                    explanation="Failed task has no dependents, safe to skip",
                    confidence=0.6,
                )

        if any(kw in error_lower for kw in ["fatal", "critical", "security", "permission denied"]):
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                explanation="Critical error detected, aborting execution",
                confidence=0.8,
            )

        return ReplanDecision(
            strategy=ReplanStrategy.SKIP,
            skip_downstream=True,
            explanation="Skipping failed task and downstream dependencies",
            confidence=0.5,
        )

    async def _build_recovery_dag(
        self,
        original_dag: TaskDAG,
        failed_task_id: str,
        recovery_steps: list[str],
    ) -> TaskDAG:
        """Build a recovery DAG from LLM-suggested steps."""
        recovery_dag = TaskDAG(
            dag_id=f"{original_dag.id}_recovery",
            name=f"{original_dag.name} - Recovery",
        )

        prev_task_id: str | None = None
        for i, step in enumerate(recovery_steps):
            task = TaskNode(
                id=f"recovery_{i}",
                name=step,
                handler="llm_recovery_step",
                depends_on=[prev_task_id] if prev_task_id else [],
                input_params={
                    "step_description": step,
                    "original_failed_task": failed_task_id,
                    "step_index": i,
                },
                priority=TaskPriority.HIGH,
            )
            recovery_dag.add_task(task)
            prev_task_id = task.id

        return recovery_dag

    def get_replan_history(self, task_id: str) -> list[ReplanDecision]:
        """Get replan history for a task."""
        return self._replan_history.get(task_id, [])

    def clear_history(self, task_id: str | None = None) -> None:
        """Clear replan history."""
        if task_id:
            self._replan_history.pop(task_id, None)
        else:
            self._replan_history.clear()


class RecoveryExecutor:
    """Executes recovery plans generated by the replanner."""

    def __init__(
        self,
        dag_executor: Any,
        replanner: DynamicReplanner,
    ):
        self.executor = dag_executor
        self.replanner = replanner

    async def execute_with_recovery(
        self,
        dag: TaskDAG,
        context: dict[str, Any] | None = None,
        max_recovery_attempts: int = 3,
    ) -> DAGExecution:
        """Execute a DAG with automatic recovery on failures."""
        attempt = 0

        while attempt < max_recovery_attempts:
            attempt += 1

            execution = await self.executor.execute(dag, context)

            if execution.state == ExecutionState.COMPLETED:
                return execution

            failed_tasks = [
                (tid, te)
                for tid, te in execution.task_executions.items()
                if te.state == ExecutionState.FAILED
            ]

            if not failed_tasks:
                return execution

            task_id, task_exec = failed_tasks[0]
            error = task_exec.error or "Unknown error"

            decision = await self.replanner.replan(dag, execution, task_id, error)

            log.info(
                "Recovery attempt %d: strategy=%s, confidence=%.2f",
                attempt, decision.strategy.value, decision.confidence,
            )

            if decision.strategy == ReplanStrategy.ABORT:
                return execution

            if decision.strategy == ReplanStrategy.RETRY:
                continue

            if decision.strategy == ReplanStrategy.SKIP:
                execution.task_executions[task_id].state = ExecutionState.SKIPPED
                if decision.skip_downstream:
                    for downstream_id in dag.get_affected_tasks(task_id):
                        if downstream_id not in execution.task_executions:
                            execution.task_executions[downstream_id] = TaskExecution(
                                task_id=downstream_id,
                                state=ExecutionState.SKIPPED,
                            )
                continue

            if decision.new_dag:
                recovery_context = dict(context) if context else {}
                recovery_context["original_execution"] = execution.to_dict()
                recovery_context["recovery_attempt"] = attempt
                recovery_execution = await self.executor.execute(
                    decision.new_dag,
                    recovery_context,
                )

                if recovery_execution.state == ExecutionState.COMPLETED:
                    execution.accumulated_context.update(
                        recovery_execution.accumulated_context
                    )
                    return execution

        return execution
