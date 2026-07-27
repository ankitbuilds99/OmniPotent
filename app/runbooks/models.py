"""Runbook data models.

Defines the structure of runbooks, steps, and execution records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ExecutionMode(Enum):
    """How the runbook should be executed."""

    MANUAL = "manual"
    AUTO_APPROVED = "auto_approved"
    REQUIRES_APPROVAL = "requires_approval"


class StepType(Enum):
    """Type of runbook step."""

    COMMAND = "command"
    KUBERNETES = "kubernetes"
    TERRAFORM = "terraform"
    HTTP = "http"
    SLACK = "slack"
    WAIT = "wait"
    CONDITION = "condition"


class StepStatus(Enum):
    """Status of a step execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"


@dataclass
class RunbookStep:
    """A single step in a runbook.

    Attributes:
        id: Unique step identifier
        name: Human-readable step name
        description: What this step does
        step_type: Type of action to perform
        action: The specific action (command, API call, etc.)
        params: Parameters for the action
        timeout_seconds: How long to wait for completion
        continue_on_failure: Whether to continue if this step fails
        requires_approval: Whether this step needs human approval
        rollback_step: ID of step to run on failure
    """

    id: str
    name: str
    description: str
    step_type: StepType
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 300
    continue_on_failure: bool = False
    requires_approval: bool = False
    rollback_step: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "step_type": self.step_type.value,
            "action": self.action,
            "params": self.params,
            "timeout_seconds": self.timeout_seconds,
            "continue_on_failure": self.continue_on_failure,
            "requires_approval": self.requires_approval,
            "rollback_step": self.rollback_step,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunbookStep":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            step_type=StepType(data["step_type"]),
            action=data["action"],
            params=data.get("params", {}),
            timeout_seconds=data.get("timeout_seconds", 300),
            continue_on_failure=data.get("continue_on_failure", False),
            requires_approval=data.get("requires_approval", False),
            rollback_step=data.get("rollback_step"),
        )


@dataclass
class Runbook:
    """A complete runbook definition.

    Attributes:
        id: Unique runbook identifier
        name: Human-readable name
        description: What this runbook does
        service: Service this runbook applies to
        trigger: What triggers this runbook (alert, manual, etc.)
        execution_mode: How the runbook executes
        steps: Ordered list of steps to execute
        labels: Labels for categorization
        enabled: Whether the runbook is active
    """

    id: str
    name: str
    description: str
    service: str
    trigger: str
    execution_mode: ExecutionMode
    steps: list[RunbookStep] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "service": self.service,
            "trigger": self.trigger,
            "execution_mode": self.execution_mode.value,
            "steps": [s.to_dict() for s in self.steps],
            "labels": self.labels,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Runbook":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            service=data.get("service", "*"),
            trigger=data.get("trigger", "manual"),
            execution_mode=ExecutionMode(data.get("execution_mode", "manual")),
            steps=[RunbookStep.from_dict(s) for s in data.get("steps", [])],
            labels=data.get("labels", {}),
            enabled=data.get("enabled", True),
        )


@dataclass
class StepExecution:
    """Record of a step execution."""

    step_id: str
    step_name: str
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    output: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "output": self.output,
            "error": self.error,
        }


@dataclass
class RunbookExecution:
    """Record of a complete runbook execution."""

    id: str
    runbook_id: str
    runbook_name: str
    status: StepStatus
    triggered_by: str
    started_at: datetime
    completed_at: datetime | None = None
    steps: list[StepExecution] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "runbook_id": self.runbook_id,
            "runbook_name": self.runbook_name,
            "status": self.status.value,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "steps": [s.to_dict() for s in self.steps],
            "context": self.context,
        }


