"""Human-in-the-Loop Analytics.

Tracks approval patterns, latency, and bottlenecks in human workflows.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.analytics_advanced.human_loop")


class ActionType(str, Enum):
    ROLLBACK = "rollback"
    SCALE = "scale"
    RESTART = "restart"
    FIX_PR = "fix_pr"
    CONFIG_CHANGE = "config_change"
    DELETE_RESOURCE = "delete_resource"
    ESCALATE = "escalate"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    TIMEOUT = "timeout"


@dataclass
class ApprovalRequest:
    """An approval request record."""

    request_id: str
    incident_id: str
    action_type: ActionType
    requested_at: datetime
    responded_at: datetime | None = None
    approver_id: str | None = None
    decision: ApprovalDecision | None = None
    modification_details: str | None = None
    timeout_threshold_minutes: int = 10

    @property
    def latency_seconds(self) -> float | None:
        if self.responded_at and self.requested_at:
            return (self.responded_at - self.requested_at).total_seconds()
        return None

    @property
    def is_timeout(self) -> bool:
        if self.responded_at:
            return self.decision == ApprovalDecision.TIMEOUT

        elapsed = (datetime.now(timezone.utc) - self.requested_at).total_seconds()
        return elapsed > self.timeout_threshold_minutes * 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "incident_id": self.incident_id,
            "action_type": self.action_type.value,
            "decision": self.decision.value if self.decision else None,
            "latency_seconds": self.latency_seconds,
            "is_timeout": self.is_timeout,
        }


@dataclass
class ApprovalPattern:
    """Approval pattern statistics."""

    avg_approvals_per_incident: float
    approval_rate: float
    rejection_rate: float
    timeout_rate: float
    modification_rate: float
    fastest_approver: str | None
    slowest_action_type: ActionType | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_approvals_per_incident": round(self.avg_approvals_per_incident, 1),
            "rates": {
                "approval": round(self.approval_rate * 100, 1),
                "rejection": round(self.rejection_rate * 100, 1),
                "timeout": round(self.timeout_rate * 100, 1),
                "modification": round(self.modification_rate * 100, 1),
            },
            "fastest_approver": self.fastest_approver,
            "slowest_action_type": (
                self.slowest_action_type.value if self.slowest_action_type else None
            ),
        }


@dataclass
class ApprovalMetrics:
    """Aggregated approval metrics."""

    period_start: datetime
    period_end: datetime
    total_requests: int
    avg_latency_seconds: float
    p50_latency_seconds: float
    p95_latency_seconds: float
    latency_by_action_type: dict[str, float]
    patterns: ApprovalPattern
    bottlenecks: list[str]
    by_approver: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "summary": {
                "total_requests": self.total_requests,
                "avg_latency_seconds": round(self.avg_latency_seconds, 1),
                "p50_latency_seconds": round(self.p50_latency_seconds, 1),
                "p95_latency_seconds": round(self.p95_latency_seconds, 1),
            },
            "latency_by_action": {
                k: round(v, 1) for k, v in self.latency_by_action_type.items()
            },
            "patterns": self.patterns.to_dict(),
            "bottlenecks": self.bottlenecks,
            "by_approver": self.by_approver,
        }


class HumanLoopAnalytics:
    """Analyzes human-in-the-loop interactions."""

    def __init__(self) -> None:
        self._requests: list[ApprovalRequest] = []

    def record_request(self, request: ApprovalRequest) -> None:
        """Record an approval request."""
        self._requests.append(request)

        if len(self._requests) > 50000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._requests = [r for r in self._requests if r.requested_at >= cutoff]

    def record_decision(
        self,
        request_id: str,
        approver_id: str,
        decision: ApprovalDecision,
        modification_details: str | None = None,
    ) -> None:
        """Record a decision on an approval request."""
        for request in self._requests:
            if request.request_id == request_id:
                request.responded_at = datetime.now(timezone.utc)
                request.approver_id = approver_id
                request.decision = decision
                request.modification_details = modification_details
                break

    def calculate_metrics(
        self,
        period_days: int = 30,
    ) -> ApprovalMetrics:
        """Calculate approval metrics."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        requests = [
            r for r in self._requests if r.requested_at >= period_start
        ]

        if not requests:
            return self._empty_metrics(period_start, now)

        latencies = [
            r.latency_seconds for r in requests
            if r.latency_seconds is not None
        ]

        avg_latency = statistics.mean(latencies) if latencies else 0.0
        p50_latency = statistics.median(latencies) if latencies else 0.0
        p95_latency = self._percentile(latencies, 95) if latencies else 0.0

        latency_by_action = self._calculate_latency_by_action(requests)

        patterns = self._calculate_patterns(requests)

        bottlenecks = self._identify_bottlenecks(requests, latency_by_action)

        by_approver = self._calculate_by_approver(requests)

        return ApprovalMetrics(
            period_start=period_start,
            period_end=now,
            total_requests=len(requests),
            avg_latency_seconds=avg_latency,
            p50_latency_seconds=p50_latency,
            p95_latency_seconds=p95_latency,
            latency_by_action_type=latency_by_action,
            patterns=patterns,
            bottlenecks=bottlenecks,
            by_approver=by_approver,
        )

    def _calculate_latency_by_action(
        self,
        requests: list[ApprovalRequest],
    ) -> dict[str, float]:
        """Calculate average latency by action type."""
        by_action: dict[str, list[float]] = {}

        for request in requests:
            action = request.action_type.value
            if action not in by_action:
                by_action[action] = []

            if request.latency_seconds is not None:
                by_action[action].append(request.latency_seconds)

        return {
            action: statistics.mean(latencies) if latencies else 0
            for action, latencies in by_action.items()
        }

    def _calculate_patterns(
        self,
        requests: list[ApprovalRequest],
    ) -> ApprovalPattern:
        """Calculate approval patterns."""
        by_incident: dict[str, int] = {}
        for r in requests:
            by_incident[r.incident_id] = by_incident.get(r.incident_id, 0) + 1

        avg_per_incident = (
            statistics.mean(by_incident.values()) if by_incident else 0.0
        )

        decided = [r for r in requests if r.decision is not None]

        if not decided:
            return ApprovalPattern(
                avg_approvals_per_incident=avg_per_incident,
                approval_rate=0.0,
                rejection_rate=0.0,
                timeout_rate=0.0,
                modification_rate=0.0,
                fastest_approver=None,
                slowest_action_type=None,
            )

        approvals = sum(1 for r in decided if r.decision == ApprovalDecision.APPROVE)
        rejections = sum(1 for r in decided if r.decision == ApprovalDecision.REJECT)
        timeouts = sum(1 for r in decided if r.decision == ApprovalDecision.TIMEOUT)
        modifications = sum(1 for r in decided if r.decision == ApprovalDecision.MODIFY)

        fastest = self._find_fastest_approver(requests)

        slowest_action = None
        latency_by_action = self._calculate_latency_by_action(requests)
        if latency_by_action:
            slowest_action_str = max(latency_by_action, key=latency_by_action.get)  # type: ignore[arg-type]
            slowest_action = ActionType(slowest_action_str)

        return ApprovalPattern(
            avg_approvals_per_incident=avg_per_incident,
            approval_rate=approvals / len(decided),
            rejection_rate=rejections / len(decided),
            timeout_rate=timeouts / len(decided),
            modification_rate=modifications / len(decided),
            fastest_approver=fastest,
            slowest_action_type=slowest_action,
        )

    def _find_fastest_approver(
        self,
        requests: list[ApprovalRequest],
    ) -> str | None:
        """Find the fastest average approver."""
        by_approver: dict[str, list[float]] = {}

        for request in requests:
            if request.approver_id and request.latency_seconds:
                if request.approver_id not in by_approver:
                    by_approver[request.approver_id] = []
                by_approver[request.approver_id].append(request.latency_seconds)

        if not by_approver:
            return None

        avg_by_approver = {
            approver: statistics.mean(latencies)
            for approver, latencies in by_approver.items()
        }

        return min(avg_by_approver, key=avg_by_approver.get)  # type: ignore[arg-type]

    def _identify_bottlenecks(
        self,
        requests: list[ApprovalRequest],
        latency_by_action: dict[str, float],
    ) -> list[str]:
        """Identify bottlenecks in approval process."""
        bottlenecks = []

        if latency_by_action:
            avg_overall = statistics.mean(latency_by_action.values())

            for action, latency in latency_by_action.items():
                if latency > avg_overall * 1.5:
                    minutes = latency / 60
                    bottlenecks.append(
                        f"{action} approvals averaging {minutes:.1f}m "
                        f"(50% above average). Consider auto-approval for low-risk cases."
                    )

        timeout_rate = sum(1 for r in requests if r.is_timeout) / len(requests) if requests else 0
        if timeout_rate > 0.05:
            bottlenecks.append(
                f"Timeout rate is {timeout_rate * 100:.1f}%. "
                "Consider reducing timeout threshold or adding backup approvers."
            )

        after_hours = sum(
            1 for r in requests
            if r.requested_at.hour < 9 or r.requested_at.hour > 17
        )
        after_hours_rate = after_hours / len(requests) if requests else 0

        if after_hours_rate > 0.3:
            bottlenecks.append(
                f"{after_hours_rate * 100:.0f}% of approvals requested after hours. "
                "Consider global on-call rotation or delayed action scheduling."
            )

        return bottlenecks

    def _calculate_by_approver(
        self,
        requests: list[ApprovalRequest],
    ) -> dict[str, dict[str, Any]]:
        """Calculate metrics by approver."""
        by_approver: dict[str, list[ApprovalRequest]] = {}

        for request in requests:
            if request.approver_id:
                if request.approver_id not in by_approver:
                    by_approver[request.approver_id] = []
                by_approver[request.approver_id].append(request)

        result = {}

        for approver, approver_requests in by_approver.items():
            latencies = [
                r.latency_seconds for r in approver_requests
                if r.latency_seconds is not None
            ]

            result[approver] = {
                "total_requests": len(approver_requests),
                "avg_latency_seconds": (
                    round(statistics.mean(latencies), 1) if latencies else 0
                ),
                "approval_rate": round(
                    sum(1 for r in approver_requests if r.decision == ApprovalDecision.APPROVE)
                    / len(approver_requests) * 100,
                    1,
                ),
            }

        return result

    def _percentile(self, values: list[float], percentile: int) -> float:
        """Calculate percentile."""
        if not values:
            return 0.0

        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile / 100)
        index = min(index, len(sorted_values) - 1)

        return sorted_values[index]

    def _empty_metrics(
        self,
        start: datetime,
        end: datetime,
    ) -> ApprovalMetrics:
        """Return empty metrics."""
        return ApprovalMetrics(
            period_start=start,
            period_end=end,
            total_requests=0,
            avg_latency_seconds=0.0,
            p50_latency_seconds=0.0,
            p95_latency_seconds=0.0,
            latency_by_action_type={},
            patterns=ApprovalPattern(
                avg_approvals_per_incident=0.0,
                approval_rate=0.0,
                rejection_rate=0.0,
                timeout_rate=0.0,
                modification_rate=0.0,
                fastest_approver=None,
                slowest_action_type=None,
            ),
            bottlenecks=[],
            by_approver={},
        )

    def get_approval_flow_visualization(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """Get approval flow for an incident."""
        incident_requests = [
            r for r in self._requests if r.incident_id == incident_id
        ]

        return [
            {
                "step": i + 1,
                "action_type": r.action_type.value,
                "requested_at": r.requested_at.isoformat(),
                "decision": r.decision.value if r.decision else "pending",
                "latency_seconds": r.latency_seconds,
                "approver": r.approver_id,
            }
            for i, r in enumerate(sorted(incident_requests, key=lambda x: x.requested_at))
        ]


_analytics: HumanLoopAnalytics | None = None


def get_human_loop_analytics() -> HumanLoopAnalytics:
    """Get the default human loop analytics."""
    global _analytics
    if _analytics is None:
        _analytics = HumanLoopAnalytics()
    return _analytics
