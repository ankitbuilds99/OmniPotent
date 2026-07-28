"""Auto-generated rollback plans for runbooks.

Generates undo steps for executed runbook actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.runbook.models import (
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepStatus,
    StepType,
)

log = logging.getLogger("devcopilot.runbook.rollback")


@dataclass
class RollbackStep:
    """A rollback step to undo an executed action."""

    step_id: str
    name: str
    description: str
    command: str
    original_step_id: str
    original_step_name: str
    reversible: bool
    risk_level: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "command": self.command,
            "original_step_id": self.original_step_id,
            "original_step_name": self.original_step_name,
            "reversible": self.reversible,
            "risk_level": self.risk_level,
            "notes": self.notes,
        }


@dataclass
class RollbackPlan:
    """A complete rollback plan for a runbook execution."""

    plan_id: str
    execution_id: str
    runbook_id: str
    steps: list[RollbackStep]
    fully_reversible: bool
    manual_steps_required: int
    estimated_duration_minutes: int
    warnings: list[str]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "execution_id": self.execution_id,
            "runbook_id": self.runbook_id,
            "steps": [s.to_dict() for s in self.steps],
            "fully_reversible": self.fully_reversible,
            "manual_steps_required": self.manual_steps_required,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "warnings": self.warnings,
            "generated_at": self.generated_at.isoformat(),
        }


class RollbackGenerator:
    """Generates rollback plans for runbook executions."""

    def __init__(self) -> None:
        self._step_counter = 0
        self._rollback_patterns = self._setup_rollback_patterns()

    def _setup_rollback_patterns(self) -> dict[str, dict[str, Any]]:
        """Set up patterns for generating rollback commands."""
        return {
            "kubectl_scale": {
                "pattern": r"kubectl scale deployment/(\S+) --replicas=(\d+)",
                "rollback_template": "kubectl scale deployment/{deployment} --replicas={original_replicas}",
                "reversible": True,
                "risk": "low",
            },
            "kubectl_patch": {
                "pattern": r"kubectl patch (\S+) (\S+)",
                "rollback_template": "kubectl rollout undo {resource}",
                "reversible": True,
                "risk": "medium",
            },
            "kubectl_rollout_restart": {
                "pattern": r"kubectl rollout restart deployment/(\S+)",
                "rollback_template": "kubectl rollout undo deployment/{deployment}",
                "reversible": True,
                "risk": "low",
            },
            "psql_alter": {
                "pattern": r"psql.*ALTER SYSTEM SET (\S+) = (\S+)",
                "rollback_template": "psql -c \"ALTER SYSTEM RESET {setting};\"",
                "reversible": True,
                "risk": "medium",
            },
            "pg_ctl_promote": {
                "pattern": r"pg_ctl promote",
                "rollback_template": None,
                "reversible": False,
                "risk": "critical",
                "note": "Database promotion cannot be automatically reversed",
            },
            "redis_del": {
                "pattern": r"redis-cli DEL",
                "rollback_template": None,
                "reversible": False,
                "risk": "high",
                "note": "Deleted keys cannot be automatically restored",
            },
            "configmap_patch": {
                "pattern": r"kubectl patch configmap (\S+)",
                "rollback_template": "kubectl rollout undo configmap/{configmap}",
                "reversible": True,
                "risk": "medium",
            },
        }

    def generate_rollback_plan(
        self,
        runbook: Runbook,
        execution: RunbookExecution | None = None,
    ) -> RollbackPlan:
        """Generate a rollback plan for a runbook."""
        import secrets

        plan_id = f"rollback_{secrets.token_hex(8)}"
        rollback_steps: list[RollbackStep] = []
        warnings: list[str] = []
        manual_required = 0

        steps_to_rollback = list(runbook.steps)
        if execution:
            completed_step_ids = {
                r.step_id for r in execution.step_results
                if r.status == StepStatus.COMPLETED
            }
            steps_to_rollback = [
                s for s in runbook.steps
                if s.step_id in completed_step_ids
            ]

        for step in reversed(steps_to_rollback):
            rollback_step = self._generate_rollback_step(step)
            if rollback_step:
                rollback_steps.append(rollback_step)

                if not rollback_step.reversible:
                    manual_required += 1
                    if rollback_step.notes:
                        warnings.append(f"Step '{step.name}': {rollback_step.notes}")

        fully_reversible = all(s.reversible for s in rollback_steps)

        if not fully_reversible:
            warnings.insert(0, "Some steps cannot be automatically rolled back")

        estimated_duration = len(rollback_steps) * 2

        return RollbackPlan(
            plan_id=plan_id,
            execution_id=execution.execution_id if execution else "",
            runbook_id=runbook.runbook_id,
            steps=rollback_steps,
            fully_reversible=fully_reversible,
            manual_steps_required=manual_required,
            estimated_duration_minutes=estimated_duration,
            warnings=warnings,
        )

    def _generate_rollback_step(self, step: RunbookStep) -> RollbackStep | None:
        """Generate a rollback step for a runbook step."""
        self._step_counter += 1
        step_id = f"rollback_step_{self._step_counter}"

        if step.rollback_command:
            return RollbackStep(
                step_id=step_id,
                name=f"Undo: {step.name}",
                description=f"Rollback for step: {step.description}",
                command=step.rollback_command,
                original_step_id=step.step_id,
                original_step_name=step.name,
                reversible=True,
                risk_level="medium",
            )

        if step.step_type in [StepType.QUERY, StepType.NOTIFICATION, StepType.WAIT]:
            return None

        if step.step_type == StepType.MANUAL:
            return RollbackStep(
                step_id=step_id,
                name=f"Manual undo: {step.name}",
                description="This step requires manual rollback",
                command="# Manual intervention required",
                original_step_id=step.step_id,
                original_step_name=step.name,
                reversible=False,
                risk_level="high",
                notes="Manual step - coordinate with team for rollback",
            )

        import re
        for pattern_name, pattern_info in self._rollback_patterns.items():
            match = re.search(pattern_info["pattern"], step.command)
            if match:
                if pattern_info.get("rollback_template"):
                    rollback_cmd = self._build_rollback_command(
                        pattern_info["rollback_template"],
                        match,
                        step,
                    )
                    return RollbackStep(
                        step_id=step_id,
                        name=f"Undo: {step.name}",
                        description=f"Auto-generated rollback for: {step.description}",
                        command=rollback_cmd,
                        original_step_id=step.step_id,
                        original_step_name=step.name,
                        reversible=pattern_info["reversible"],
                        risk_level=pattern_info["risk"],
                    )
                else:
                    return RollbackStep(
                        step_id=step_id,
                        name=f"Cannot undo: {step.name}",
                        description=pattern_info.get("note", "This action cannot be automatically reversed"),
                        command="# Manual intervention required",
                        original_step_id=step.step_id,
                        original_step_name=step.name,
                        reversible=False,
                        risk_level=pattern_info["risk"],
                        notes=pattern_info.get("note", ""),
                    )

        return RollbackStep(
            step_id=step_id,
            name=f"Verify: {step.name}",
            description="Verify state after this step",
            command=f"# Review impact of: {step.command[:100]}",
            original_step_id=step.step_id,
            original_step_name=step.name,
            reversible=False,
            risk_level="unknown",
            notes="No automatic rollback available - manual review required",
        )

    def _build_rollback_command(
        self,
        template: str,
        match: Any,
        step: RunbookStep,
    ) -> str:
        """Build a rollback command from a template and match."""
        command = template

        groups = match.groups()
        if groups:
            if "deployment" in template and len(groups) >= 1:
                command = command.replace("{deployment}", groups[0])
            if "original_replicas" in template:
                command = command.replace("{original_replicas}", "3")
            if "resource" in template and len(groups) >= 2:
                command = command.replace("{resource}", f"{groups[0]} {groups[1]}")
            if "setting" in template and len(groups) >= 1:
                command = command.replace("{setting}", groups[0])
            if "configmap" in template and len(groups) >= 1:
                command = command.replace("{configmap}", groups[0])

        return command

    def estimate_rollback_risk(self, plan: RollbackPlan) -> dict[str, Any]:
        """Estimate the risk of executing a rollback plan."""
        total_steps = len(plan.steps)
        if total_steps == 0:
            return {
                "overall_risk": "none",
                "risk_score": 0,
                "breakdown": {},
            }

        risk_scores = {"low": 1, "medium": 2, "high": 3, "critical": 4, "unknown": 2}

        risk_counts: dict[str, int] = {}
        total_score = 0

        for step in plan.steps:
            risk = step.risk_level
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            total_score += risk_scores.get(risk, 2)

        avg_score = total_score / total_steps

        if avg_score <= 1.5:
            overall_risk = "low"
        elif avg_score <= 2.5:
            overall_risk = "medium"
        elif avg_score <= 3.5:
            overall_risk = "high"
        else:
            overall_risk = "critical"

        return {
            "overall_risk": overall_risk,
            "risk_score": round(avg_score, 2),
            "breakdown": risk_counts,
            "non_reversible_steps": plan.manual_steps_required,
            "recommendation": self._get_risk_recommendation(overall_risk, plan),
        }

    def _get_risk_recommendation(
        self,
        risk_level: str,
        plan: RollbackPlan,
    ) -> str:
        """Get recommendation based on risk level."""
        if risk_level == "low":
            return "Rollback can be executed with minimal supervision"
        elif risk_level == "medium":
            return "Recommend having DBA or SRE review before executing"
        elif risk_level == "high":
            return "Requires approval from team lead before executing"
        else:
            return "Critical risk - requires incident commander approval and full team standby"


_generator: RollbackGenerator | None = None


def get_rollback_generator() -> RollbackGenerator:
    """Get the default rollback generator."""
    global _generator
    if _generator is None:
        _generator = RollbackGenerator()
    return _generator