# Pre-defined runbooks for common scenarios
DEFAULT_RUNBOOKS: list[dict[str, Any]] = [
    {
        "id": "restart-service",
        "name": "Restart Service",
        "description": "Restart a Kubernetes deployment by rolling out new pods",
        "service": "*",
        "trigger": "manual",
        "execution_mode": "requires_approval",
        "labels": {"category": "remediation", "impact": "medium"},
        "steps": [
            {
                "id": "notify-start",
                "name": "Notify Start",
                "description": "Post notification that restart is beginning",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Starting service restart for ${service}"},
            },
            {
                "id": "rollout-restart",
                "name": "Rollout Restart",
                "description": "Trigger Kubernetes rollout restart",
                "step_type": "kubernetes",
                "action": "rollout_restart",
                "params": {"deployment": "${service}", "namespace": "${namespace}"},
                "requires_approval": True,
            },
            {
                "id": "wait-healthy",
                "name": "Wait for Healthy",
                "description": "Wait for deployment to become healthy",
                "step_type": "kubernetes",
                "action": "wait_rollout",
                "params": {"deployment": "${service}", "timeout": 300},
            },
            {
                "id": "notify-complete",
                "name": "Notify Complete",
                "description": "Post notification that restart is complete",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Service restart complete for ${service}"},
            },
        ],
    },
    {
        "id": "scale-up",
        "name": "Scale Up Deployment",
        "description": "Increase replica count to handle load",
        "service": "*",
        "trigger": "alert:high-cpu-usage",
        "execution_mode": "auto_approved",
        "labels": {"category": "scaling", "impact": "low"},
        "steps": [
            {
                "id": "get-current",
                "name": "Get Current Replicas",
                "description": "Check current replica count",
                "step_type": "kubernetes",
                "action": "get_deployment",
                "params": {"deployment": "${service}"},
            },
            {
                "id": "scale",
                "name": "Scale Deployment",
                "description": "Increase replica count by 2",
                "step_type": "kubernetes",
                "action": "scale",
                "params": {"deployment": "${service}", "replicas": "+2"},
            },
            {
                "id": "notify",
                "name": "Notify",
                "description": "Notify about scaling action",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Scaled up ${service} by 2 replicas due to high CPU"},
            },
        ],
    },
    {
        "id": "rollback-deployment",
        "name": "Rollback Deployment",
        "description": "Rollback to previous deployment revision",
        "service": "*",
        "trigger": "alert:deployment-failed",
        "execution_mode": "requires_approval",
        "labels": {"category": "remediation", "impact": "high"},
        "steps": [
            {
                "id": "get-history",
                "name": "Get Rollout History",
                "description": "Check deployment revision history",
                "step_type": "kubernetes",
                "action": "rollout_history",
                "params": {"deployment": "${service}"},
            },
            {
                "id": "rollback",
                "name": "Rollback",
                "description": "Rollback to previous revision",
                "step_type": "kubernetes",
                "action": "rollout_undo",
                "params": {"deployment": "${service}"},
                "requires_approval": True,
            },
            {
                "id": "verify",
                "name": "Verify Rollback",
                "description": "Verify deployment is healthy after rollback",
                "step_type": "kubernetes",
                "action": "wait_rollout",
                "params": {"deployment": "${service}", "timeout": 300},
            },
            {
                "id": "notify",
                "name": "Notify",
                "description": "Notify about rollback",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Rolled back ${service} to previous version"},
            },
        ],
    },
    {
        "id": "drain-node",
        "name": "Drain Kubernetes Node",
        "description": "Safely drain a node for maintenance",
        "service": "*",
        "trigger": "manual",
        "execution_mode": "requires_approval",
        "labels": {"category": "maintenance", "impact": "high"},
        "steps": [
            {
                "id": "cordon",
                "name": "Cordon Node",
                "description": "Mark node as unschedulable",
                "step_type": "kubernetes",
                "action": "cordon_node",
                "params": {"node": "${node}"},
            },
            {
                "id": "drain",
                "name": "Drain Node",
                "description": "Evict pods from node",
                "step_type": "kubernetes",
                "action": "drain_node",
                "params": {"node": "${node}", "grace_period": 60},
                "requires_approval": True,
                "timeout_seconds": 600,
            },
            {
                "id": "notify",
                "name": "Notify",
                "description": "Notify that node is drained",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Node ${node} has been drained and is ready for maintenance"},
            },
        ],
    },
    {
        "id": "clear-cache",
        "name": "Clear Redis Cache",
        "description": "Flush Redis cache to resolve stale data issues",
        "service": "*",
        "trigger": "manual",
        "execution_mode": "requires_approval",
        "labels": {"category": "remediation", "impact": "medium"},
        "steps": [
            {
                "id": "notify-start",
                "name": "Notify Start",
                "description": "Warn about upcoming cache clear",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Clearing Redis cache for ${service} in 10 seconds..."},
            },
            {
                "id": "wait",
                "name": "Wait",
                "description": "Brief pause before clearing",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 10},
            },
            {
                "id": "flush",
                "name": "Flush Cache",
                "description": "Execute cache flush command",
                "step_type": "command",
                "action": "redis-cli FLUSHDB",
                "requires_approval": True,
            },
            {
                "id": "notify-complete",
                "name": "Notify Complete",
                "description": "Confirm cache cleared",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "Redis cache cleared for ${service}"},
            },
        ],
    },
]
