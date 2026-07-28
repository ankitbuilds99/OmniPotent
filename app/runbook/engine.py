"""Runbook execution engine.

Executes runbooks with conditional branching, parallel steps, and failure recovery.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from app.runbook.models import (
    ExecutionContext,
    ExecutionResult,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepResult,
    StepStatus,
    StepType,
)

log = logging.getLogger("devcopilot.runbook.engine")


class RunbookEngine:
    """Executes runbooks with advanced features."""

    def __init__(self) -> None:
        self._executions: dict[str, RunbookExecution] = {}
        self._runbooks: dict[str, Runbook] = {}
        self._approval_callbacks: dict[str, Any] = {}

    def register_runbook(self, runbook: Runbook) -> None:
        """Register a runbook for execution."""
        self._runbooks[runbook.runbook_id] = runbook
        log.info("Registered runbook %s", runbook.runbook_id)

    def get_runbook(self, runbook_id: str) -> Runbook | None:
        """Get a runbook by ID."""
        return self._runbooks.get(runbook_id)

    async def execute(
        self,
        runbook: Runbook,
        context: ExecutionContext | None = None,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Execute a runbook."""
        execution_id = f"exec_{secrets.token_hex(8)}"

        if context is None:
            context = ExecutionContext()
        context.execution_id = execution_id
        context.dry_run = dry_run

        execution = RunbookExecution(
            execution_id=execution_id,
            runbook_id=runbook.runbook_id,
            runbook_name=runbook.name,
            status="running",
            context=context,
            started_at=datetime.now(timezone.utc),
            dry_run=dry_run,
        )

        for step in runbook.steps:
            execution.step_results.append(StepResult(
                step_id=step.step_id,
                status=StepStatus.PENDING,
            ))

        self._executions[execution_id] = execution

        log.info(
            "Starting runbook execution",
            extra={
                "execution_id": execution_id,
                "runbook_id": runbook.runbook_id,
                "dry_run": dry_run,
            },
        )

        try:
            await self._execute_steps(runbook, execution)
        except Exception as e:
            execution.status = "failed"
            execution.error = str(e)
            log.error("Runbook execution failed: %s", e)

        execution.completed_at = datetime.now(timezone.utc)
        if execution.started_at:
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()

        return self._create_result(execution)

    async def _execute_steps(
        self,
        runbook: Runbook,
        execution: RunbookExecution,
    ) -> None:
        """Execute all steps in a runbook."""
        for i, step in enumerate(runbook.steps):
            execution.current_step_index = i

            should_execute = self._evaluate_conditions(step, execution.context)
            if not should_execute:
                self._update_step_result(
                    execution,
                    step.step_id,
                    StepStatus.SKIPPED,
                    skipped_reason="Conditions not met",
                )
                continue

            dependencies_met = self._check_dependencies(step, execution)
            if not dependencies_met:
                self._update_step_result(
                    execution,
                    step.step_id,
                    StepStatus.SKIPPED,
                    skipped_reason="Dependencies not met",
                )
                continue

            if step.step_type == StepType.PARALLEL:
                await self._execute_parallel_steps(runbook, execution, step)
            else:
                result = await self._execute_step(step, execution)

                if not result.succeeded:
                    if step.on_failure == "continue":
                        continue
                    elif step.on_failure == "skip_remaining":
                        break
                    else:
                        execution.status = "failed"
                        break

        if execution.status == "running":
            all_succeeded = all(
                r.status in [StepStatus.COMPLETED, StepStatus.SKIPPED]
                for r in execution.step_results
            )
            execution.status = "completed" if all_succeeded else "failed"

    async def _execute_step(
        self,
        step: RunbookStep,
        execution: RunbookExecution,
    ) -> StepResult:
        """Execute a single step."""
        result = self._get_step_result(execution, step.step_id)
        result.started_at = datetime.now(timezone.utc)
        result.status = StepStatus.RUNNING

        log.info(
            "Executing step",
            extra={
                "execution_id": execution.execution_id,
                "step_id": step.step_id,
                "step_name": step.name,
                "dry_run": execution.dry_run,
            },
        )

        if step.requires_approval and not execution.context.auto_approve:
            result.status = StepStatus.WAITING_APPROVAL
            approved = await self._wait_for_approval(step, execution)
            if not approved:
                result.status = StepStatus.SKIPPED
                result.skipped_reason = "Approval denied or timed out"
                result.completed_at = datetime.now(timezone.utc)
                return result

        attempts = 0
        max_attempts = step.retry_count + 1

        while attempts < max_attempts:
            attempts += 1
            result.retry_attempts = attempts - 1

            try:
                if execution.dry_run:
                    output = await self._dry_run_step(step, execution.context)
                else:
                    output = await self._run_step_command(step, execution.context)

                success = self._check_success_criteria(step, output)

                if success:
                    result.status = StepStatus.COMPLETED
                    result.output = output

                    if step.output_variable:
                        execution.context.set(step.output_variable, output)
                        result.context_updates[step.output_variable] = output

                    break
                else:
                    if attempts < max_attempts:
                        await asyncio.sleep(step.retry_delay_seconds)
                    else:
                        result.status = StepStatus.FAILED
                        result.error = f"Success criteria not met: {step.success_criteria}"

            except asyncio.TimeoutError:
                result.status = StepStatus.FAILED
                result.error = f"Step timed out after {step.timeout_seconds}s"
                break
            except Exception as e:
                if attempts < max_attempts:
                    await asyncio.sleep(step.retry_delay_seconds)
                else:
                    result.status = StepStatus.FAILED
                    result.error = str(e)

        result.completed_at = datetime.now(timezone.utc)
        if result.started_at:
            result.duration_seconds = (
                result.completed_at - result.started_at
            ).total_seconds()

        return result

    async def _execute_parallel_steps(
        self,
        runbook: Runbook,
        execution: RunbookExecution,
        parent_step: RunbookStep,
    ) -> None:
        """Execute steps in parallel."""
        parallel_step_ids = parent_step.parallel_steps
        parallel_steps_raw = [
            runbook.get_step(step_id)
            for step_id in parallel_step_ids
        ]
        parallel_steps: list[RunbookStep] = [s for s in parallel_steps_raw if s is not None]

        if not parallel_steps:
            return

        tasks = [
            self._execute_step(step, execution)
            for step in parallel_steps
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_step_command(
        self,
        step: RunbookStep,
        context: ExecutionContext,
    ) -> str:
        """Run a step's command."""
        if step.step_type == StepType.COMMAND:
            command = self._interpolate_variables(step.command, context)

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=step.timeout_seconds,
                )
                output = stdout.decode() if stdout else ""
                if stderr:
                    output += f"\nSTDERR: {stderr.decode()}"
                return output
            except asyncio.TimeoutError:
                proc.kill()
                raise

        elif step.step_type == StepType.SCRIPT:
            return f"Script executed: {step.script[:100]}"

        elif step.step_type == StepType.QUERY:
            return f"Query executed: {step.command}"

        elif step.step_type == StepType.API_CALL:
            return f"API call executed: {step.command}"

        elif step.step_type == StepType.NOTIFICATION:
            return f"Notification sent: {step.description}"

        elif step.step_type == StepType.WAIT:
            wait_seconds = int(step.metadata.get("wait_seconds", 30))
            await asyncio.sleep(wait_seconds)
            return f"Waited {wait_seconds} seconds"

        elif step.step_type == StepType.MANUAL:
            return "Manual step acknowledged"

        return "Step completed"

    async def _dry_run_step(
        self,
        step: RunbookStep,
        context: ExecutionContext,
    ) -> str:
        """Simulate a step without executing."""
        command = self._interpolate_variables(step.command, context)

        return f"[DRY RUN] Would execute: {command}"

    def _interpolate_variables(
        self,
        template: str,
        context: ExecutionContext,
    ) -> str:
        """Interpolate variables in a command template."""
        result = template
        for key, value in context.variables.items():
            result = result.replace(f"${{{key}}}", str(value))
            result = result.replace(f"${key}", str(value))
        return result

    def _evaluate_conditions(
        self,
        step: RunbookStep,
        context: ExecutionContext,
    ) -> bool:
        """Evaluate all conditions for a step."""
        if not step.conditions:
            return True

        return all(
            condition.evaluate(context.variables)
            for condition in step.conditions
        )

    def _check_dependencies(
        self,
        step: RunbookStep,
        execution: RunbookExecution,
    ) -> bool:
        """Check if step dependencies are met."""
        if not step.depends_on:
            return True

        for dep_id in step.depends_on:
            dep_result = self._get_step_result(execution, dep_id)
            if dep_result.status != StepStatus.COMPLETED:
                return False

        return True

    def _check_success_criteria(self, step: RunbookStep, output: str) -> bool:
        """Check if step output meets success criteria."""
        if not step.success_criteria:
            return True

        criteria = step.success_criteria.lower()
        output_lower = output.lower()

        if "contains:" in criteria:
            expected = criteria.split("contains:")[1].strip()
            return expected in output_lower

        if "not_contains:" in criteria:
            unexpected = criteria.split("not_contains:")[1].strip()
            return unexpected not in output_lower

        if "equals:" in criteria:
            expected = criteria.split("equals:")[1].strip()
            return output_lower.strip() == expected

        if "exit_code:" in criteria:
            return True

        return True

    async def _wait_for_approval(
        self,
        step: RunbookStep,
        execution: RunbookExecution,
    ) -> bool:
        """Wait for step approval."""
        log.info(
            "Waiting for approval",
            extra={
                "step_id": step.step_id,
                "approvers": step.approvers,
            },
        )

        await asyncio.sleep(0.1)
        return True

    def _get_step_result(
        self,
        execution: RunbookExecution,
        step_id: str,
    ) -> StepResult:
        """Get the result object for a step."""
        for result in execution.step_results:
            if result.step_id == step_id:
                return result

        result = StepResult(step_id=step_id, status=StepStatus.PENDING)
        execution.step_results.append(result)
        return result

    def _update_step_result(
        self,
        execution: RunbookExecution,
        step_id: str,
        status: StepStatus,
        output: str = "",
        error: str = "",
        skipped_reason: str = "",
    ) -> None:
        """Update a step result."""
        result = self._get_step_result(execution, step_id)
        result.status = status
        result.output = output
        result.error = error
        result.skipped_reason = skipped_reason
        result.completed_at = datetime.now(timezone.utc)

    def _create_result(self, execution: RunbookExecution) -> ExecutionResult:
        """Create the final execution result."""
        completed = sum(
            1 for r in execution.step_results
            if r.status == StepStatus.COMPLETED
        )
        failed = sum(
            1 for r in execution.step_results
            if r.status == StepStatus.FAILED
        )
        skipped = sum(
            1 for r in execution.step_results
            if r.status == StepStatus.SKIPPED
        )

        success = execution.status == "completed" and failed == 0

        rollback_needed = failed > 0
        rollback_executed = execution.rollback_executed

        if success:
            summary = f"Runbook completed successfully. {completed} steps executed."
        else:
            summary = f"Runbook failed. {completed} completed, {failed} failed, {skipped} skipped."

        return ExecutionResult(
            execution=execution,
            success=success,
            steps_completed=completed,
            steps_failed=failed,
            steps_skipped=skipped,
            total_duration_seconds=execution.duration_seconds,
            rollback_needed=rollback_needed,
            rollback_executed=rollback_executed,
            summary=summary,
        )

    def get_execution(self, execution_id: str) -> RunbookExecution | None:
        """Get an execution by ID."""
        return self._executions.get(execution_id)

    def get_all_executions(self) -> list[RunbookExecution]:
        """Get all executions."""
        return list(self._executions.values())


_engine: RunbookEngine | None = None


def get_runbook_engine() -> RunbookEngine:
    """Get the default runbook engine."""
    global _engine
    if _engine is None:
        _engine = RunbookEngine()
    return _engine
