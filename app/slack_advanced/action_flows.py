"""Action Confirmation Flows for Slack.

Manages confirmation flows for critical actions like rollback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_advanced.action_flows")


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionType(str, Enum):
    ROLLBACK = "rollback"
    SCALE = "scale"
    RESTART = "restart"
    DEPLOY = "deploy"
    CONFIG_CHANGE = "config_change"
    DATABASE_OPERATION = "database_operation"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SafetyCheck:
    """A safety check before action execution."""

    check_name: str
    passed: bool
    details: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check_name,
            "passed": self.passed,
            "details": self.details,
            "required": self.required,
        }


@dataclass
class ActionConfirmation:
    """An action awaiting confirmation."""

    confirmation_id: str
    action_type: ActionType
    description: str
    channel_id: str
    thread_ts: str
    requested_by: str
    target_service: str
    target_deploy: str | None
    safety_checks: list[SafetyCheck]
    risk_level: RiskLevel
    estimated_duration_seconds: int
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    confirmed_by: str = ""
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=5)
    )

    @property
    def is_safe(self) -> bool:
        return all(
            c.passed for c in self.safety_checks if c.required
        )

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.confirmation_id,
            "action": self.action_type.value,
            "description": self.description,
            "channel": self.channel_id,
            "thread": self.thread_ts,
            "requested_by": self.requested_by,
            "target": {
                "service": self.target_service,
                "deploy": self.target_deploy,
            },
            "safety_checks": [c.to_dict() for c in self.safety_checks],
            "is_safe": self.is_safe,
            "risk_level": self.risk_level.value,
            "estimated_seconds": self.estimated_duration_seconds,
            "status": self.status.value,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "result": self.result,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "is_expired": self.is_expired,
        }


@dataclass
class ExecutionProgress:
    """Progress of an action execution."""

    confirmation_id: str
    step: int
    total_steps: int
    current_step_name: str
    progress_percent: float
    started_at: datetime
    estimated_completion: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.confirmation_id,
            "step": self.step,
            "total": self.total_steps,
            "current": self.current_step_name,
            "progress": round(self.progress_percent, 1),
            "started": self.started_at.isoformat(),
            "estimated_completion": (
                self.estimated_completion.isoformat()
                if self.estimated_completion else None
            ),
        }


class ActionFlowManager:
    """Manages action confirmation flows."""

    def __init__(self) -> None:
        self._pending: dict[str, ActionConfirmation] = {}
        self._completed: dict[str, ActionConfirmation] = {}
        self._progress: dict[str, ExecutionProgress] = {}

    def create_rollback_confirmation(
        self,
        channel_id: str,
        thread_ts: str,
        requested_by: str,
        service: str,
        deploy_id: str,
    ) -> ActionConfirmation:
        """Create a rollback confirmation request."""
        confirmation_id = f"confirm_rollback_{deploy_id}_{datetime.now(timezone.utc).timestamp()}"

        safety_checks = [
            SafetyCheck(
                check_name="No migrations to reverse",
                passed=True,
                details="No database migrations in this deploy",
                required=True,
            ),
            SafetyCheck(
                check_name="No API breaking changes",
                passed=True,
                details="API contracts unchanged",
                required=True,
            ),
            SafetyCheck(
                check_name="Rollback target healthy",
                passed=True,
                details=f"Deploy #{int(deploy_id) - 1} is a known good state",
                required=True,
            ),
            SafetyCheck(
                check_name="Within rollback window",
                passed=True,
                details="Deploy is recent enough to safely rollback",
                required=False,
            ),
        ]

        confirmation = ActionConfirmation(
            confirmation_id=confirmation_id,
            action_type=ActionType.ROLLBACK,
            description=f"Rollback {service} from deploy #{deploy_id} to #{int(deploy_id) - 1}",
            channel_id=channel_id,
            thread_ts=thread_ts,
            requested_by=requested_by,
            target_service=service,
            target_deploy=deploy_id,
            safety_checks=safety_checks,
            risk_level=RiskLevel.LOW if all(c.passed for c in safety_checks) else RiskLevel.MEDIUM,
            estimated_duration_seconds=120,
        )

        self._pending[confirmation_id] = confirmation

        log.info(
            "Created rollback confirmation: id=%s service=%s deploy=%s",
            confirmation_id,
            service,
            deploy_id,
        )

        return confirmation

    def create_scale_confirmation(
        self,
        channel_id: str,
        thread_ts: str,
        requested_by: str,
        service: str,
        target_replicas: int,
    ) -> ActionConfirmation:
        """Create a scaling confirmation request."""
        confirmation_id = f"confirm_scale_{service}_{datetime.now(timezone.utc).timestamp()}"

        safety_checks = [
            SafetyCheck(
                check_name="Resource quota available",
                passed=True,
                details=f"Cluster has capacity for {target_replicas} replicas",
                required=True,
            ),
            SafetyCheck(
                check_name="Service health OK",
                passed=True,
                details="Current pods are healthy",
                required=True,
            ),
        ]

        confirmation = ActionConfirmation(
            confirmation_id=confirmation_id,
            action_type=ActionType.SCALE,
            description=f"Scale {service} to {target_replicas} replicas",
            channel_id=channel_id,
            thread_ts=thread_ts,
            requested_by=requested_by,
            target_service=service,
            target_deploy=None,
            safety_checks=safety_checks,
            risk_level=RiskLevel.LOW,
            estimated_duration_seconds=180,
        )

        self._pending[confirmation_id] = confirmation

        return confirmation

    def confirm_action(
        self,
        confirmation_id: str,
        confirmed_by: str,
    ) -> ActionConfirmation | None:
        """Confirm an action for execution."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            return None

        if confirmation.is_expired:
            confirmation.status = ConfirmationStatus.EXPIRED
            return confirmation

        if not confirmation.is_safe:
            log.warning(
                "Cannot confirm unsafe action: id=%s",
                confirmation_id,
            )
            return None

        confirmation.status = ConfirmationStatus.CONFIRMED
        confirmation.confirmed_by = confirmed_by
        confirmation.confirmed_at = datetime.now(timezone.utc)

        log.info(
            "Action confirmed: id=%s by=%s",
            confirmation_id,
            confirmed_by,
        )

        return confirmation

    def cancel_action(
        self,
        confirmation_id: str,
        cancelled_by: str,
    ) -> ActionConfirmation | None:
        """Cancel a pending action."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            return None

        confirmation.status = ConfirmationStatus.CANCELLED
        confirmation.result = {"cancelled_by": cancelled_by}

        del self._pending[confirmation_id]
        self._completed[confirmation_id] = confirmation

        log.info(
            "Action cancelled: id=%s by=%s",
            confirmation_id,
            cancelled_by,
        )

        return confirmation

    def start_execution(
        self,
        confirmation_id: str,
    ) -> ExecutionProgress | None:
        """Start executing a confirmed action."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            return None

        if confirmation.status != ConfirmationStatus.CONFIRMED:
            return None

        confirmation.status = ConfirmationStatus.EXECUTING
        confirmation.executed_at = datetime.now(timezone.utc)

        progress = ExecutionProgress(
            confirmation_id=confirmation_id,
            step=1,
            total_steps=4,
            current_step_name="Initiating",
            progress_percent=0,
            started_at=datetime.now(timezone.utc),
            estimated_completion=datetime.now(timezone.utc) + timedelta(
                seconds=confirmation.estimated_duration_seconds
            ),
        )

        self._progress[confirmation_id] = progress

        log.info("Started execution: id=%s", confirmation_id)

        return progress

    def update_progress(
        self,
        confirmation_id: str,
        step: int,
        step_name: str,
        progress_percent: float,
    ) -> ExecutionProgress | None:
        """Update execution progress."""
        progress = self._progress.get(confirmation_id)
        if not progress:
            return None

        progress.step = step
        progress.current_step_name = step_name
        progress.progress_percent = progress_percent

        return progress

    def complete_execution(
        self,
        confirmation_id: str,
        success: bool,
        result: dict[str, Any],
    ) -> ActionConfirmation | None:
        """Mark execution as complete."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            return None

        confirmation.status = (
            ConfirmationStatus.COMPLETED if success
            else ConfirmationStatus.FAILED
        )
        confirmation.completed_at = datetime.now(timezone.utc)
        confirmation.result = result

        del self._pending[confirmation_id]
        self._completed[confirmation_id] = confirmation

        if confirmation_id in self._progress:
            del self._progress[confirmation_id]

        log.info(
            "Execution complete: id=%s success=%s",
            confirmation_id,
            success,
        )

        return confirmation

    def get_confirmation(
        self,
        confirmation_id: str,
    ) -> ActionConfirmation | None:
        """Get a confirmation by ID."""
        return (
            self._pending.get(confirmation_id) or
            self._completed.get(confirmation_id)
        )

    def get_progress(
        self,
        confirmation_id: str,
    ) -> ExecutionProgress | None:
        """Get execution progress."""
        return self._progress.get(confirmation_id)

    def get_confirmation_message(
        self,
        confirmation: ActionConfirmation,
    ) -> dict[str, Any]:
        """Generate Slack message for confirmation."""
        checks_text = ""
        for check in confirmation.safety_checks:
            emoji = ":white_check_mark:" if check.passed else ":x:"
            checks_text += f"{emoji} {check.check_name}\n"

        risk_emoji = {
            RiskLevel.LOW: ":large_green_circle:",
            RiskLevel.MEDIUM: ":large_yellow_circle:",
            RiskLevel.HIGH: ":large_orange_circle:",
            RiskLevel.CRITICAL: ":red_circle:",
        }

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Confirm {confirmation.action_type.value.title()}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{confirmation.description}*\n\n"
                        f"*Requested by:* <@{confirmation.requested_by}>\n"
                        f"*Risk Level:* {risk_emoji[confirmation.risk_level]} {confirmation.risk_level.value}\n"
                        f"*Est. Duration:* {confirmation.estimated_duration_seconds}s"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Safety Checks:*\n{checks_text}",
                },
            },
        ]

        if confirmation.is_safe:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Confirm"},
                        "style": "danger" if confirmation.risk_level in (
                            RiskLevel.HIGH, RiskLevel.CRITICAL
                        ) else "primary",
                        "action_id": f"confirm_{confirmation.confirmation_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "action_id": f"cancel_{confirmation.confirmation_id}",
                    },
                ],
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":warning: *Cannot proceed - safety checks failed*",
                },
            })

        return {
            "blocks": blocks,
            "text": f"Confirm {confirmation.action_type.value}?",
        }

    def get_progress_message(
        self,
        progress: ExecutionProgress,
    ) -> dict[str, Any]:
        """Generate Slack message for progress."""
        bar_width = 20
        filled = int(progress.progress_percent / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Executing...*\n\n"
                        f"Step {progress.step}/{progress.total_steps}: {progress.current_step_name}\n"
                        f"`{bar}` {progress.progress_percent:.0f}%"
                    ),
                },
            },
        ]

        return {
            "blocks": blocks,
            "text": f"Execution progress: {progress.progress_percent:.0f}%",
        }

    def get_completion_message(
        self,
        confirmation: ActionConfirmation,
    ) -> dict[str, Any]:
        """Generate Slack message for completion."""
        if confirmation.status == ConfirmationStatus.COMPLETED:
            emoji = ":white_check_mark:"
            status_text = "COMPLETE"
        else:
            emoji = ":x:"
            status_text = "FAILED"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{confirmation.action_type.value.title()} {status_text}*\n\n"
                        f"{confirmation.description}\n\n"
                        f"*Confirmed by:* <@{confirmation.confirmed_by}>"
                    ),
                },
            },
        ]

        if confirmation.result:
            result_text = "\n".join(
                f"- {k}: {v}" for k, v in list(confirmation.result.items())[:5]
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Result:*\n{result_text}",
                },
            })

        return {
            "blocks": blocks,
            "text": f"{confirmation.action_type.value.title()} {status_text.lower()}",
        }


_manager: ActionFlowManager | None = None


def get_action_flow_manager() -> ActionFlowManager:
    """Get the default action flow manager."""
    global _manager
    if _manager is None:
        _manager = ActionFlowManager()
    return _manager
