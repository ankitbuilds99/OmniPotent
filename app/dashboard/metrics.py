"""Dashboard metrics and ROI calculation.

Provides:
- Investigation success metrics
- MTTR (Mean Time To Resolution) tracking
- Cost savings calculation
- Usage analytics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.dashboard.metrics")


class InvestigationOutcome(str, Enum):
    """Possible investigation outcomes."""

    ROOT_CAUSE_FOUND = "root_cause_found"
    PARTIAL_DIAGNOSIS = "partial_diagnosis"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass
class InvestigationRecord:
    """Record of a single investigation."""

    id: str
    started_at: datetime
    completed_at: datetime | None = None
    outcome: InvestigationOutcome = InvestigationOutcome.PARTIAL_DIAGNOSIS
    duration_seconds: float = 0.0
    tokens_used: int = 0
    severity: str = "unknown"
    service: str = ""
    hypotheses_generated: int = 0
    tools_used: list[str] = field(default_factory=list)
    user_feedback: str | None = None
    time_saved_minutes: float = 0.0


@dataclass
class MTTRStats:
    """Mean Time To Resolution statistics."""

    current_mttr_minutes: float = 0.0
    previous_mttr_minutes: float = 0.0
    improvement_percent: float = 0.0
    fastest_resolution_minutes: float = 0.0
    slowest_resolution_minutes: float = 0.0
    p50_minutes: float = 0.0
    p90_minutes: float = 0.0
    p99_minutes: float = 0.0
    sample_size: int = 0

    @property
    def improved(self) -> bool:
        """Whether MTTR has improved."""
        return self.improvement_percent > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_mttr_minutes": round(self.current_mttr_minutes, 1),
            "previous_mttr_minutes": round(self.previous_mttr_minutes, 1),
            "improvement_percent": round(self.improvement_percent, 1),
            "fastest_resolution_minutes": round(self.fastest_resolution_minutes, 1),
            "slowest_resolution_minutes": round(self.slowest_resolution_minutes, 1),
            "p50_minutes": round(self.p50_minutes, 1),
            "p90_minutes": round(self.p90_minutes, 1),
            "p99_minutes": round(self.p99_minutes, 1),
            "sample_size": self.sample_size,
            "improved": self.improved,
        }


@dataclass
class ROIMetrics:
    """Return on Investment metrics."""

    time_saved_hours: float = 0.0
    investigations_completed: int = 0
    avg_time_saved_per_investigation_minutes: float = 0.0
    estimated_cost_savings_usd: float = 0.0
    engineer_hours_recovered: float = 0.0
    downtime_prevented_minutes: float = 0.0
    incidents_auto_resolved: int = 0
    escalations_prevented: int = 0

    hourly_engineer_cost_usd: float = 150.0
    downtime_cost_per_minute_usd: float = 500.0

    def calculate_savings(self) -> None:
        """Calculate cost savings based on time saved."""
        self.estimated_cost_savings_usd = (
            self.time_saved_hours * self.hourly_engineer_cost_usd
            + self.downtime_prevented_minutes * self.downtime_cost_per_minute_usd
        )

        if self.investigations_completed > 0:
            self.avg_time_saved_per_investigation_minutes = (
                self.time_saved_hours * 60 / self.investigations_completed
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_saved_hours": round(self.time_saved_hours, 1),
            "investigations_completed": self.investigations_completed,
            "avg_time_saved_per_investigation_minutes": round(
                self.avg_time_saved_per_investigation_minutes, 1
            ),
            "estimated_cost_savings_usd": round(self.estimated_cost_savings_usd, 2),
            "engineer_hours_recovered": round(self.engineer_hours_recovered, 1),
            "downtime_prevented_minutes": round(self.downtime_prevented_minutes, 1),
            "incidents_auto_resolved": self.incidents_auto_resolved,
            "escalations_prevented": self.escalations_prevented,
        }


@dataclass
class UsageStats:
    """Usage statistics."""

    total_investigations: int = 0
    investigations_this_period: int = 0
    tokens_used: int = 0
    tokens_used_this_period: int = 0
    active_users: int = 0
    investigations_by_severity: dict[str, int] = field(default_factory=dict)
    investigations_by_service: dict[str, int] = field(default_factory=dict)
    investigations_by_outcome: dict[str, int] = field(default_factory=dict)
    top_tools_used: list[tuple[str, int]] = field(default_factory=list)
    avg_investigation_duration_seconds: float = 0.0
    avg_tokens_per_investigation: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_investigations": self.total_investigations,
            "investigations_this_period": self.investigations_this_period,
            "tokens_used": self.tokens_used,
            "tokens_used_this_period": self.tokens_used_this_period,
            "active_users": self.active_users,
            "investigations_by_severity": self.investigations_by_severity,
            "investigations_by_service": self.investigations_by_service,
            "investigations_by_outcome": self.investigations_by_outcome,
            "top_tools_used": self.top_tools_used,
            "avg_investigation_duration_seconds": round(
                self.avg_investigation_duration_seconds, 1
            ),
            "avg_tokens_per_investigation": round(self.avg_tokens_per_investigation, 0),
        }


@dataclass
class InvestigationSummary:
    """Summary of recent investigations."""

    total: int = 0
    successful: int = 0
    partial: int = 0
    failed: int = 0
    abstained: int = 0

    success_rate: float = 0.0
    abstention_rate: float = 0.0

    avg_duration_seconds: float = 0.0
    avg_hypotheses: float = 0.0

    recent_investigations: list[dict[str, Any]] = field(default_factory=list)

    def calculate_rates(self) -> None:
        """Calculate success and abstention rates."""
        if self.total > 0:
            self.success_rate = self.successful / self.total
            self.abstention_rate = self.abstained / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "successful": self.successful,
            "partial": self.partial,
            "failed": self.failed,
            "abstained": self.abstained,
            "success_rate": round(self.success_rate, 3),
            "abstention_rate": round(self.abstention_rate, 3),
            "avg_duration_seconds": round(self.avg_duration_seconds, 1),
            "avg_hypotheses": round(self.avg_hypotheses, 1),
            "recent_investigations": self.recent_investigations[:10],
        }


@dataclass
class DashboardMetrics:
    """Complete dashboard metrics."""

    org_id: str = ""
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    period_start: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) - timedelta(days=30)
    )
    period_end: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    mttr: MTTRStats = field(default_factory=MTTRStats)
    roi: ROIMetrics = field(default_factory=ROIMetrics)
    usage: UsageStats = field(default_factory=UsageStats)
    summary: InvestigationSummary = field(default_factory=InvestigationSummary)

    health_score: float = 0.0

    def calculate_health_score(self) -> None:
        """Calculate overall health score (0-100)."""
        score = 0.0

        if self.summary.success_rate > 0.8:
            score += 30
        elif self.summary.success_rate > 0.6:
            score += 20
        elif self.summary.success_rate > 0.4:
            score += 10

        if self.mttr.improved:
            score += 25
        elif self.mttr.improvement_percent > -10:
            score += 15

        if self.summary.abstention_rate < 0.1:
            score += 20
        elif self.summary.abstention_rate < 0.2:
            score += 10

        if self.usage.investigations_this_period > 10:
            score += 15
        elif self.usage.investigations_this_period > 5:
            score += 10

        if self.roi.time_saved_hours > 0:
            score += 10

        self.health_score = min(100, score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "generated_at": self.generated_at.isoformat(),
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "health_score": round(self.health_score, 0),
            "mttr": self.mttr.to_dict(),
            "roi": self.roi.to_dict(),
            "usage": self.usage.to_dict(),
            "summary": self.summary.to_dict(),
        }


_investigation_store: dict[str, list[InvestigationRecord]] = {}


def record_investigation(
    org_id: str,
    investigation_id: str,
    started_at: datetime,
    completed_at: datetime,
    outcome: InvestigationOutcome,
    tokens_used: int = 0,
    severity: str = "unknown",
    service: str = "",
    hypotheses_generated: int = 0,
    tools_used: list[str] | None = None,
    time_saved_minutes: float = 0.0,
) -> InvestigationRecord:
    """Record a completed investigation."""
    record = InvestigationRecord(
        id=investigation_id,
        started_at=started_at,
        completed_at=completed_at,
        outcome=outcome,
        duration_seconds=(completed_at - started_at).total_seconds(),
        tokens_used=tokens_used,
        severity=severity,
        service=service,
        hypotheses_generated=hypotheses_generated,
        tools_used=tools_used or [],
        time_saved_minutes=time_saved_minutes,
    )

    if org_id not in _investigation_store:
        _investigation_store[org_id] = []

    _investigation_store[org_id].append(record)

    while len(_investigation_store[org_id]) > 10000:
        _investigation_store[org_id].pop(0)

    return record


def get_dashboard_metrics(
    org_id: str,
    period_days: int = 30,
    baseline_mttr_minutes: float = 45.0,
) -> DashboardMetrics:
    """Get dashboard metrics for an organization.

    Args:
        org_id: Organization ID
        period_days: Number of days to analyze
        baseline_mttr_minutes: Baseline MTTR for comparison

    Returns:
        DashboardMetrics with all calculated values
    """
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=period_days)
    prev_period_start = period_start - timedelta(days=period_days)

    investigations = _investigation_store.get(org_id, [])

    current_period = [
        inv for inv in investigations
        if inv.started_at >= period_start
    ]
    previous_period = [
        inv for inv in investigations
        if prev_period_start <= inv.started_at < period_start
    ]

    metrics = DashboardMetrics(
        org_id=org_id,
        period_start=period_start,
        period_end=now,
    )

    metrics.summary = _calculate_summary(current_period)
    metrics.mttr = _calculate_mttr(current_period, previous_period, baseline_mttr_minutes)
    metrics.roi = _calculate_roi(current_period)
    metrics.usage = _calculate_usage(investigations, current_period)

    metrics.calculate_health_score()

    return metrics


def _calculate_summary(investigations: list[InvestigationRecord]) -> InvestigationSummary:
    """Calculate investigation summary."""
    summary = InvestigationSummary(total=len(investigations))

    if not investigations:
        return summary

    total_duration = 0.0
    total_hypotheses = 0

    for inv in investigations:
        if inv.outcome == InvestigationOutcome.ROOT_CAUSE_FOUND:
            summary.successful += 1
        elif inv.outcome == InvestigationOutcome.RESOLVED:
            summary.successful += 1
        elif inv.outcome == InvestigationOutcome.PARTIAL_DIAGNOSIS:
            summary.partial += 1
        elif inv.outcome == InvestigationOutcome.ABSTAINED:
            summary.abstained += 1
        else:
            summary.failed += 1

        total_duration += inv.duration_seconds
        total_hypotheses += inv.hypotheses_generated

    summary.avg_duration_seconds = total_duration / len(investigations)
    summary.avg_hypotheses = total_hypotheses / len(investigations)
    summary.calculate_rates()

    summary.recent_investigations = [
        {
            "id": inv.id,
            "started_at": inv.started_at.isoformat(),
            "outcome": inv.outcome.value,
            "duration_seconds": inv.duration_seconds,
            "service": inv.service,
            "severity": inv.severity,
        }
        for inv in sorted(investigations, key=lambda x: x.started_at, reverse=True)[:10]
    ]

    return summary


def _calculate_mttr(
    current: list[InvestigationRecord],
    previous: list[InvestigationRecord],
    baseline: float,
) -> MTTRStats:
    """Calculate MTTR statistics."""
    stats = MTTRStats()

    if not current:
        return stats

    durations = sorted([inv.duration_seconds / 60 for inv in current])
    stats.sample_size = len(durations)
    stats.current_mttr_minutes = sum(durations) / len(durations)
    stats.fastest_resolution_minutes = min(durations)
    stats.slowest_resolution_minutes = max(durations)

    stats.p50_minutes = durations[len(durations) // 2]
    stats.p90_minutes = durations[int(len(durations) * 0.9)]
    stats.p99_minutes = durations[min(int(len(durations) * 0.99), len(durations) - 1)]

    if previous:
        prev_durations = [inv.duration_seconds / 60 for inv in previous]
        stats.previous_mttr_minutes = sum(prev_durations) / len(prev_durations)
    else:
        stats.previous_mttr_minutes = baseline

    if stats.previous_mttr_minutes > 0:
        stats.improvement_percent = (
            (stats.previous_mttr_minutes - stats.current_mttr_minutes)
            / stats.previous_mttr_minutes
            * 100
        )

    return stats


def _calculate_roi(investigations: list[InvestigationRecord]) -> ROIMetrics:
    """Calculate ROI metrics."""
    roi = ROIMetrics()

    if not investigations:
        return roi

    roi.investigations_completed = len(investigations)

    total_time_saved_minutes = sum(inv.time_saved_minutes for inv in investigations)
    roi.time_saved_hours = total_time_saved_minutes / 60
    roi.engineer_hours_recovered = roi.time_saved_hours

    roi.incidents_auto_resolved = sum(
        1 for inv in investigations
        if inv.outcome == InvestigationOutcome.RESOLVED
    )

    roi.escalations_prevented = sum(
        1 for inv in investigations
        if inv.outcome in (
            InvestigationOutcome.ROOT_CAUSE_FOUND,
            InvestigationOutcome.RESOLVED,
        )
    )

    roi.calculate_savings()

    return roi


def _calculate_usage(
    all_investigations: list[InvestigationRecord],
    current_period: list[InvestigationRecord],
) -> UsageStats:
    """Calculate usage statistics."""
    usage = UsageStats()

    usage.total_investigations = len(all_investigations)
    usage.investigations_this_period = len(current_period)

    usage.tokens_used = sum(inv.tokens_used for inv in all_investigations)
    usage.tokens_used_this_period = sum(inv.tokens_used for inv in current_period)

    if all_investigations:
        usage.avg_tokens_per_investigation = (
            usage.tokens_used / len(all_investigations)
        )
        usage.avg_investigation_duration_seconds = (
            sum(inv.duration_seconds for inv in all_investigations)
            / len(all_investigations)
        )

    for inv in current_period:
        usage.investigations_by_severity[inv.severity] = (
            usage.investigations_by_severity.get(inv.severity, 0) + 1
        )
        if inv.service:
            usage.investigations_by_service[inv.service] = (
                usage.investigations_by_service.get(inv.service, 0) + 1
            )
        usage.investigations_by_outcome[inv.outcome.value] = (
            usage.investigations_by_outcome.get(inv.outcome.value, 0) + 1
        )

    tool_counts: dict[str, int] = {}
    for inv in current_period:
        for tool in inv.tools_used:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

    usage.top_tools_used = sorted(
        tool_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    return usage


def calculate_roi(
    time_saved_minutes: float,
    incidents_resolved: int = 0,
    downtime_prevented_minutes: float = 0.0,
    hourly_engineer_cost: float = 150.0,
    downtime_cost_per_minute: float = 500.0,
) -> dict[str, Any]:
    """Calculate ROI for given metrics.

    Args:
        time_saved_minutes: Total engineer time saved
        incidents_resolved: Number of incidents auto-resolved
        downtime_prevented_minutes: Downtime prevented
        hourly_engineer_cost: Hourly cost of engineer time
        downtime_cost_per_minute: Cost per minute of downtime

    Returns:
        Dictionary with ROI calculations
    """
    time_saved_hours = time_saved_minutes / 60
    engineer_savings = time_saved_hours * hourly_engineer_cost
    downtime_savings = downtime_prevented_minutes * downtime_cost_per_minute
    total_savings = engineer_savings + downtime_savings

    return {
        "time_saved_hours": round(time_saved_hours, 1),
        "engineer_savings_usd": round(engineer_savings, 2),
        "downtime_savings_usd": round(downtime_savings, 2),
        "total_savings_usd": round(total_savings, 2),
        "incidents_resolved": incidents_resolved,
    }
