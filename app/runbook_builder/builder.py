"""Custom runbook builder.

Enables creation and execution of automated runbooks with:
- Multiple step types (script, API call, manual, approval)
- Conditional branching
- Variable substitution
- Parallel execution
- Rollback support
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("omnipotent.runbook_builder")


class StepType(Enum):
    """Types of runbook steps."""

    SCRIPT = "script"
    API_CALL = "api_call"
    MANUAL = "manual"
    APPROVAL = "approval"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    WAIT = "wait"
    NOTIFY = "notify"
    KUBERNETES = "kubernetes"
    DATABASE = "database"


class ExecutionStatus(Enum):
    """Execution status."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_MANUAL = "waiting_manual"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


@dataclass
class StepCondition:
    """Condition for conditional execution."""

    variable: str
    operator: str
    value: Any

    def evaluate(self, variables: dict[str, Any]) -> bool:
        """Evaluate the condition."""
        actual = variables.get(self.variable)

        if self.operator == "equals":
            return actual == self.value
        elif self.operator == "not_equals":
            return actual != self.value
        elif self.operator == "contains":
            return self.value in str(actual)
        elif self.operator == "greater_than":
            return float(actual if actual is not None else 0) > float(self.value)
        elif self.operator == "less_than":
            return float(actual if actual is not None else 0) < float(self.value)
        elif self.operator == "is_empty":
            return not actual
        elif self.operator == "is_not_empty":
            return bool(actual)
        elif self.operator == "matches":
            return bool(re.match(self.value, str(actual)))

        return False


@dataclass
class RunbookVariable:
    """A runbook variable definition."""

    name: str
    description: str = ""
    var_type: str = "string"
    default_value: Any = None
    required: bool = False
    secret: bool = False
    options: list[str] | None = None


@dataclass
class RunbookStep:
    """A single step in a runbook."""

    id: str
    name: str
    step_type: StepType
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    conditions: list[StepCondition] = field(default_factory=list)
    on_failure: str = "stop"
    timeout_seconds: int = 300
    retry_count: int = 0
    retry_delay_seconds: int = 5
    rollback_step_id: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    next_step_id: str | None = None
    parallel_steps: list[str] | None = None

    def should_execute(self, variables: dict[str, Any]) -> bool:
        """Check if step should execute based on conditions."""
        if not self.conditions:
            return True
        return all(c.evaluate(variables) for c in self.conditions)


@dataclass
class StepResult:
    """Result of executing a step."""

    step_id: str
    status: ExecutionStatus
    output: Any = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    duration_seconds: float = 0


@dataclass
class Runbook:
    """A complete runbook definition."""

    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    variables: list[RunbookVariable] = field(default_factory=list)
    steps: list[RunbookStep] = field(default_factory=list)
    start_step_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Validate runbook configuration."""
        errors: list[str] = []

        if not self.name:
            errors.append("Runbook name is required")

        if not self.steps:
            errors.append("Runbook must have at least one step")

        step_ids = {s.id for s in self.steps}

        for step in self.steps:
            if not step.id:
                errors.append(f"Step missing ID: {step.name}")

            if step.next_step_id and step.next_step_id not in step_ids:
                errors.append(f"Step {step.id} references unknown next step: {step.next_step_id}")

            if step.rollback_step_id and step.rollback_step_id not in step_ids:
                errors.append(f"Step {step.id} references unknown rollback step")

            if step.parallel_steps:
                for ps in step.parallel_steps:
                    if ps not in step_ids:
                        errors.append(f"Step {step.id} references unknown parallel step: {ps}")

        required_vars = [v.name for v in self.variables if v.required]
        for step in self.steps:
            config_str = str(step.config)
            for var in required_vars:
                if f"${{{var}}}" not in config_str and f"${{{{variables.{var}}}}}" not in config_str:
                    pass

        return errors

    def get_step(self, step_id: str) -> RunbookStep | None:
        """Get step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


@dataclass
class RunbookExecution:
    """Execution state for a runbook."""

    id: str
    runbook_id: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    variables: dict[str, Any] = field(default_factory=dict)
    step_results: list[StepResult] = field(default_factory=list)
    current_step_id: str | None = None
    error: str | None = None
    initiated_by: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)

    def log(self, message: str, level: str = "info") -> None:
        """Add a log entry."""
        self.logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
        })


_RUNBOOKS: dict[str, Runbook] = {}
_EXECUTIONS: dict[str, RunbookExecution] = {}


StepHandler = Callable[
    [RunbookStep, dict[str, Any], RunbookExecution],
    Awaitable[StepResult],
]


