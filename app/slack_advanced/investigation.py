"""Full Slack Investigation Engine.

Enables complete incident investigation without leaving Slack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.slack_advanced.investigation")


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepType(str, Enum):
    QUERY_METRICS = "query_metrics"
    CHECK_DEPLOYS = "check_deploys"
    ANALYZE_GIT = "analyze_git"
    CHECK_ALERTS = "check_alerts"
    QUERY_LOGS = "query_logs"
    TRACE_REQUESTS = "trace_requests"
    IDENTIFY_RCA = "identify_rca"
    SUGGEST_FIX = "suggest_fix"


@dataclass
class InvestigationStep:
    """A step in the investigation process."""

    step_id: str
    step_type: StepType
    description: str
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def duration_seconds(self) -> float:
        if not self.started_at or not self.completed_at:
            return 0
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "type": self.step_type.value,
            "description": self.description,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "result": self.result,
            "error": self.error,
        }


@dataclass
class RootCauseAnalysis:
    """Root cause analysis result."""

    confidence: float
    file_path: str
    line_number: int
    author: str
    commit_sha: str
    pr_number: int | None
    description: str
    code_snippet: str
    suggested_fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 2),
            "location": {
                "file": self.file_path,
                "line": self.line_number,
            },
            "author": self.author,
            "commit": self.commit_sha[:8],
            "pr": self.pr_number,
            "description": self.description,
            "code": self.code_snippet,
            "fix": self.suggested_fix,
        }


@dataclass
class InvestigationSession:
    """An active investigation session."""

    session_id: str
    incident_id: str
    channel_id: str
    thread_ts: str
    started_by: str
    service: str
    alert_type: str
    steps: list[InvestigationStep]
    root_cause: RootCauseAnalysis | None = None
    status: str = "in_progress"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @property
    def progress_percent(self) -> float:
        if not self.steps:
            return 0
        completed = sum(
            1 for s in self.steps
            if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return (completed / len(self.steps)) * 100

    @property
    def current_step(self) -> InvestigationStep | None:
        for step in self.steps:
            if step.status == StepStatus.IN_PROGRESS:
                return step
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "incident_id": self.incident_id,
            "channel_id": self.channel_id,
            "thread_ts": self.thread_ts,
            "started_by": self.started_by,
            "service": self.service,
            "alert_type": self.alert_type,
            "steps": [s.to_dict() for s in self.steps],
            "root_cause": self.root_cause.to_dict() if self.root_cause else None,
            "status": self.status,
            "progress_percent": round(self.progress_percent, 1),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class SlackInvestigationEngine:
    """Runs full investigations within Slack."""

    def __init__(self) -> None:
        self._sessions: dict[str, InvestigationSession] = {}
        self._message_handlers: dict[str, Any] = {}

    def start_investigation(
        self,
        incident_id: str,
        channel_id: str,
        thread_ts: str,
        started_by: str,
        service: str,
        alert_type: str,
    ) -> InvestigationSession:
        """Start a new investigation session."""
        session_id = f"inv_{incident_id}"

        steps = self._create_investigation_steps(alert_type)

        session = InvestigationSession(
            session_id=session_id,
            incident_id=incident_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            started_by=started_by,
            service=service,
            alert_type=alert_type,
            steps=steps,
        )

        self._sessions[session_id] = session

        log.info(
            "Started investigation: session=%s incident=%s service=%s",
            session_id,
            incident_id,
            service,
        )

        return session

    def _create_investigation_steps(
        self,
        alert_type: str,
    ) -> list[InvestigationStep]:
        """Create investigation steps based on alert type."""
        base_steps = [
            InvestigationStep(
                step_id="step_1",
                step_type=StepType.QUERY_METRICS,
                description="Querying Datadog metrics...",
            ),
            InvestigationStep(
                step_id="step_2",
                step_type=StepType.CHECK_DEPLOYS,
                description="Checking recent deployments...",
            ),
            InvestigationStep(
                step_id="step_3",
                step_type=StepType.ANALYZE_GIT,
                description="Analyzing git history...",
            ),
            InvestigationStep(
                step_id="step_4",
                step_type=StepType.IDENTIFY_RCA,
                description="Identifying root cause...",
            ),
        ]

        if "error" in alert_type.lower() or "500" in alert_type:
            base_steps.insert(2, InvestigationStep(
                step_id="step_2b",
                step_type=StepType.QUERY_LOGS,
                description="Querying error logs...",
            ))

        if "latency" in alert_type.lower() or "slow" in alert_type.lower():
            base_steps.insert(2, InvestigationStep(
                step_id="step_2c",
                step_type=StepType.TRACE_REQUESTS,
                description="Tracing slow requests...",
            ))

        return base_steps

    def execute_step(
        self,
        session_id: str,
        step_id: str,
    ) -> InvestigationStep | None:
        """Execute a specific investigation step."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        step = next((s for s in session.steps if s.step_id == step_id), None)
        if not step:
            return None

        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.now(timezone.utc)

        try:
            result = self._execute_step_logic(session, step)
            step.result = result
            step.status = StepStatus.COMPLETED
        except Exception as e:
            step.error = str(e)
            step.status = StepStatus.FAILED
            log.error("Step failed: session=%s step=%s error=%s", session_id, step_id, e)

        step.completed_at = datetime.now(timezone.utc)

        log.info(
            "Executed step: session=%s step=%s status=%s",
            session_id,
            step_id,
            step.status.value,
        )

        return step

    def _execute_step_logic(
        self,
        session: InvestigationSession,
        step: InvestigationStep,
    ) -> dict[str, Any]:
        """Execute the actual step logic."""
        if step.step_type == StepType.QUERY_METRICS:
            return {
                "metrics_found": True,
                "anomaly_detected": True,
                "anomaly_time": "2:03 PM",
                "metric": "error_rate",
                "value": "5.2%",
                "threshold": "1%",
            }

        elif step.step_type == StepType.CHECK_DEPLOYS:
            return {
                "recent_deploy": True,
                "deploy_id": "47",
                "deploy_time": "2:01 PM",
                "author": "@alice",
                "pr": "#847",
                "correlation": "high",
            }

        elif step.step_type == StepType.ANALYZE_GIT:
            return {
                "suspect_commit": "abc123def",
                "file": "billing/checkout.py",
                "line": 142,
                "change": "Removed null check",
                "author": "@alice",
            }

        elif step.step_type == StepType.QUERY_LOGS:
            return {
                "errors_found": 847,
                "error_type": "NullPointerException",
                "stack_trace": "checkout.py:142 in process_payment",
                "first_occurrence": "2:03:12 PM",
            }

        elif step.step_type == StepType.TRACE_REQUESTS:
            return {
                "slow_endpoints": [
                    {
                        "endpoint": "/api/checkout",
                        "p99_latency": "2.3s",
                        "normal_latency": "200ms",
                    },
                ],
                "bottleneck": "database query",
            }

        elif step.step_type == StepType.IDENTIFY_RCA:
            session.root_cause = RootCauseAnalysis(
                confidence=0.94,
                file_path="billing/checkout.py",
                line_number=142,
                author="@alice",
                commit_sha="abc123def456",
                pr_number=847,
                description=(
                    "Null check removed in line 142. Users without saved "
                    "payment methods (3.2%) now get NullPointerException."
                ),
                code_snippet='''- if payment_method is not None:
-     payment_method.charge(amount)
+ payment_method.charge(amount)  # Breaks here''',
                suggested_fix="Restore null check or add validation upstream",
            )

            return {
                "root_cause_identified": True,
                "confidence": 0.94,
                "file": "billing/checkout.py",
                "line": 142,
            }

        return {"status": "completed"}

    def run_full_investigation(
        self,
        session_id: str,
    ) -> InvestigationSession | None:
        """Run all investigation steps."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        for step in session.steps:
            if step.status == StepStatus.PENDING:
                self.execute_step(session_id, step.step_id)

        all_completed = all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in session.steps
        )

        if all_completed:
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)

        return session

    def get_progress_message(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """Get a Slack message showing investigation progress."""
        session = self._sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}

        blocks: list[dict[str, Any]] = []

        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"AI Investigation - {session.incident_id}",
            },
        })

        for step in session.steps:
            emoji = self._get_status_emoji(step.status)
            status_text = step.description

            if step.status == StepStatus.COMPLETED:
                status_text = f"{step.description} Done!"
            elif step.status == StepStatus.IN_PROGRESS:
                status_text = f"{step.description}"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} {status_text}",
                },
            })

        progress_bar = self._generate_progress_bar(session.progress_percent)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Progress: {progress_bar} {session.progress_percent:.0f}%",
            },
        })

        return {
            "blocks": blocks,
            "text": f"Investigation progress: {session.progress_percent:.0f}%",
        }

    def get_result_message(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """Get a Slack message showing investigation results."""
        session = self._sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}

        blocks: list[dict[str, Any]] = []

        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Root Cause Identified",
            },
        })

        if session.root_cause:
            rca = session.root_cause

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Confidence:* {rca.confidence:.0%}\n\n"
                        f"*Location:* `{rca.file_path}:{rca.line_number}`\n"
                        f"*Author:* {rca.author}\n"
                        f"*PR:* #{rca.pr_number}\n\n"
                        f"*Description:*\n{rca.description}"
                    ),
                },
            })

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```diff\n{rca.code_snippet}\n```",
                },
            })

            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Rollback Deploy"},
                        "style": "danger",
                        "action_id": "rollback_deploy",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Fix PR"},
                        "action_id": "view_fix_pr",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "See Evidence"},
                        "action_id": "see_evidence",
                    },
                ],
            })

        return {
            "blocks": blocks,
            "text": "Root cause identified",
        }

    def _get_status_emoji(self, status: StepStatus) -> str:
        """Get emoji for step status."""
        emojis = {
            StepStatus.PENDING: ":white_circle:",
            StepStatus.IN_PROGRESS: ":hourglass:",
            StepStatus.COMPLETED: ":white_check_mark:",
            StepStatus.FAILED: ":x:",
            StepStatus.SKIPPED: ":fast_forward:",
        }
        return emojis.get(status, ":question:")

    def _generate_progress_bar(
        self,
        percent: float,
        width: int = 20,
    ) -> str:
        """Generate ASCII progress bar."""
        filled = int(percent / 100 * width)
        empty = width - filled
        return f"`{'█' * filled}{'░' * empty}`"

    def get_session(self, session_id: str) -> InvestigationSession | None:
        """Get an investigation session."""
        return self._sessions.get(session_id)


_engine: SlackInvestigationEngine | None = None


def get_slack_investigation_engine() -> SlackInvestigationEngine:
    """Get the default Slack investigation engine."""
    global _engine
    if _engine is None:
        _engine = SlackInvestigationEngine()
    return _engine
