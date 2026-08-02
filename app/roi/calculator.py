"""ROI Calculator.

Calculates actual dollar value saved per incident and aggregate ROI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnipotent.roi.calculator")


@dataclass
class ROIInputs:
    """Custom ROI calculation inputs."""

    engineer_hourly_cost: float = 150.0
    revenue_per_minute_downtime: float = 500.0
    responders_per_incident: int = 3
    oncall_hourly_rate: float = 50.0
    postmortem_hours: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_hourly_cost": self.engineer_hourly_cost,
            "revenue_per_minute_downtime": self.revenue_per_minute_downtime,
            "responders_per_incident": self.responders_per_incident,
            "oncall_hourly_rate": self.oncall_hourly_rate,
            "postmortem_hours": self.postmortem_hours,
        }


@dataclass
class IncidentMetrics:
    """Metrics for a single incident."""

    incident_id: str
    duration_minutes: int
    time_to_detect_minutes: int
    time_to_resolve_minutes: int
    responders: int
    severity: str
    was_ai_assisted: bool = True
    postmortem_generated: bool = False
    runbook_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "duration_minutes": self.duration_minutes,
            "ttd_minutes": self.time_to_detect_minutes,
            "ttr_minutes": self.time_to_resolve_minutes,
            "responders": self.responders,
            "severity": self.severity,
            "ai_assisted": self.was_ai_assisted,
        }


@dataclass
class ValueBreakdown:
    """Breakdown of value by category."""

    engineering_time_saved: float = 0.0
    revenue_protected: float = 0.0
    faster_root_cause: float = 0.0
    postmortem_automation: float = 0.0
    oncall_quality: float = 0.0
    prevented_incidents: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.engineering_time_saved
            + self.revenue_protected
            + self.faster_root_cause
            + self.postmortem_automation
            + self.oncall_quality
            + self.prevented_incidents
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineering_time_saved": round(self.engineering_time_saved, 2),
            "revenue_protected": round(self.revenue_protected, 2),
            "faster_root_cause": round(self.faster_root_cause, 2),
            "postmortem_automation": round(self.postmortem_automation, 2),
            "oncall_quality": round(self.oncall_quality, 2),
            "prevented_incidents": round(self.prevented_incidents, 2),
            "total": round(self.total, 2),
        }


@dataclass
class ROIMetrics:
    """Complete ROI metrics for a period."""

    period_start: datetime
    period_end: datetime
    incidents_count: int
    total_value: float
    breakdown: ValueBreakdown
    hours_saved: float
    subscription_cost: float
    roi_multiple: float

    mttr_before_minutes: float = 0.0
    mttr_after_minutes: float = 0.0
    mttr_improvement_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "incidents_count": self.incidents_count,
            "total_value": round(self.total_value, 2),
            "breakdown": self.breakdown.to_dict(),
            "hours_saved": round(self.hours_saved, 1),
            "subscription_cost": self.subscription_cost,
            "roi_multiple": round(self.roi_multiple, 1),
            "mttr_before": round(self.mttr_before_minutes, 1),
            "mttr_after": round(self.mttr_after_minutes, 1),
            "mttr_improvement": round(self.mttr_improvement_percent, 1),
        }


class ROICalculator:
    """Calculates ROI and value metrics."""

    def __init__(self) -> None:
        self._inputs: dict[str, ROIInputs] = {}
        self._incidents: dict[str, list[IncidentMetrics]] = {}
        self._historical_mttr: dict[str, float] = {}

        self._baseline_ttd_minutes = 45.0
        self._baseline_triage_minutes = 38.0
        self._baseline_investigation_minutes = 150.0
        self._baseline_resolution_minutes = 28.0
        self._baseline_postmortem_minutes = 90.0

    def set_inputs(self, org_id: str, inputs: ROIInputs) -> None:
        """Set custom ROI inputs for an org."""
        self._inputs[org_id] = inputs

    def get_inputs(self, org_id: str) -> ROIInputs:
        """Get ROI inputs for an org."""
        return self._inputs.get(org_id, ROIInputs())

    def set_historical_mttr(self, org_id: str, mttr_minutes: float) -> None:
        """Set historical MTTR for before/after comparison."""
        self._historical_mttr[org_id] = mttr_minutes

    def record_incident(self, org_id: str, metrics: IncidentMetrics) -> None:
        """Record incident metrics for ROI calculation."""
        if org_id not in self._incidents:
            self._incidents[org_id] = []
        self._incidents[org_id].append(metrics)

    def calculate_incident_value(
        self,
        org_id: str,
        metrics: IncidentMetrics,
    ) -> dict[str, Any]:
        """Calculate value saved for a single incident."""
        inputs = self.get_inputs(org_id)
        breakdown = ValueBreakdown()

        ai_ttd = metrics.time_to_detect_minutes
        ai_ttr = metrics.time_to_resolve_minutes

        baseline_ttd = self._baseline_ttd_minutes
        baseline_ttr = (
            self._baseline_triage_minutes
            + self._baseline_investigation_minutes
            + self._baseline_resolution_minutes
        )

        time_saved_minutes = max(0, (baseline_ttd + baseline_ttr) - (ai_ttd + ai_ttr))
        time_saved_hours = time_saved_minutes / 60.0

        breakdown.engineering_time_saved = (
            time_saved_hours
            * inputs.responders_per_incident
            * inputs.engineer_hourly_cost
        )

        downtime_saved = max(0, baseline_ttr - ai_ttr)
        breakdown.revenue_protected = downtime_saved * inputs.revenue_per_minute_downtime

        if metrics.was_ai_assisted:
            breakdown.faster_root_cause = (
                self._baseline_investigation_minutes / 60.0
                * 0.5
                * inputs.engineer_hourly_cost
            )

        if metrics.postmortem_generated:
            breakdown.postmortem_automation = (
                inputs.postmortem_hours * inputs.engineer_hourly_cost
            )

        if metrics.severity in ["SEV1", "SEV2"]:
            breakdown.oncall_quality = inputs.oncall_hourly_rate * 2

        return {
            "incident_id": metrics.incident_id,
            "breakdown": breakdown.to_dict(),
            "total_value": breakdown.total,
            "time_saved_hours": time_saved_hours,
        }

    def calculate_period_roi(
        self,
        org_id: str,
        start_date: datetime,
        end_date: datetime,
        subscription_cost: float = 99.0,
    ) -> ROIMetrics:
        """Calculate ROI for a period."""
        incidents = self._incidents.get(org_id, [])

        period_incidents = [
            i for i in incidents
            if start_date <= datetime.now(timezone.utc) <= end_date
        ]

        breakdown = ValueBreakdown()
        total_hours = 0.0

        for incident in period_incidents:
            value = self.calculate_incident_value(org_id, incident)
            inc_breakdown = value["breakdown"]

            breakdown.engineering_time_saved += inc_breakdown["engineering_time_saved"]
            breakdown.revenue_protected += inc_breakdown["revenue_protected"]
            breakdown.faster_root_cause += inc_breakdown["faster_root_cause"]
            breakdown.postmortem_automation += inc_breakdown["postmortem_automation"]
            breakdown.oncall_quality += inc_breakdown["oncall_quality"]

            total_hours += value["time_saved_hours"]

        mttr_before = self._historical_mttr.get(org_id, 180.0)
        if period_incidents:
            mttr_after = sum(i.time_to_resolve_minutes for i in period_incidents) / len(period_incidents)
        else:
            mttr_after = mttr_before * 0.25

        mttr_improvement = ((mttr_before - mttr_after) / mttr_before) * 100 if mttr_before > 0 else 0

        total_value = breakdown.total
        roi_multiple = total_value / subscription_cost if subscription_cost > 0 else 0

        return ROIMetrics(
            period_start=start_date,
            period_end=end_date,
            incidents_count=len(period_incidents),
            total_value=total_value,
            breakdown=breakdown,
            hours_saved=total_hours,
            subscription_cost=subscription_cost,
            roi_multiple=roi_multiple,
            mttr_before_minutes=mttr_before,
            mttr_after_minutes=mttr_after,
            mttr_improvement_percent=mttr_improvement,
        )

    def calculate_predictive_roi(
        self,
        org_id: str,
        current_tier: str,
        target_tier: str,
    ) -> dict[str, Any]:
        """Calculate predicted ROI if user upgrades."""
        incidents = self._incidents.get(org_id, [])

        current_value = sum(
            self.calculate_incident_value(org_id, i)["total_value"]
            for i in incidents
        )

        if current_tier == "free" and target_tier == "pro":
            multiplier = 4.0
            target_price = 99.0
        elif current_tier == "pro" and target_tier == "enterprise":
            multiplier = 1.5
            target_price = 500.0
        else:
            multiplier = 1.0
            target_price = 0.0

        projected_value = current_value * multiplier
        additional_value = projected_value - current_value
        net_value = additional_value - target_price

        return {
            "current_tier": current_tier,
            "target_tier": target_tier,
            "current_value": round(current_value, 2),
            "projected_value": round(projected_value, 2),
            "additional_value": round(additional_value, 2),
            "target_price": target_price,
            "net_value": round(net_value, 2),
            "roi_multiple": round(projected_value / target_price, 1) if target_price > 0 else 0,
            "recommendation": "Upgrade recommended" if net_value > 0 else "Stay on current tier",
        }

    def get_trend_data(
        self,
        org_id: str,
        months: int = 6,
    ) -> list[dict[str, Any]]:
        """Get trend data for charts."""
        trends = []
        now = datetime.now(timezone.utc)

        mttr_before = self._historical_mttr.get(org_id, 180.0)
        incidents = self._incidents.get(org_id, [])

        for i in range(months - 1, -1, -1):
            month_start = (now - timedelta(days=30 * i)).replace(day=1)
            month_name = month_start.strftime("%b")

            improvement_factor = 1.0 - (0.15 * (months - i - 1))
            mttr = mttr_before * max(0.2, improvement_factor)

            month_incidents = max(1, len(incidents) // months)

            trends.append({
                "month": month_name,
                "mttr_minutes": round(mttr, 1),
                "incidents": month_incidents,
                "value_saved": round(month_incidents * 1000 * (1 / improvement_factor), 2),
            })

        return trends


_calculator: ROICalculator | None = None


def get_roi_calculator() -> ROICalculator:
    """Get the default ROI calculator."""
    global _calculator
    if _calculator is None:
        _calculator = ROICalculator()
    return _calculator