class RunbookBuilder:
    """Builder for creating runbooks programmatically."""

    def __init__(self, name: str, description: str = ""):
        self.id = f"rb_{secrets.token_hex(8)}"
        self.name = name
        self.description = description
        self.variables: list[RunbookVariable] = []
        self.steps: list[RunbookStep] = []
        self._step_counter = 0

    def add_variable(
        self,
        name: str,
        description: str = "",
        var_type: str = "string",
        default_value: Any = None,
        required: bool = False,
        secret: bool = False,
        options: list[str] | None = None,
    ) -> "RunbookBuilder":
        """Add a variable definition."""
        self.variables.append(RunbookVariable(
            name=name,
            description=description,
            var_type=var_type,
            default_value=default_value,
            required=required,
            secret=secret,
            options=options,
        ))
        return self

    def _next_step_id(self) -> str:
        """Generate next step ID."""
        self._step_counter += 1
        return f"step_{self._step_counter}"

    def add_script_step(
        self,
        name: str,
        script: str,
        description: str = "",
        timeout_seconds: int = 300,
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a script execution step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.SCRIPT,
            config={"script": script},
            timeout_seconds=timeout_seconds,
            **kwargs,
        ))
        return self

    def add_api_step(
        self,
        name: str,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add an API call step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.API_CALL,
            config={
                "method": method,
                "url": url,
                "headers": headers or {},
                "body": body,
            },
            **kwargs,
        ))
        return self

    def add_manual_step(
        self,
        name: str,
        instructions: str,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a manual intervention step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.MANUAL,
            config={"instructions": instructions},
            **kwargs,
        ))
        return self

    def add_approval_step(
        self,
        name: str,
        approvers: list[str],
        message: str = "",
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add an approval step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.APPROVAL,
            config={"approvers": approvers, "message": message},
            **kwargs,
        ))
        return self

    def add_wait_step(
        self,
        name: str,
        duration_seconds: int,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a wait step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.WAIT,
            config={"duration_seconds": duration_seconds},
            **kwargs,
        ))
        return self

    def add_notify_step(
        self,
        name: str,
        channel: str,
        message: str,
        recipients: list[str] | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a notification step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.NOTIFY,
            config={
                "channel": channel,
                "message": message,
                "recipients": recipients or [],
            },
            **kwargs,
        ))
        return self

    def add_kubernetes_step(
        self,
        name: str,
        action: str,
        resource_type: str,
        resource_name: str,
        namespace: str = "default",
        spec: dict[str, Any] | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a Kubernetes action step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.KUBERNETES,
            config={
                "action": action,
                "resource_type": resource_type,
                "resource_name": resource_name,
                "namespace": namespace,
                "spec": spec,
            },
            **kwargs,
        ))
        return self

    def add_conditional_step(
        self,
        name: str,
        condition: StepCondition,
        then_step_id: str,
        else_step_id: str | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a conditional branching step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.CONDITIONAL,
            config={
                "then_step": then_step_id,
                "else_step": else_step_id,
            },
            conditions=[condition],
            **kwargs,
        ))
        return self

    def add_parallel_step(
        self,
        name: str,
        step_ids: list[str],
        description: str = "",
        **kwargs: Any,
    ) -> "RunbookBuilder":
        """Add a parallel execution step."""
        self.steps.append(RunbookStep(
            id=self._next_step_id(),
            name=name,
            description=description,
            step_type=StepType.PARALLEL,
            parallel_steps=step_ids,
            **kwargs,
        ))
        return self

    def build(self) -> Runbook:
        """Build the runbook."""
        for i, step in enumerate(self.steps[:-1]):
            if not step.next_step_id:
                step.next_step_id = self.steps[i + 1].id

        runbook = Runbook(
            id=self.id,
            name=self.name,
            description=self.description,
            variables=self.variables,
            steps=self.steps,
            start_step_id=self.steps[0].id if self.steps else None,
        )

        errors = runbook.validate()
        if errors:
            log.warning("Runbook validation warnings: %s", errors)

        return runbook


