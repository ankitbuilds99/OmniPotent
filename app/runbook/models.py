"""Runbook models and data structures.

Defines runbooks, steps, conditions, and execution context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.runbook.models")


class StepType(Enum):
    """Type of runbook step."""

    COMMAND = "command"
    SCRIPT = "script"
    QUERY = "query"
    API_CALL = "api_call"
    APPROVAL = "approval"
    NOTIFICATION = "notification"
    CONDITION = "condition"
    PARALLEL = "parallel"
    WAIT = "wait"
    MANUAL = "manual"


class StepStatus(Enum):
    """Status of a runbook step."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


class ConditionOperator(Enum):
    """Operator for step conditions."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    REGEX_MATCH = "regex_match"
    EXISTS = "exists"


@dataclass
class StepCondition:
    """A condition that determines if a step should execute."""

    variable: str
    operator: ConditionOperator
    value: Any
    description: str = ""

    def evaluate(self, context: dict[str, Any]) -> bool:
        """Evaluate the condition against a context."""
        actual = context.get(self.variable)

        if self.operator == ConditionOperator.EXISTS:
            return actual is not None

        if actual is None:
            return False

        if self.operator == ConditionOperator.EQUALS:
            return actual == self.value
        elif self.operator == ConditionOperator.NOT_EQUALS:
            return actual != self.value
        elif self.operator == ConditionOperator.GREATER_THAN:
            return float(actual) > float(self.value)
        elif self.operator == ConditionOperator.LESS_THAN:
            return float(actual) < float(self.value)
        elif self.operator == ConditionOperator.CONTAINS:
            return str(self.value) in str(actual)
        elif self.operator == ConditionOperator.NOT_CONTAINS:
            return str(self.value) not in str(actual)
        elif self.operator == ConditionOperator.REGEX_MATCH:
            import re
            return bool(re.search(str(self.value), str(actual)))

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "operator": self.operator.value,
            "value": self.value,
            "description": self.description,
        }


@dataclass
class RunbookStep:
    """A step in a runbook."""

    step_id: str
    name: str
    description: str
    step_type: StepType
    command: str = ""
    script: str = ""
    expected_output: str = ""
    success_criteria: str = ""
    timeout_seconds: int = 300
    retry_count: int = 0
    retry_delay_seconds: int = 10
    requires_approval: bool = False
    approvers: list[str] = field(default_factory=list)
    conditions: list[StepCondition] = field(default_factory=list)
    on_failure: str = "stop"
    rollback_command: str = ""
    output_variable: str = ""
    parallel_steps: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "step_type": self.step_type.value,
            "command": self.command,
            "script": self.script,
            "expected_output": self.expected_output,
            "success_criteria": self.success_criteria,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "requires_approval": self.requires_approval,
            "approvers": self.approvers,
            "conditions": [c.to_dict() for c in self.conditions],
            "on_failure": self.on_failure,
            "rollback_command": self.rollback_command,
            "output_variable": self.output_variable,
            "parallel_steps": self.parallel_steps,
            "depends_on": self.depends_on,
            "tags": self.tags,
        }


@dataclass
class StepResult:
    """Result of executing a runbook step."""

    step_id: str
    status: StepStatus
    output: str = ""
    error: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    retry_attempts: int = 0
    approved_by: str = ""
    skipped_reason: str = ""
    context_updates: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == StepStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "retry_attempts": self.retry_attempts,
            "approved_by": self.approved_by,
            "skipped_reason": self.skipped_reason,
            "context_updates": self.context_updates,
        }


@dataclass
class ExecutionContext:
    """Context passed between runbook steps."""

    variables: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    environment: str = "production"
    dry_run: bool = False
    auto_approve: bool = False
    skip_on_failure: bool = False
    execution_id: str = ""
    incident_id: str = ""
    triggered_by: str = ""
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get(self, key: str, default: Any = None) -> Any:
        """Get a variable from context."""
        return self.variables.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a variable in context."""
        self.variables[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "variables": self.variables,
            "environment": self.environment,
            "dry_run": self.dry_run,
            "auto_approve": self.auto_approve,
            "execution_id": self.execution_id,
            "incident_id": self.incident_id,
            "triggered_by": self.triggered_by,
            "triggered_at": self.triggered_at.isoformat(),
        }


@dataclass
class Runbook:
    """A complete runbook definition."""

    runbook_id: str
    name: str
    description: str
    version: str = "1.0.0"
    trigger: str = ""
    severity: str = "medium"
    steps: list[RunbookStep] = field(default_factory=list)
    auto_execute_steps: list[str] = field(default_factory=list)
    estimated_duration_minutes: int = 0
    required_approvers: int = 1
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_executed_at: datetime | None = None
    execution_count: int = 0
    success_rate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_id: str) -> RunbookStep | None:
        """Get a step by ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "runbook_id": self.runbook_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "trigger": self.trigger,
            "severity": self.severity,
            "steps": [s.to_dict() for s in self.steps],
            "auto_execute_steps": self.auto_execute_steps,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "required_approvers": self.required_approvers,
            "tags": self.tags,
            "owner": self.owner,
            "created_at": self.created_at.isoformat(),
            "execution_count": self.execution_count,
            "success_rate": round(self.success_rate, 2),
        }


@dataclass
class RunbookExecution:
    """A runbook execution record."""

    execution_id: str
    runbook_id: str
    runbook_name: str
    status: str = "pending"
    context: ExecutionContext = field(default_factory=ExecutionContext)
    step_results: list[StepResult] = field(default_factory=list)
    current_step_index: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    dry_run: bool = False
    rollback_executed: bool = False
    error: str = ""

    @property
    def progress_percent(self) -> float:
        """Get execution progress as a percentage."""
        if not self.step_results:
            return 0.0
        completed = sum(1 for r in self.step_results if r.status in [
            StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED
        ])
        return (completed / len(self.step_results)) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "runbook_id": self.runbook_id,
            "runbook_name": self.runbook_name,
            "status": self.status,
            "context": self.context.to_dict(),
            "step_results": [r.to_dict() for r in self.step_results],
            "current_step_index": self.current_step_index,
            "progress_percent": round(self.progress_percent, 1),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "dry_run": self.dry_run,
            "rollback_executed": self.rollback_executed,
            "error": self.error,
        }


@dataclass
class ExecutionResult:
    """Final result of a runbook execution."""

    execution: RunbookExecution
    success: bool
    steps_completed: int
    steps_failed: int
    steps_skipped: int
    total_duration_seconds: float
    rollback_needed: bool
    rollback_executed: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution": self.execution.to_dict(),
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
            "total_duration_seconds": round(self.total_duration_seconds, 2),
            "rollback_needed": self.rollback_needed,
            "rollback_executed": self.rollback_executed,
            "summary": self.summary,
        }
