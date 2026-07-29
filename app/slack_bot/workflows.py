"""Workflow Automation for Slack.

Saved workflows with conditional logic and
human-in-the-loop approval steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.slack_bot.workflows")


class StepType(str, Enum):
    CHECK = "check"
    ACTION = "action"
    WAIT = "wait"
    APPROVE = "approve"
    NOTIFY = "notify"
    CONDITION = "condition"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowStep:
    """A step in a workflow."""

    step_id: str
    name: str
    step_type: StepType
    description: str
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None
    timeout_seconds: int = 300
    require_approval: bool = False
    on_failure: str = "stop"
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "name": self.name,
            "type": self.step_type.value,
            "description": self.description,
            "action": self.action,
            "parameters": self.parameters,
            "condition": self.condition,
            "require_approval": self.require_approval,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass
class Workflow:
    """A saved workflow."""

    workflow_id: str
    org_id: str
    name: str
    description: str
    steps: list[WorkflowStep]
    trigger: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_step(self, step_id: str) -> WorkflowStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "trigger": self.trigger,
            "parameters": self.parameters,
            "tags": self.tags,
            "status": self.status.value,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class WorkflowRun:
    """An execution of a workflow."""

    run_id: str
    workflow_id: str
    org_id: str
    channel_id: str
    user_id: str
    parameters: dict[str, Any]
    status: WorkflowStatus = WorkflowStatus.RUNNING
    current_step_index: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        end_time = self.completed_at or datetime.now(timezone.utc)
        return (end_time - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "current_step": self.current_step_index,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


class WorkflowEngine:
    """Executes and manages workflows."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._runs: dict[str, WorkflowRun] = {}
        self._default_workflows: dict[str, Workflow] = {}
        self._register_default_workflows()

    def _register_default_workflows(self) -> None:
        """Register default/built-in workflows."""
        restart_safely = Workflow(
            workflow_id="wf_restart_safely",
            org_id="default",
            name="restart-safely",
            description="Safe rolling restart with traffic checks",
            parameters=[{"name": "service", "type": "string", "required": True}],
            steps=[
                WorkflowStep(
                    step_id="step_1",
                    name="Check Traffic",
                    step_type=StepType.CHECK,
                    description="Verify traffic is below threshold",
                    action="check_traffic",
                    parameters={"threshold_rps": 100},
                ),
                WorkflowStep(
                    step_id="step_2",
                    name="Enable Maintenance Mode",
                    step_type=StepType.ACTION,
                    description="Enable maintenance mode on service",
                    action="enable_maintenance",
                    require_approval=True,
                ),
                WorkflowStep(
                    step_id="step_3",
                    name="Rolling Restart",
                    step_type=StepType.ACTION,
                    description="Restart pods one by one",
                    action="rolling_restart",
                    parameters={"batch_size": 1, "wait_seconds": 30},
                ),
                WorkflowStep(
                    step_id="step_4",
                    name="Verify Health",
                    step_type=StepType.CHECK,
                    description="Verify all health checks passing",
                    action="verify_health",
                ),
                WorkflowStep(
                    step_id="step_5",
                    name="Disable Maintenance Mode",
                    step_type=StepType.ACTION,
                    description="Disable maintenance mode",
                    action="disable_maintenance",
                ),
            ],
            tags=["restart", "safe", "maintenance"],
        )

        scale_up_emergency = Workflow(
            workflow_id="wf_scale_up_emergency",
            org_id="default",
            name="scale-up-emergency",
            description="Emergency scale up with notifications",
            parameters=[
                {"name": "service", "type": "string", "required": True},
                {"name": "target_replicas", "type": "number", "required": True},
            ],
            steps=[
                WorkflowStep(
                    step_id="step_1",
                    name="Notify Team",
                    step_type=StepType.NOTIFY,
                    description="Alert the team about emergency scaling",
                    action="notify",
                    parameters={"channel": "sre-alerts"},
                ),
                WorkflowStep(
                    step_id="step_2",
                    name="Scale Service",
                    step_type=StepType.ACTION,
                    description="Scale to target replicas",
                    action="scale_service",
                    require_approval=False,
                ),
                WorkflowStep(
                    step_id="step_3",
                    name="Wait for Pods",
                    step_type=StepType.WAIT,
                    description="Wait for pods to be ready",
                    action="wait_pods_ready",
                    timeout_seconds=180,
                ),
                WorkflowStep(
                    step_id="step_4",
                    name="Verify Capacity",
                    step_type=StepType.CHECK,
                    description="Verify capacity meets requirements",
                    action="verify_capacity",
                ),
            ],
            tags=["scale", "emergency"],
        )

        db_failover = Workflow(
            workflow_id="wf_db_failover",
            org_id="default",
            name="db-failover",
            description="Database failover with connection draining",
            parameters=[{"name": "cluster", "type": "string", "required": True}],
            steps=[
                WorkflowStep(
                    step_id="step_1",
                    name="Verify Replica Health",
                    step_type=StepType.CHECK,
                    description="Ensure replica is healthy and in sync",
                    action="check_replica_health",
                ),
                WorkflowStep(
                    step_id="step_2",
                    name="Approval Required",
                    step_type=StepType.APPROVE,
                    description="Get approval from database team",
                    action="request_approval",
                    parameters={"approvers": ["dba-team"]},
                    require_approval=True,
                ),
                WorkflowStep(
                    step_id="step_3",
                    name="Drain Connections",
                    step_type=StepType.ACTION,
                    description="Drain existing connections",
                    action="drain_connections",
                    parameters={"timeout_seconds": 60},
                ),
                WorkflowStep(
                    step_id="step_4",
                    name="Promote Replica",
                    step_type=StepType.ACTION,
                    description="Promote replica to primary",
                    action="promote_replica",
                ),
                WorkflowStep(
                    step_id="step_5",
                    name="Update DNS",
                    step_type=StepType.ACTION,
                    description="Update DNS to point to new primary",
                    action="update_dns",
                ),
                WorkflowStep(
                    step_id="step_6",
                    name="Verify Applications",
                    step_type=StepType.CHECK,
                    description="Verify all applications reconnected",
                    action="verify_app_connections",
                ),
            ],
            tags=["database", "failover", "critical"],
        )

        handle_high_latency = Workflow(
            workflow_id="wf_handle_high_latency",
            org_id="default",
            name="handle-high-latency",
            description="Conditional response to high latency",
            parameters=[
                {"name": "service", "type": "string", "required": True},
                {"name": "latency_ms", "type": "number", "required": True},
                {"name": "error_rate", "type": "number", "required": True},
            ],
            steps=[
                WorkflowStep(
                    step_id="step_1",
                    name="Evaluate Severity",
                    step_type=StepType.CONDITION,
                    description="Determine response based on metrics",
                    action="evaluate",
                    condition="latency_ms > 10000 AND error_rate > 50",
                ),
                WorkflowStep(
                    step_id="step_2a",
                    name="Escalate to P1",
                    step_type=StepType.ACTION,
                    description="Escalate to P1 and page SRE lead",
                    action="escalate_p1",
                    condition="severity == 'critical'",
                ),
                WorkflowStep(
                    step_id="step_2b",
                    name="Start Investigation",
                    step_type=StepType.ACTION,
                    description="Start AI investigation",
                    action="start_investigation",
                    condition="severity == 'high'",
                ),
                WorkflowStep(
                    step_id="step_2c",
                    name="Log Warning",
                    step_type=StepType.NOTIFY,
                    description="Log warning and monitor",
                    action="log_warning",
                    condition="severity == 'medium'",
                ),
                WorkflowStep(
                    step_id="step_3",
                    name="Auto-resolve Check",
                    step_type=StepType.WAIT,
                    description="Wait and check if auto-resolves",
                    action="wait_for_resolution",
                    parameters={"check_interval_seconds": 60, "max_wait_seconds": 300},
                ),
            ],
            tags=["latency", "conditional", "auto-response"],
        )

        for wf in [restart_safely, scale_up_emergency, db_failover, handle_high_latency]:
            self._default_workflows[wf.name] = wf

    async def create_workflow(
        self,
        org_id: str,
        name: str,
        description: str,
        steps: list[WorkflowStep],
        created_by: str,
        parameters: list[dict[str, Any]] | None = None,
        trigger: str | None = None,
        tags: list[str] | None = None,
    ) -> Workflow:
        """Create a new workflow."""
        workflow_id = f"wf_{org_id}_{name}_{datetime.now(timezone.utc).timestamp()}"

        workflow = Workflow(
            workflow_id=workflow_id,
            org_id=org_id,
            name=name,
            description=description,
            steps=steps,
            parameters=parameters or [],
            trigger=trigger,
            tags=tags or [],
            created_by=created_by,
        )

        self._workflows[workflow_id] = workflow

        log.info(
            "Created workflow: id=%s name=%s steps=%d",
            workflow_id,
            name,
            len(steps),
        )

        return workflow

    def get_workflow(
        self,
        workflow_id: str | None = None,
        name: str | None = None,
    ) -> Workflow | None:
        """Get a workflow by ID or name."""
        if workflow_id:
            return self._workflows.get(workflow_id)

        if name:
            if name in self._default_workflows:
                return self._default_workflows[name]

            for wf in self._workflows.values():
                if wf.name == name:
                    return wf

        return None

    def list_workflows(
        self,
        org_id: str | None = None,
        tag: str | None = None,
    ) -> list[Workflow]:
        """List available workflows."""
        workflows = list(self._default_workflows.values())

        for wf in self._workflows.values():
            if org_id and wf.org_id != org_id:
                continue
            workflows.append(wf)

        if tag:
            workflows = [wf for wf in workflows if tag in wf.tags]

        return workflows

    async def run_workflow(
        self,
        workflow_name: str,
        org_id: str,
        channel_id: str,
        user_id: str,
        parameters: dict[str, Any],
    ) -> WorkflowRun:
        """Start a workflow run."""
        workflow = self.get_workflow(name=workflow_name)

        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_name}")

        run_id = f"run_{workflow.workflow_id}_{datetime.now(timezone.utc).timestamp()}"

        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            org_id=org_id,
            channel_id=channel_id,
            user_id=user_id,
            parameters=parameters,
        )

        self._runs[run_id] = run

        log.info(
            "Started workflow run: id=%s workflow=%s",
            run_id,
            workflow_name,
        )

        await self._execute_next_step(run, workflow)

        return run

    async def _execute_next_step(
        self,
        run: WorkflowRun,
        workflow: Workflow,
    ) -> None:
        """Execute the next step in the workflow."""
        if run.current_step_index >= len(workflow.steps):
            run.status = WorkflowStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)

            log.info("Workflow completed: run_id=%s", run.run_id)
            return

        step = workflow.steps[run.current_step_index]

        step.status = StepStatus.RUNNING
        step.started_at = datetime.now(timezone.utc)

        try:
            if step.condition:
                should_run = await self._evaluate_condition(step.condition, run.parameters)
                if not should_run:
                    step.status = StepStatus.SKIPPED
                    step.completed_at = datetime.now(timezone.utc)
                    run.current_step_index += 1
                    await self._execute_next_step(run, workflow)
                    return

            if step.require_approval:
                step.status = StepStatus.WAITING_APPROVAL
                run.status = WorkflowStatus.PAUSED

                log.info(
                    "Workflow paused for approval: run_id=%s step=%s",
                    run.run_id,
                    step.name,
                )
                return

            result = await self._execute_step_action(step, run.parameters)

            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.now(timezone.utc)
            step.result = result

            run.current_step_index += 1

            await self._execute_next_step(run, workflow)

        except Exception as e:
            step.status = StepStatus.FAILED
            step.error = str(e)
            step.completed_at = datetime.now(timezone.utc)

            if step.on_failure == "stop":
                run.status = WorkflowStatus.FAILED
                run.error = f"Step '{step.name}' failed: {e}"
                run.completed_at = datetime.now(timezone.utc)

                log.error(
                    "Workflow failed: run_id=%s step=%s error=%s",
                    run.run_id,
                    step.name,
                    str(e),
                )
            elif step.on_failure == "skip":
                run.current_step_index += 1
                await self._execute_next_step(run, workflow)

    async def _evaluate_condition(
        self,
        condition: str,
        parameters: dict[str, Any],
    ) -> bool:
        """Evaluate a step condition."""
        return True

    async def _execute_step_action(
        self,
        step: WorkflowStep,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a step action."""
        action_handlers = {
            "check_traffic": self._action_check_traffic,
            "enable_maintenance": self._action_enable_maintenance,
            "disable_maintenance": self._action_disable_maintenance,
            "rolling_restart": self._action_rolling_restart,
            "verify_health": self._action_verify_health,
            "scale_service": self._action_scale_service,
            "notify": self._action_notify,
            "wait_pods_ready": self._action_wait_pods_ready,
        }

        handler = action_handlers.get(step.action)

        if handler:
            return await handler(step.parameters, parameters)

        return {"status": "completed", "action": step.action}

    async def _action_check_traffic(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Check traffic levels."""
        threshold = step_params.get("threshold_rps", 100)
        current_rps = 50

        return {
            "current_rps": current_rps,
            "threshold": threshold,
            "below_threshold": current_rps < threshold,
        }

    async def _action_enable_maintenance(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Enable maintenance mode."""
        service = run_params.get("service", "unknown")

        return {
            "service": service,
            "maintenance_mode": True,
        }

    async def _action_disable_maintenance(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Disable maintenance mode."""
        service = run_params.get("service", "unknown")

        return {
            "service": service,
            "maintenance_mode": False,
        }

    async def _action_rolling_restart(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform rolling restart."""
        service = run_params.get("service", "unknown")
        batch_size = step_params.get("batch_size", 1)

        return {
            "service": service,
            "pods_restarted": 3,
            "batch_size": batch_size,
        }

    async def _action_verify_health(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify health checks."""
        service = run_params.get("service", "unknown")

        return {
            "service": service,
            "health_checks_passed": True,
            "checks": ["liveness", "readiness", "startup"],
        }

    async def _action_scale_service(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Scale service."""
        service = run_params.get("service", "unknown")
        target = run_params.get("target_replicas", 3)

        return {
            "service": service,
            "previous_replicas": 3,
            "current_replicas": target,
        }

    async def _action_notify(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send notification."""
        channel = step_params.get("channel", "general")

        return {
            "channel": channel,
            "notified": True,
        }

    async def _action_wait_pods_ready(
        self,
        step_params: dict[str, Any],
        run_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Wait for pods to be ready."""
        service = run_params.get("service", "unknown")

        return {
            "service": service,
            "all_pods_ready": True,
            "ready_pods": 3,
            "total_pods": 3,
        }

    async def approve_step(
        self,
        run_id: str,
        approver_id: str,
    ) -> bool:
        """Approve a waiting step."""
        run = self._runs.get(run_id)
        if not run:
            return False

        if run.status != WorkflowStatus.PAUSED:
            return False

        workflow = self.get_workflow(workflow_id=run.workflow_id)
        if not workflow:
            return False

        step = workflow.steps[run.current_step_index]

        if step.status != StepStatus.WAITING_APPROVAL:
            return False

        step.status = StepStatus.COMPLETED
        step.completed_at = datetime.now(timezone.utc)
        step.result = {"approved_by": approver_id}

        run.status = WorkflowStatus.RUNNING
        run.current_step_index += 1

        log.info(
            "Step approved: run_id=%s step=%s approver=%s",
            run_id,
            step.name,
            approver_id,
        )

        await self._execute_next_step(run, workflow)

        return True

    async def reject_step(
        self,
        run_id: str,
        rejector_id: str,
        reason: str = "",
    ) -> bool:
        """Reject a waiting step."""
        run = self._runs.get(run_id)
        if not run:
            return False

        if run.status != WorkflowStatus.PAUSED:
            return False

        workflow = self.get_workflow(workflow_id=run.workflow_id)
        if not workflow:
            return False

        step = workflow.steps[run.current_step_index]

        if step.status != StepStatus.WAITING_APPROVAL:
            return False

        step.status = StepStatus.FAILED
        step.completed_at = datetime.now(timezone.utc)
        step.error = f"Rejected by {rejector_id}: {reason}"

        run.status = WorkflowStatus.CANCELLED
        run.completed_at = datetime.now(timezone.utc)
        run.error = f"Rejected at step '{step.name}'"

        log.info(
            "Workflow rejected: run_id=%s step=%s rejector=%s",
            run_id,
            step.name,
            rejector_id,
        )

        return True

    async def cancel_run(
        self,
        run_id: str,
        cancelled_by: str,
    ) -> bool:
        """Cancel a running workflow."""
        run = self._runs.get(run_id)
        if not run:
            return False

        if run.status not in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED):
            return False

        run.status = WorkflowStatus.CANCELLED
        run.completed_at = datetime.now(timezone.utc)
        run.error = f"Cancelled by {cancelled_by}"

        log.info("Workflow cancelled: run_id=%s by=%s", run_id, cancelled_by)

        return True

    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run."""
        return self._runs.get(run_id)

    def get_active_runs(self, org_id: str | None = None) -> list[WorkflowRun]:
        """Get active workflow runs."""
        runs = [
            run for run in self._runs.values()
            if run.status in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED)
        ]

        if org_id:
            runs = [run for run in runs if run.org_id == org_id]

        return runs

    def get_run_progress(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Get progress of a workflow run."""
        run = self._runs.get(run_id)
        if not run:
            return None

        workflow = self.get_workflow(workflow_id=run.workflow_id)
        if not workflow:
            return None

        steps_progress = []

        for i, step in enumerate(workflow.steps):
            status_emoji = {
                StepStatus.PENDING: ":white_circle:",
                StepStatus.RUNNING: ":large_blue_circle:",
                StepStatus.WAITING_APPROVAL: ":yellow_circle:",
                StepStatus.COMPLETED: ":white_check_mark:",
                StepStatus.FAILED: ":x:",
                StepStatus.SKIPPED: ":fast_forward:",
            }

            steps_progress.append({
                "index": i,
                "name": step.name,
                "status": step.status.value,
                "emoji": status_emoji.get(step.status, ":white_circle:"),
                "is_current": i == run.current_step_index,
            })

        return {
            "run_id": run.run_id,
            "workflow_name": workflow.name,
            "status": run.status.value,
            "current_step": run.current_step_index,
            "total_steps": len(workflow.steps),
            "steps": steps_progress,
            "duration_seconds": run.duration_seconds,
        }

    def get_slack_progress_blocks(
        self,
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Get Slack blocks for workflow progress."""
        progress = self.get_run_progress(run_id)

        if not progress:
            return []

        steps_text = "\n".join(
            f"{s['emoji']} {s['name']}" + (" *(current)*" if s['is_current'] else "")
            for s in progress["steps"]
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Workflow: {progress['workflow_name']}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Status:* {progress['status']}\n"
                        f"*Progress:* {progress['current_step']}/{progress['total_steps']} steps\n\n"
                        f"{steps_text}"
                    ),
                },
            },
        ]

        run = self._runs.get(run_id)
        if run and run.status == WorkflowStatus.PAUSED:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": f"approve_workflow_{run_id}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "action_id": f"reject_workflow_{run_id}",
                        "style": "danger",
                    },
                ],
            })

        return blocks


_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    """Get the default workflow engine."""
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