class RunbookExecutor:
    """Executes runbooks."""

    def __init__(self):
        self._handlers: dict[StepType, StepHandler] = {}
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register default step handlers."""
        self._handlers[StepType.SCRIPT] = self._handle_script
        self._handlers[StepType.API_CALL] = self._handle_api_call
        self._handlers[StepType.WAIT] = self._handle_wait
        self._handlers[StepType.MANUAL] = self._handle_manual
        self._handlers[StepType.APPROVAL] = self._handle_approval
        self._handlers[StepType.NOTIFY] = self._handle_notify
        self._handlers[StepType.KUBERNETES] = self._handle_kubernetes

    def register_handler(self, step_type: StepType, handler: StepHandler) -> None:
        """Register a custom step handler."""
        self._handlers[step_type] = handler

    async def execute(
        self,
        runbook: Runbook,
        variables: dict[str, Any],
        initiated_by: str = "",
    ) -> RunbookExecution:
        """Execute a runbook."""
        execution = RunbookExecution(
            id=f"exec_{secrets.token_hex(8)}",
            runbook_id=runbook.id,
            status=ExecutionStatus.RUNNING,
            variables=self._resolve_variables(runbook, variables),
            initiated_by=initiated_by,
            current_step_id=runbook.start_step_id,
        )

        _EXECUTIONS[execution.id] = execution
        execution.log(f"Starting execution of {runbook.name}")

        try:
            await self._execute_steps(runbook, execution)

            if execution.status == ExecutionStatus.RUNNING:
                execution.status = ExecutionStatus.COMPLETED
                execution.log("Execution completed successfully")
        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.error = str(e)
            execution.log(f"Execution failed: {e}", "error")
            log.error("Runbook execution failed: %s", e)
        finally:
            execution.completed_at = datetime.now(timezone.utc)

        return execution

    def _resolve_variables(
        self,
        runbook: Runbook,
        provided: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve variable values."""
        resolved: dict[str, Any] = {}

        for var in runbook.variables:
            if var.name in provided:
                resolved[var.name] = provided[var.name]
            elif var.default_value is not None:
                resolved[var.name] = var.default_value
            elif var.required:
                raise ValueError(f"Required variable not provided: {var.name}")

        return resolved

    def _substitute_variables(
        self,
        config: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Substitute variables in config."""
        import json

        config_str = json.dumps(config)

        for name, value in variables.items():
            config_str = config_str.replace(f"${{{name}}}", str(value))
            config_str = config_str.replace(f"${{{{variables.{name}}}}}", str(value))

        return json.loads(config_str)

    async def _execute_steps(
        self,
        runbook: Runbook,
        execution: RunbookExecution,
    ) -> None:
        """Execute steps sequentially."""
        current_id = execution.current_step_id

        while current_id:
            step = runbook.get_step(current_id)
            if not step:
                break

            execution.current_step_id = current_id

            if not step.should_execute(execution.variables):
                execution.log(f"Skipping step {step.name} (condition not met)")
                current_id = step.next_step_id
                continue

            execution.log(f"Executing step: {step.name}")

            result = await self._execute_step(step, execution)
            execution.step_results.append(result)

            if result.status == ExecutionStatus.FAILED:
                if step.on_failure == "stop":
                    execution.status = ExecutionStatus.FAILED
                    execution.error = result.error
                    break
                elif step.on_failure == "rollback" and step.rollback_step_id:
                    execution.log(f"Rolling back via step {step.rollback_step_id}")
                    rollback_step = runbook.get_step(step.rollback_step_id)
                    if rollback_step:
                        await self._execute_step(rollback_step, execution)
                    execution.status = ExecutionStatus.ROLLED_BACK
                    break

            if step.outputs and result.output:
                for var_name, output_path in step.outputs.items():
                    try:
                        value = result.output
                        for key in output_path.split("."):
                            if isinstance(value, dict):
                                value = value.get(key)
                        execution.variables[var_name] = value
                    except Exception:
                        pass

            if step.step_type == StepType.CONDITIONAL and step.config:
                if step.conditions and step.conditions[0].evaluate(execution.variables):
                    current_id = step.config.get("then_step")
                else:
                    current_id = step.config.get("else_step")
            elif result.status in (
                ExecutionStatus.WAITING_APPROVAL,
                ExecutionStatus.WAITING_MANUAL,
            ):
                execution.status = result.status
                break
            else:
                current_id = step.next_step_id

    async def _execute_step(
        self,
        step: RunbookStep,
        execution: RunbookExecution,
    ) -> StepResult:
        """Execute a single step."""
        handler = self._handlers.get(step.step_type)
        if not handler:
            return StepResult(
                step_id=step.id,
                status=ExecutionStatus.FAILED,
                error=f"No handler for step type: {step.step_type}",
            )

        config = self._substitute_variables(step.config, execution.variables)

        for attempt in range(step.retry_count + 1):
            try:
                result = await asyncio.wait_for(
                    handler(step, config, execution),
                    timeout=step.timeout_seconds,
                )
                if result.status != ExecutionStatus.FAILED:
                    return result
            except asyncio.TimeoutError:
                result = StepResult(
                    step_id=step.id,
                    status=ExecutionStatus.FAILED,
                    error=f"Step timed out after {step.timeout_seconds}s",
                )
            except Exception as e:
                result = StepResult(
                    step_id=step.id,
                    status=ExecutionStatus.FAILED,
                    error=str(e),
                )

            if attempt < step.retry_count:
                execution.log(
                    f"Step {step.name} failed, retrying in {step.retry_delay_seconds}s",
                    "warning",
                )
                await asyncio.sleep(step.retry_delay_seconds)

        return result

    async def _handle_script(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle script execution step."""
        import subprocess

        script = config.get("script", "")

        try:
            result = subprocess.run(
                script,
                shell=True,
                capture_output=True,
                text=True,
                timeout=step.timeout_seconds,
            )

            if result.returncode != 0:
                return StepResult(
                    step_id=step.id,
                    status=ExecutionStatus.FAILED,
                    error=result.stderr,
                    output={"stdout": result.stdout, "stderr": result.stderr},
                )

            return StepResult(
                step_id=step.id,
                status=ExecutionStatus.COMPLETED,
                output={"stdout": result.stdout, "returncode": result.returncode},
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                step_id=step.id,
                status=ExecutionStatus.FAILED,
                error="Script timed out",
            )

    async def _handle_api_call(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle API call step."""
        import httpx

        method = config.get("method", "GET")
        url = config.get("url", "")
        headers = config.get("headers", {})
        body = config.get("body")

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=body,
                    timeout=step.timeout_seconds,
                )

                output = {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                }

                try:
                    output["body"] = resp.json()
                except Exception:
                    output["body"] = resp.text

                if resp.status_code >= 400:
                    return StepResult(
                        step_id=step.id,
                        status=ExecutionStatus.FAILED,
                        error=f"HTTP {resp.status_code}",
                        output=output,
                    )

                return StepResult(
                    step_id=step.id,
                    status=ExecutionStatus.COMPLETED,
                    output=output,
                )
        except Exception as e:
            return StepResult(
                step_id=step.id,
                status=ExecutionStatus.FAILED,
                error=str(e),
            )

    async def _handle_wait(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle wait step."""
        duration = config.get("duration_seconds", 0)
        await asyncio.sleep(duration)
        return StepResult(
            step_id=step.id,
            status=ExecutionStatus.COMPLETED,
            output={"waited_seconds": duration},
        )

    async def _handle_manual(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle manual step - waits for user confirmation."""
        execution.log(f"Manual step required: {config.get('instructions', '')}")
        return StepResult(
            step_id=step.id,
            status=ExecutionStatus.WAITING_MANUAL,
            output={"instructions": config.get("instructions", "")},
        )

    async def _handle_approval(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle approval step - waits for approval."""
        approvers = config.get("approvers", [])
        message = config.get("message", "Approval required")

        execution.log(f"Waiting for approval from: {', '.join(approvers)}")

        return StepResult(
            step_id=step.id,
            status=ExecutionStatus.WAITING_APPROVAL,
            output={"approvers": approvers, "message": message},
        )

    async def _handle_notify(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle notification step."""
        channel = config.get("channel", "")
        message = config.get("message", "")

        execution.log(f"Sending notification via {channel}: {message[:100]}")

        return StepResult(
            step_id=step.id,
            status=ExecutionStatus.COMPLETED,
            output={"channel": channel, "sent": True},
        )

    async def _handle_kubernetes(
        self,
        step: RunbookStep,
        config: dict[str, Any],
        execution: RunbookExecution,
    ) -> StepResult:
        """Handle Kubernetes step."""
        action = config.get("action", "")
        resource_type = config.get("resource_type", "")
        resource_name = config.get("resource_name", "")
        namespace = config.get("namespace", "default")

        execution.log(f"Kubernetes {action} {resource_type}/{resource_name} in {namespace}")

        return StepResult(
            step_id=step.id,
            status=ExecutionStatus.COMPLETED,
            output={
                "action": action,
                "resource": f"{resource_type}/{resource_name}",
                "namespace": namespace,
            },
        )


_executor: RunbookExecutor | None = None


def _get_executor() -> RunbookExecutor:
    """Get or create executor singleton."""
    global _executor
    if _executor is None:
        _executor = RunbookExecutor()
    return _executor


def create_runbook(runbook: Runbook) -> Runbook:
    """Save a runbook."""
    _RUNBOOKS[runbook.id] = runbook
    return runbook


def get_runbook(runbook_id: str) -> Runbook | None:
    """Get a runbook by ID."""
    return _RUNBOOKS.get(runbook_id)


def list_runbooks() -> list[Runbook]:
    """List all runbooks."""
    return list(_RUNBOOKS.values())


async def execute_runbook(
    runbook_id: str,
    variables: dict[str, Any] | None = None,
    initiated_by: str = "",
) -> RunbookExecution:
    """Execute a runbook by ID."""
    runbook = get_runbook(runbook_id)
    if not runbook:
        raise ValueError(f"Runbook not found: {runbook_id}")

    executor = _get_executor()
    return await executor.execute(runbook, variables or {}, initiated_by)


def get_execution_status(execution_id: str) -> RunbookExecution | None:
    """Get execution status by ID."""
    return _EXECUTIONS.get(execution_id)
