"""Core Incident Metrics.

Comprehensive metrics including granular lifecycle breakdown:
MTTD, MTTA, MTTI, MTTF, MTTV, MTTC, MTBF
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.analytics_advanced.core_metrics")


class Severity(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentPhase(str, Enum):
    DETECTION = "detection"
    ACKNOWLEDGEMENT = "acknowledgement"
    INVESTIGATION = "investigation"
    FIX = "fix"
    VERIFICATION = "verification"
    CLOSURE = "closure"


@dataclass
class IncidentTimestamps:
    """Timestamps for each phase of an incident."""

    incident_id: str
    severity: Severity
    service: str
    team: str
    created_at: datetime
    detected_at: datetime | None = None
    acknowledged_at: datetime | None = None
    investigation_started_at: datetime | None = None
    investigation_completed_at: datetime | None = None
    fix_started_at: datetime | None = None
    fix_completed_at: datetime | None = None
    verification_started_at: datetime | None = None
    verified_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None

    def get_phase_duration(self, phase: IncidentPhase) -> float | None:
        """Get duration in minutes for a phase."""
        if phase == IncidentPhase.DETECTION:
            if self.detected_at and self.created_at:
                return (self.detected_at - self.created_at).total_seconds() / 60
        elif phase == IncidentPhase.ACKNOWLEDGEMENT:
            start = self.detected_at or self.created_at
            if self.acknowledged_at and start:
                return (self.acknowledged_at - start).total_seconds() / 60
        elif phase == IncidentPhase.INVESTIGATION:
            if self.investigation_completed_at and self.investigation_started_at:
                return (
                    self.investigation_completed_at - self.investigation_started_at
                ).total_seconds() / 60
        elif phase == IncidentPhase.FIX:
            if self.fix_completed_at and self.fix_started_at:
                return (
                    self.fix_completed_at - self.fix_started_at
                ).total_seconds() / 60
        elif phase == IncidentPhase.VERIFICATION:
            if self.verified_at and self.verification_started_at:
                return (
                    self.verified_at - self.verification_started_at
                ).total_seconds() / 60
        elif phase == IncidentPhase.CLOSURE:
            if self.closed_at and self.resolved_at:
                return (self.closed_at - self.resolved_at).total_seconds() / 60

        return None

    def get_total_duration(self) -> float | None:
        """Get total incident duration in minutes."""
        end = self.closed_at or self.resolved_at
        if end and self.created_at:
            return (end - self.created_at).total_seconds() / 60
        return None


@dataclass
class LifecycleBreakdown:
    """Breakdown of incident lifecycle phases."""

    mttd_minutes: float
    mtta_minutes: float
    mtti_minutes: float
    mttf_minutes: float
    mttv_minutes: float
    mttc_minutes: float
    total_minutes: float

    @property
    def mttd_percent(self) -> float:
        return (self.mttd_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def mtta_percent(self) -> float:
        return (self.mtta_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def mtti_percent(self) -> float:
        return (self.mtti_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def mttf_percent(self) -> float:
        return (self.mttf_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def mttv_percent(self) -> float:
        return (self.mttv_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def mttc_percent(self) -> float:
        return (self.mttc_minutes / self.total_minutes * 100) if self.total_minutes else 0

    @property
    def bottleneck(self) -> str:
        """Identify the bottleneck phase."""
        phases = {
            "detection": self.mttd_percent,
            "acknowledgement": self.mtta_percent,
            "investigation": self.mtti_percent,
            "fix": self.mttf_percent,
            "verification": self.mttv_percent,
            "closure": self.mttc_percent,
        }
        return max(phases, key=phases.get)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mttd": {"minutes": round(self.mttd_minutes, 1), "percent": round(self.mttd_percent, 1)},
            "mtta": {"minutes": round(self.mtta_minutes, 1), "percent": round(self.mtta_percent, 1)},
            "mtti": {"minutes": round(self.mtti_minutes, 1), "percent": round(self.mtti_percent, 1)},
            "mttf": {"minutes": round(self.mttf_minutes, 1), "percent": round(self.mttf_percent, 1)},
            "mttv": {"minutes": round(self.mttv_minutes, 1), "percent": round(self.mttv_percent, 1)},
            "mttc": {"minutes": round(self.mttc_minutes, 1), "percent": round(self.mttc_percent, 1)},
            "total_minutes": round(self.total_minutes, 1),
            "bottleneck": self.bottleneck,
        }

    def get_progress_bar(self, width: int = 60) -> str:
        """Generate ASCII progress bar showing phase distribution."""
        phases = [
            ("D", self.mttd_percent),
            ("A", self.mtta_percent),
            ("I", self.mtti_percent),
            ("F", self.mttf_percent),
            ("V", self.mttv_percent),
            ("C", self.mttc_percent),
        ]

        bar = ""
        for label, pct in phases:
            chars = max(1, int(pct / 100 * width))
            bar += label * chars

        return f"[{bar[:width]}]"


@dataclass
class IncidentMetrics:
    """Aggregated incident metrics."""

    period_start: datetime
    period_end: datetime
    total_incidents: int
    by_severity: dict[str, int]
    mtta_minutes: float
    mttd_minutes: float
    mtti_minutes: float
    mttf_minutes: float
    mttv_minutes: float
    mttc_minutes: float
    mttr_minutes: float
    mtbf_hours: float
    p50_mttr_minutes: float
    p95_mttr_minutes: float
    p99_mttr_minutes: float
    lifecycle_breakdown: LifecycleBreakdown
    trend_vs_previous: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "total_incidents": self.total_incidents,
            "by_severity": self.by_severity,
            "metrics": {
                "mtta_minutes": round(self.mtta_minutes, 1),
                "mttd_minutes": round(self.mttd_minutes, 1),
                "mtti_minutes": round(self.mtti_minutes, 1),
                "mttf_minutes": round(self.mttf_minutes, 1),
                "mttv_minutes": round(self.mttv_minutes, 1),
                "mttc_minutes": round(self.mttc_minutes, 1),
                "mttr_minutes": round(self.mttr_minutes, 1),
                "mtbf_hours": round(self.mtbf_hours, 1),
            },
            "percentiles": {
                "p50_mttr": round(self.p50_mttr_minutes, 1),
                "p95_mttr": round(self.p95_mttr_minutes, 1),
                "p99_mttr": round(self.p99_mttr_minutes, 1),
            },
            "lifecycle": self.lifecycle_breakdown.to_dict(),
            "trends": self.trend_vs_previous,
        }


class MetricsCalculator:
    """Calculates incident metrics."""

    def __init__(self) -> None:
        self._incidents: list[IncidentTimestamps] = []

    def record_incident(self, incident: IncidentTimestamps) -> None:
        """Record an incident for metrics calculation."""
        self._incidents.append(incident)

        if len(self._incidents) > 10000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._incidents = [i for i in self._incidents if i.created_at >= cutoff]

    def calculate_metrics(
        self,
        period_days: int = 30,
        service: str | None = None,
        team: str | None = None,
        severity: Severity | None = None,
    ) -> IncidentMetrics:
        """Calculate metrics for a period."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)
        period_end = now

        incidents = self._filter_incidents(
            period_start, period_end, service, team, severity
        )

        if not incidents:
            return self._empty_metrics(period_start, period_end)

        by_severity = self._count_by_severity(incidents)

        mttd_values = []
        mtta_values = []
        mtti_values = []
        mttf_values = []
        mttv_values = []
        mttc_values = []
        mttr_values = []

        for inc in incidents:
            if val := inc.get_phase_duration(IncidentPhase.DETECTION):
                mttd_values.append(val)
            if val := inc.get_phase_duration(IncidentPhase.ACKNOWLEDGEMENT):
                mtta_values.append(val)
            if val := inc.get_phase_duration(IncidentPhase.INVESTIGATION):
                mtti_values.append(val)
            if val := inc.get_phase_duration(IncidentPhase.FIX):
                mttf_values.append(val)
            if val := inc.get_phase_duration(IncidentPhase.VERIFICATION):
                mttv_values.append(val)
            if val := inc.get_phase_duration(IncidentPhase.CLOSURE):
                mttc_values.append(val)
            if val := inc.get_total_duration():
                mttr_values.append(val)

        avg_mttd = statistics.mean(mttd_values) if mttd_values else 0.0
        avg_mtta = statistics.mean(mtta_values) if mtta_values else 0.0
        avg_mtti = statistics.mean(mtti_values) if mtti_values else 0.0
        avg_mttf = statistics.mean(mttf_values) if mttf_values else 0.0
        avg_mttv = statistics.mean(mttv_values) if mttv_values else 0.0
        avg_mttc = statistics.mean(mttc_values) if mttc_values else 0.0
        avg_mttr = statistics.mean(mttr_values) if mttr_values else 0.0

        total_avg = avg_mttd + avg_mtta + avg_mtti + avg_mttf + avg_mttv + avg_mttc
        if total_avg == 0:
            total_avg = avg_mttr

        lifecycle = LifecycleBreakdown(
            mttd_minutes=avg_mttd,
            mtta_minutes=avg_mtta,
            mtti_minutes=avg_mtti,
            mttf_minutes=avg_mttf,
            mttv_minutes=avg_mttv,
            mttc_minutes=avg_mttc,
            total_minutes=total_avg,
        )

        mtbf_hours = self._calculate_mtbf(incidents, period_days)

        p50 = statistics.median(mttr_values) if mttr_values else 0.0
        p95 = self._percentile(mttr_values, 95) if mttr_values else 0.0
        p99 = self._percentile(mttr_values, 99) if mttr_values else 0.0

        trends = self._calculate_trends(
            period_start, period_days, service, team, severity, avg_mttr
        )

        return IncidentMetrics(
            period_start=period_start,
            period_end=period_end,
            total_incidents=len(incidents),
            by_severity=by_severity,
            mtta_minutes=avg_mtta,
            mttd_minutes=avg_mttd,
            mtti_minutes=avg_mtti,
            mttf_minutes=avg_mttf,
            mttv_minutes=avg_mttv,
            mttc_minutes=avg_mttc,
            mttr_minutes=avg_mttr,
            mtbf_hours=mtbf_hours,
            p50_mttr_minutes=p50,
            p95_mttr_minutes=p95,
            p99_mttr_minutes=p99,
            lifecycle_breakdown=lifecycle,
            trend_vs_previous=trends,
        )

    def _filter_incidents(
        self,
        start: datetime,
        end: datetime,
        service: str | None,
        team: str | None,
        severity: Severity | None,
    ) -> list[IncidentTimestamps]:
        """Filter incidents by criteria."""
        filtered = []

        for inc in self._incidents:
            if inc.created_at < start or inc.created_at > end:
                continue
            if service and inc.service != service:
                continue
            if team and inc.team != team:
                continue
            if severity and inc.severity != severity:
                continue
            filtered.append(inc)

        return filtered

    def _count_by_severity(
        self,
        incidents: list[IncidentTimestamps],
    ) -> dict[str, int]:
        """Count incidents by severity."""
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for inc in incidents:
            counts[inc.severity.value] += 1
        return counts

    def _calculate_mtbf(
        self,
        incidents: list[IncidentTimestamps],
        period_days: int,
    ) -> float:
        """Calculate mean time between failures in hours."""
        if len(incidents) <= 1:
            return period_days * 24.0

        sorted_incidents = sorted(incidents, key=lambda x: x.created_at)

        gaps = []
        for i in range(1, len(sorted_incidents)):
            gap = (
                sorted_incidents[i].created_at - sorted_incidents[i - 1].created_at
            ).total_seconds() / 3600
            gaps.append(gap)

        return statistics.mean(gaps) if gaps else period_days * 24.0

    def _percentile(self, values: list[float], percentile: int) -> float:
        """Calculate percentile."""
        if not values:
            return 0.0

        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile / 100)
        index = min(index, len(sorted_values) - 1)

        return sorted_values[index]

    def _calculate_trends(
        self,
        current_start: datetime,
        period_days: int,
        service: str | None,
        team: str | None,
        severity: Severity | None,
        current_mttr: float,
    ) -> dict[str, float]:
        """Calculate trends vs previous period."""
        previous_start = current_start - timedelta(days=period_days)
        previous_end = current_start

        previous_incidents = self._filter_incidents(
            previous_start, previous_end, service, team, severity
        )

        if not previous_incidents:
            return {}

        previous_mttr_values = []
        for inc in previous_incidents:
            if val := inc.get_total_duration():
                previous_mttr_values.append(val)

        if not previous_mttr_values:
            return {}

        previous_mttr = statistics.mean(previous_mttr_values)

        if previous_mttr == 0:
            return {}

        mttr_change = ((current_mttr - previous_mttr) / previous_mttr) * 100
        incident_change = (
            (len(self._incidents) - len(previous_incidents)) / len(previous_incidents)
        ) * 100 if previous_incidents else 0

        return {
            "mttr_change_percent": round(mttr_change, 1),
            "incident_count_change_percent": round(incident_change, 1),
        }

    def _empty_metrics(
        self,
        start: datetime,
        end: datetime,
    ) -> IncidentMetrics:
        """Return empty metrics."""
        return IncidentMetrics(
            period_start=start,
            period_end=end,
            total_incidents=0,
            by_severity={s.value: 0 for s in Severity},
            mtta_minutes=0.0,
            mttd_minutes=0.0,
            mtti_minutes=0.0,
            mttf_minutes=0.0,
            mttv_minutes=0.0,
            mttc_minutes=0.0,
            mttr_minutes=0.0,
            mtbf_hours=0.0,
            p50_mttr_minutes=0.0,
            p95_mttr_minutes=0.0,
            p99_mttr_minutes=0.0,
            lifecycle_breakdown=LifecycleBreakdown(
                mttd_minutes=0,
                mtta_minutes=0,
                mtti_minutes=0,
                mttf_minutes=0,
                mttv_minutes=0,
                mttc_minutes=0,
                total_minutes=0,
            ),
        )

    def get_bottleneck_recommendation(
        self,
        lifecycle: LifecycleBreakdown,
    ) -> str:
        """Get recommendation based on bottleneck."""
        recommendations = {
            "detection": "Improve monitoring coverage and alert thresholds",
            "acknowledgement": "Review on-call response times and escalation policies",
            "investigation": "Enhance AI investigation capabilities and runbooks",
            "fix": "Improve auto-fix acceptance rate and code review speed",
            "verification": "Automate verification checks and rollback procedures",
            "closure": "Streamline postmortem and documentation processes",
        }

        return recommendations.get(
            lifecycle.bottleneck,
            "Review overall incident response process",
        )

    def compare_periods(
        self,
        period1_days: int,
        period2_days: int,
        service: str | None = None,
    ) -> dict[str, Any]:
        """Compare metrics between two periods."""
        metrics1 = self.calculate_metrics(period1_days, service)

        now = datetime.now(timezone.utc)
        period2_end = now - timedelta(days=period1_days)
        period2_start = period2_end - timedelta(days=period2_days)

        incidents2 = self._filter_incidents(
            period2_start, period2_end, service, None, None
        )

        mttr2_values = []
        for inc in incidents2:
            if val := inc.get_total_duration():
                mttr2_values.append(val)

        mttr2 = statistics.mean(mttr2_values) if mttr2_values else 0

        return {
            "current": {
                "period_days": period1_days,
                "incidents": metrics1.total_incidents,
                "mttr_minutes": round(metrics1.mttr_minutes, 1),
            },
            "previous": {
                "period_days": period2_days,
                "incidents": len(incidents2),
                "mttr_minutes": round(mttr2, 1),
            },
            "improvement": {
                "incident_reduction_percent": round(
                    ((len(incidents2) - metrics1.total_incidents) / len(incidents2) * 100)
                    if incidents2 else 0,
                    1,
                ),
                "mttr_improvement_percent": round(
                    ((mttr2 - metrics1.mttr_minutes) / mttr2 * 100) if mttr2 else 0,
                    1,
                ),
            },
        }


_calculator: MetricsCalculator | None = None


def get_metrics_calculator() -> MetricsCalculator:
    """Get the default metrics calculator."""
    global _calculator
    if _calculator is None:
        _calculator = MetricsCalculator()
    return _calculator
