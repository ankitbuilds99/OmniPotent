"""Runbook execution engine.

Executes runbook steps with approval workflows, rollback support,
and integration with various systems (Kubernetes, Slack, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config import get_settings
from app.runbooks.models import (
    DEFAULT_RUNBOOKS,
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepExecution,
    StepStatus,
    StepType,
)

log = logging.getLogger("omnipotent.runbooks")

# Called after every step so a caller can persist partial progress mid-run.
ProgressHook = Callable[[RunbookExecution], Awaitable[None]]


async def _report(hook: ProgressHook | None, execution: RunbookExecution) -> None:
    """Invoke a progress hook, never letting it take the run down with it.

    The hook is bookkeeping — usually a database write. A step that really ran
    must not be un-run because recording it failed, so a raising hook is logged
    and the execution continues; the final state is written again at the end.
    """
    if hook is None:
        return
    try:
        await hook(execution)
    except Exception:
        log.exception("Runbook progress hook failed for execution %s", execution.id)


def _refuse(step_kind: str, missing: str) -> tuple[bool, str, str]:
    """Fail-closed (success, output, error) triple for a step with no backend.

    Most step types below have nothing behind them: no shell is spawned, no
    cluster is contacted, no message is posted. Returning success for those
    would tell a responder that a mitigation ran when it never did — the one
    failure mode that is worse than the step erroring out — so they refuse
    instead unless the operator explicitly asked for a simulation.
    """
    log.warning("Runbook %s step not executed: %s", step_kind, missing)
    return (
        False,
        "",
        f"{missing}; refusing to report success for an action that was not performed",
    )


class RunbookExecutor:
    """Executes runbooks with approval workflows.

    Handles step execution, approval gates, rollbacks,
    and notifications.
    """

    def __init__(self) -> None:
        self._runbooks: dict[str, Runbook] = {}
        self._executions: dict[str, RunbookExecution] = {}
        self._pending_approvals: dict[str, tuple[RunbookExecution, RunbookStep]] = {}
        self._load_default_runbooks()

    def _load_default_runbooks(self) -> None:
        """Load default runbook definitions."""
        for rb_data in DEFAULT_RUNBOOKS:
            runbook = Runbook.from_dict(rb_data)
            self._runbooks[runbook.id] = runbook

    def add_runbook(self, runbook: Runbook) -> None:
        """Add or update a runbook."""
        self._runbooks[runbook.id] = runbook
        log.info("Added runbook: %s", runbook.name)

    def remove_runbook(self, runbook_id: str) -> bool:
        """Remove a runbook."""
        if runbook_id in self._runbooks:
            del self._runbooks[runbook_id]
            return True
        return False

    def get_runbook(self, runbook_id: str) -> Runbook | None:
        """Get a runbook by ID."""
        return self._runbooks.get(runbook_id)

    def list_runbooks(
        self,
        service: str | None = None,
        trigger: str | None = None,
    ) -> list[Runbook]:
        """List runbooks with optional filtering."""
        runbooks = list(self._runbooks.values())

        if service:
            runbooks = [r for r in runbooks if r.service == "*" or r.service == service]

        if trigger:
            runbooks = [r for r in runbooks if r.trigger == trigger or r.trigger == "manual"]

        return [r for r in runbooks if r.enabled]

    def _substitute_variables(self, text: str, context: dict[str, Any]) -> str:
        """Substitute ${var} placeholders with context values."""
        def replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return str(context.get(var_name, match.group(0)))

        return re.sub(r"\$\{(\w+)\}", replace, text)

    def _substitute_step(self, step: RunbookStep, context: dict[str, Any]) -> RunbookStep:
        """Create a copy of step with substituted variables."""
        return RunbookStep(
            id=step.id,
            name=step.name,
            description=self._substitute_variables(step.description, context),
            step_type=step.step_type,
            action=self._substitute_variables(step.action, context),
            params={
                k: self._substitute_variables(str(v), context) if isinstance(v, str) else v
                for k, v in step.params.items()
            },
            timeout_seconds=step.timeout_seconds,
            continue_on_failure=step.continue_on_failure,
            requires_approval=step.requires_approval,
            rollback_step=step.rollback_step,
        )

    async def _execute_step(
        self,
        step: RunbookStep,
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Execute a single step.

        Returns (success, output, error).
        """
        step = self._substitute_step(step, context)

        try:
            if step.step_type == StepType.COMMAND:
                return await self._exec_command(step)
            elif step.step_type == StepType.KUBERNETES:
                return await self._exec_kubernetes(step)
            elif step.step_type == StepType.SLACK:
                return await self._exec_slack(step, context)
            elif step.step_type == StepType.WAIT:
                return await self._exec_wait(step)
            elif step.step_type == StepType.HTTP:
                return await self._exec_http(step)
            else:
                return False, "", f"Unsupported step type: {step.step_type}"
        except asyncio.TimeoutError:
            return False, "", f"Step timed out after {step.timeout_seconds}s"
        except Exception as e:
            return False, "", str(e)

    async def _exec_command(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Execute a shell command.

        No command runner is wired into this executor — nothing is ever spawned.
        """
        if not get_settings().copilot_demo_mode:
            return _refuse(
                "command",
                f"no command executor is configured for runbook COMMAND steps "
                f"(command was: {step.action!r})",
            )

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING command: %s", step.action)
        return True, f"Command executed: {step.action} (simulated)", ""

    async def _exec_kubernetes(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Execute a Kubernetes operation.

        RunbookExecutor holds no Kubernetes client, so none of these actions
        ever reach a cluster; the strings below are descriptions of what *would*
        happen, not observations of what did.
        """
        action = step.action
        params = step.params

        # Work out the message first so an unknown action is still reported as
        # such rather than being masked by the not-configured refusal.
        if action == "rollout_restart":
            deployment = params.get("deployment", "unknown")
            output = f"Initiated rollout restart for {deployment}"

        elif action == "wait_rollout":
            deployment = params.get("deployment", "unknown")
            output = f"Rollout completed for {deployment}"

        elif action == "scale":
            deployment = params.get("deployment", "unknown")
            replicas = params.get("replicas", "0")
            output = f"Scaled {deployment} to {replicas} replicas"

        elif action == "get_deployment":
            deployment = params.get("deployment", "unknown")
            output = f"Deployment {deployment}: 3 replicas, Ready"

        elif action == "rollout_history":
            deployment = params.get("deployment", "unknown")
            output = f"Revision history for {deployment}: rev1, rev2, rev3 (current)"

        elif action == "rollout_undo":
            deployment = params.get("deployment", "unknown")
            output = f"Rolled back {deployment} to previous revision"

        elif action == "cordon_node":
            node = params.get("node", "unknown")
            output = f"Node {node} cordoned"

        elif action == "drain_node":
            node = params.get("node", "unknown")
            output = f"Node {node} drained"

        else:
            return False, "", f"Unknown Kubernetes action: {action}"

        if not get_settings().copilot_demo_mode:
            return _refuse(
                f"kubernetes/{action}",
                "no Kubernetes client is wired into the runbook executor "
                "(set KUBE_CONFIG_PATH and inject a client)",
            )

        log.warning(
            "COPILOT_DEMO_MODE=true - SIMULATING Kubernetes action: %s params=%s",
            action, params,
        )
        return True, f"{output} (simulated)", ""

    async def _exec_slack(
        self,
        step: RunbookStep,
        context: dict[str, Any],
    ) -> tuple[bool, str, str]:
        """Post a Slack message.

        No Slack client is wired into this executor, so nothing is posted. A
        step that claims it notified the channel when it did not is how an
        incident goes unannounced.
        """
        message = step.params.get("message", "")
        message = self._substitute_variables(message, context)

        if not get_settings().copilot_demo_mode:
            return _refuse(
                "slack",
                "no Slack client is wired into the runbook executor "
                "(set SLACK_BOT_TOKEN and inject a client)",
            )

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING Slack message: %s", message)
        return True, f"Posted to Slack: {message} (simulated)", ""

    async def _exec_wait(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Wait for a specified duration.

        This is the one step type that really does what it says, so it runs in
        both modes — but the wait is only shortened (and relabelled) for demos;
        a real run has to actually elapse or the steps after it are checking a
        system that has not settled yet.
        """
        seconds = int(step.params.get("seconds", 10))

        if get_settings().copilot_demo_mode:
            capped = min(seconds, 5)
            log.warning(
                "COPILOT_DEMO_MODE=true - SIMULATING %ds wait as %ds", seconds, capped
            )
            await asyncio.sleep(capped)
            return True, f"Waited {seconds} seconds (simulated: slept {capped}s)", ""

        # Nothing else enforces step.timeout_seconds, so refuse a wait that
        # would outlive its own step budget instead of blocking the execution.
        if seconds > step.timeout_seconds:
            return False, "", (
                f"wait of {seconds}s exceeds the step timeout of "
                f"{step.timeout_seconds}s"
            )

        log.info("Waiting %d seconds", seconds)
        await asyncio.sleep(seconds)
        return True, f"Waited {seconds} seconds", ""

    async def _exec_http(self, step: RunbookStep) -> tuple[bool, str, str]:
        """Make an HTTP request.

        No request is issued here; "200 OK" below is a literal, not a response.
        """
        url = step.params.get("url", "")
        method = step.params.get("method", "GET")

        if not get_settings().copilot_demo_mode:
            return _refuse(
                "http",
                f"no HTTP client is wired into the runbook executor "
                f"(request was: {method} {url})",
            )

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING HTTP %s %s", method, url)
        return True, f"HTTP {method} {url}: 200 OK (simulated)", ""

    async def execute(
        self,
        runbook_id: str,
        context: dict[str, Any],
        triggered_by: str = "manual",
        skip_approval: bool = False,
        runbook: Runbook | None = None,
        execution_id: str | None = None,
        on_progress: ProgressHook | None = None,
    ) -> RunbookExecution:
        """Execute a runbook.

        Args:
            runbook_id: ID of the runbook to execute
            context: Variables for substitution (service, namespace, etc.)
            triggered_by: Who/what triggered this execution
            skip_approval: Skip approval gates (for testing)
            runbook: The definition to run. Supplied by callers that keep their
                runbooks somewhere other than this executor's registry — the API
                router loads them per tenant, and looking them up here would mean
                one org's edit to a shared id changing what another org runs.
            execution_id: Id to record this run under. Callers that must return
                the id before the run finishes (the API returns it immediately
                and the caller polls) generate it up front; otherwise one is
                minted here.
            on_progress: Awaited after each step completes and once at the end,
                so a caller can persist partial progress. A run that only reports
                its result at the end is indistinguishable from a hung one.

        Returns:
            RunbookExecution record
        """
        if runbook is None:
            runbook = self._runbooks.get(runbook_id)
        if not runbook:
            raise ValueError(f"Runbook '{runbook_id}' not found")

        if not runbook.enabled:
            raise ValueError(f"Runbook '{runbook_id}' is disabled")

        execution = RunbookExecution(
            id=execution_id or str(uuid.uuid4()),
            runbook_id=runbook.id,
            runbook_name=runbook.name,
            status=StepStatus.RUNNING,
            triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc),
            context=context,
        )
        self._executions[execution.id] = execution

        log.info(
            "Starting runbook execution: %s (id=%s) triggered_by=%s",
            runbook.name, execution.id, triggered_by
        )

        for step in runbook.steps:
            step_exec = StepExecution(
                step_id=step.id,
                step_name=step.name,
                status=StepStatus.PENDING,
                started_at=datetime.now(timezone.utc),
            )

            if step.requires_approval and not skip_approval:
                if runbook.execution_mode == ExecutionMode.REQUIRES_APPROVAL:
                    step_exec.status = StepStatus.WAITING_APPROVAL
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.WAITING_APPROVAL
                    self._pending_approvals[execution.id] = (execution, step)
                    log.info("Step %s waiting for approval", step.name)
                    await _report(on_progress, execution)
                    return execution

            step_exec.status = StepStatus.RUNNING
            success, output, error = await self._execute_step(step, context)

            step_exec.completed_at = datetime.now(timezone.utc)
            step_exec.output = output
            step_exec.error = error

            if success:
                step_exec.status = StepStatus.SUCCESS
                log.info("Step %s completed successfully", step.name)
            else:
                step_exec.status = StepStatus.FAILED
                log.error("Step %s failed: %s", step.name, error)

                if not step.continue_on_failure:
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.FAILED
                    execution.completed_at = datetime.now(timezone.utc)
                    await _report(on_progress, execution)
                    return execution

            execution.steps.append(step_exec)
            await _report(on_progress, execution)

        execution.status = StepStatus.SUCCESS
        execution.completed_at = datetime.now(timezone.utc)
        log.info("Runbook execution completed: %s", execution.id)

        await _report(on_progress, execution)
        return execution

    async def approve(self, execution_id: str) -> RunbookExecution | None:
        """Approve and continue a paused execution held in this process."""
        if execution_id not in self._pending_approvals:
            return None

        execution, step = self._pending_approvals.pop(execution_id)
        runbook = self._runbooks.get(execution.runbook_id)

        if not runbook:
            return None

        return await self.resume(execution, runbook, step.id)

    async def resume(
        self,
        execution: RunbookExecution,
        runbook: Runbook,
        from_step_id: str,
        on_progress: ProgressHook | None = None,
    ) -> RunbookExecution | None:
        """Continue a paused execution from `from_step_id` onwards.

        Split out of :meth:`approve` so a caller holding the execution and the
        runbook can resume without going through `_pending_approvals`. That dict
        lives in one process, so an approval that landed on a different replica
        than the one that paused the run could never be actioned — the run simply
        stayed WAITING_APPROVAL forever.

        Returns None if `from_step_id` is not a step of this runbook, which means
        the definition was edited between the pause and the approval; resuming at
        a guessed offset would run the wrong actions.
        """
        continue_from = next(
            (i for i, s in enumerate(runbook.steps) if s.id == from_step_id), None
        )
        if continue_from is None:
            log.warning(
                "Cannot resume execution %s: step %s is no longer in runbook %s",
                execution.id, from_step_id, runbook.id,
            )
            return None

        log.info(
            "Resuming execution %s from step %s", execution.id, from_step_id
        )
        execution.status = StepStatus.RUNNING
        # The pause recorded a placeholder StepExecution for the step it stopped
        # at; drop it so the real result replaces it rather than appearing twice.
        execution.steps = [
            s for s in execution.steps if s.status != StepStatus.WAITING_APPROVAL
        ]
        await _report(on_progress, execution)

        for step in runbook.steps[continue_from:]:
            step_exec = StepExecution(
                step_id=step.id,
                step_name=step.name,
                status=StepStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )

            success, output, error = await self._execute_step(step, execution.context)

            step_exec.completed_at = datetime.now(timezone.utc)
            step_exec.output = output
            step_exec.error = error

            if success:
                step_exec.status = StepStatus.SUCCESS
            else:
                step_exec.status = StepStatus.FAILED
                if not step.continue_on_failure:
                    execution.steps.append(step_exec)
                    execution.status = StepStatus.FAILED
                    execution.completed_at = datetime.now(timezone.utc)
                    await _report(on_progress, execution)
                    return execution

            execution.steps.append(step_exec)
            await _report(on_progress, execution)

        execution.status = StepStatus.SUCCESS
        execution.completed_at = datetime.now(timezone.utc)
        await _report(on_progress, execution)
        return execution

    async def reject(self, execution_id: str) -> RunbookExecution | None:
        """Reject a pending execution."""
        if execution_id not in self._pending_approvals:
            return None

        execution, _ = self._pending_approvals.pop(execution_id)
        execution.status = StepStatus.SKIPPED
        execution.completed_at = datetime.now(timezone.utc)
        log.info("Execution %s rejected", execution_id)
        return execution

    def get_execution(self, execution_id: str) -> RunbookExecution | None:
        """Get an execution by ID."""
        return self._executions.get(execution_id)

    def list_executions(
        self,
        runbook_id: str | None = None,
        status: StepStatus | None = None,
        limit: int = 50,
    ) -> list[RunbookExecution]:
        """List executions with optional filtering."""
        executions = list(self._executions.values())

        if runbook_id:
            executions = [e for e in executions if e.runbook_id == runbook_id]

        if status:
            executions = [e for e in executions if e.status == status]

        return sorted(
            executions,
            key=lambda e: e.started_at,
            reverse=True,
        )[:limit]

    def get_pending_approvals(self) -> list[RunbookExecution]:
        """Get all executions waiting for approval."""
        return [e for e, _ in self._pending_approvals.values()]


_executor: RunbookExecutor | None = None


def get_executor() -> RunbookExecutor:
    """Get the singleton executor instance."""
    global _executor
    if _executor is None:
        _executor = RunbookExecutor()
    return _executor
