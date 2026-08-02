"""ROI Calculator.

Calculates cost savings and business impact with configurable cost models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnipotent.analytics_advanced.roi_calculator")


@dataclass
class CostModel:
    """Configurable cost model for ROI calculations."""

    model_id: str
    org_id: str
    hourly_engineer_cost: float = 150.0
    oncall_multiplier: float = 1.5
    revenue_per_hour: float = 3500.0
    p1_cost_per_minute: float = 58.0
    p2_cost_per_minute: float = 23.0
    p3_cost_per_minute: float = 8.0
    p4_cost_per_minute: float = 2.0
    average_customer_ltv: float = 2400.0
    churn_probability_per_p1: float = 0.008
    affected_customers_per_p1: int = 1200
    support_ticket_cost: float = 45.0
    baseline_mttr_minutes: float = 180.0
    baseline_investigation_minutes: float = 150.0
    baseline_fix_minutes: float = 45.0

    def get_severity_cost(self, severity: str) -> float:
        """Get cost per minute for severity."""
        costs = {
            "P1": self.p1_cost_per_minute,
            "P2": self.p2_cost_per_minute,
            "P3": self.p3_cost_per_minute,
            "P4": self.p4_cost_per_minute,
        }
        return costs.get(severity, self.p3_cost_per_minute)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_costs": {
                "hourly_rate": self.hourly_engineer_cost,
                "oncall_multiplier": self.oncall_multiplier,
            },
            "downtime_costs": {
                "revenue_per_hour": self.revenue_per_hour,
                "p1_per_minute": self.p1_cost_per_minute,
                "p2_per_minute": self.p2_cost_per_minute,
                "p3_per_minute": self.p3_cost_per_minute,
                "p4_per_minute": self.p4_cost_per_minute,
            },
            "customer_impact": {
                "average_ltv": self.average_customer_ltv,
                "churn_probability_per_p1": self.churn_probability_per_p1,
                "affected_customers_per_p1": self.affected_customers_per_p1,
                "support_ticket_cost": self.support_ticket_cost,
            },
            "baseline": {
                "mttr_minutes": self.baseline_mttr_minutes,
                "investigation_minutes": self.baseline_investigation_minutes,
                "fix_minutes": self.baseline_fix_minutes,
            },
        }


@dataclass
class IncidentROI:
    """ROI calculation for a single incident."""

    incident_id: str
    severity: str
    actual_mttr_minutes: float
    baseline_mttr_minutes: float
    time_saved_minutes: float
    engineer_cost_saved: float
    downtime_cost_saved: float
    customer_impact_avoided: float
    total_savings: float
    affected_customers: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "timeline": {
                "actual_mttr_minutes": round(self.actual_mttr_minutes, 1),
                "baseline_mttr_minutes": round(self.baseline_mttr_minutes, 1),
                "time_saved_minutes": round(self.time_saved_minutes, 1),
            },
            "savings": {
                "engineer_cost": round(self.engineer_cost_saved, 2),
                "downtime_cost": round(self.downtime_cost_saved, 2),
                "customer_impact": round(self.customer_impact_avoided, 2),
                "total": round(self.total_savings, 2),
            },
            "affected_customers": self.affected_customers,
        }


@dataclass
class ROICalculation:
    """Aggregated ROI calculation for a period."""

    period_start: datetime
    period_end: datetime
    total_incidents: int
    total_time_saved_hours: float
    total_engineer_cost_saved: float
    total_downtime_prevented_hours: float
    total_downtime_cost_saved: float
    total_customer_impact_avoided: float
    total_savings: float
    roi_by_category: dict[str, float]
    monthly_trend: list[dict[str, Any]]
    cost_model: CostModel

    @property
    def daily_average_savings(self) -> float:
        days = (self.period_end - self.period_start).days or 1
        return self.total_savings / days

    @property
    def per_incident_average(self) -> float:
        return self.total_savings / self.total_incidents if self.total_incidents else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "summary": {
                "total_incidents": self.total_incidents,
                "total_savings": round(self.total_savings, 2),
                "daily_average": round(self.daily_average_savings, 2),
                "per_incident_average": round(self.per_incident_average, 2),
            },
            "breakdown": {
                "time_saved_hours": round(self.total_time_saved_hours, 1),
                "engineer_cost_saved": round(self.total_engineer_cost_saved, 2),
                "downtime_prevented_hours": round(self.total_downtime_prevented_hours, 1),
                "downtime_cost_saved": round(self.total_downtime_cost_saved, 2),
                "customer_impact_avoided": round(self.total_customer_impact_avoided, 2),
            },
            "by_category": {
                k: round(v, 2) for k, v in self.roi_by_category.items()
            },
            "monthly_trend": self.monthly_trend,
        }


@dataclass
class IncidentData:
    """Incident data for ROI calculation."""

    incident_id: str
    severity: str
    created_at: datetime
    resolved_at: datetime | None
    detection_minutes: float = 0.0
    investigation_minutes: float = 0.0
    fix_minutes: float = 0.0
    verification_minutes: float = 0.0
    affected_customers: int = 0
    ai_assisted: bool = True


class ROICalculator:
    """Calculates ROI and cost savings."""

    def __init__(self) -> None:
        self._cost_models: dict[str, CostModel] = {}
        self._incidents: list[IncidentData] = []

    def set_cost_model(self, model: CostModel) -> None:
        """Set cost model for an org."""
        self._cost_models[model.org_id] = model

    def get_cost_model(self, org_id: str) -> CostModel:
        """Get cost model for an org."""
        if org_id not in self._cost_models:
            self._cost_models[org_id] = CostModel(
                model_id=f"model_{org_id}",
                org_id=org_id,
            )
        return self._cost_models[org_id]

    def record_incident(self, incident: IncidentData) -> None:
        """Record an incident for ROI calculation."""
        self._incidents.append(incident)

        if len(self._incidents) > 10000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._incidents = [i for i in self._incidents if i.created_at >= cutoff]

    def calculate_incident_roi(
        self,
        incident: IncidentData,
        org_id: str,
    ) -> IncidentROI:
        """Calculate ROI for a single incident."""
        model = self.get_cost_model(org_id)

        if incident.resolved_at:
            actual_mttr = (
                incident.resolved_at - incident.created_at
            ).total_seconds() / 60
        else:
            actual_mttr = (
                incident.detection_minutes +
                incident.investigation_minutes +
                incident.fix_minutes +
                incident.verification_minutes
            )

        baseline_mttr = model.baseline_mttr_minutes
        time_saved = max(0, baseline_mttr - actual_mttr)

        time_saved_hours = time_saved / 60
        engineer_cost_saved = (
            time_saved_hours *
            model.hourly_engineer_cost *
            model.oncall_multiplier
        )

        severity_cost = model.get_severity_cost(incident.severity)
        downtime_cost_saved = time_saved * severity_cost

        customer_impact = 0.0
        if incident.severity == "P1":
            customers_saved = int(
                incident.affected_customers * model.churn_probability_per_p1
            )
            customer_impact = customers_saved * model.average_customer_ltv

        total_savings = (
            engineer_cost_saved +
            downtime_cost_saved +
            customer_impact
        )

        return IncidentROI(
            incident_id=incident.incident_id,
            severity=incident.severity,
            actual_mttr_minutes=actual_mttr,
            baseline_mttr_minutes=baseline_mttr,
            time_saved_minutes=time_saved,
            engineer_cost_saved=engineer_cost_saved,
            downtime_cost_saved=downtime_cost_saved,
            customer_impact_avoided=customer_impact,
            total_savings=total_savings,
            affected_customers=incident.affected_customers,
        )

    def calculate_period_roi(
        self,
        org_id: str,
        period_days: int = 30,
    ) -> ROICalculation:
        """Calculate ROI for a period."""
        model = self.get_cost_model(org_id)
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents
            if i.created_at >= period_start
        ]

        total_time_saved = 0.0
        total_engineer_cost = 0.0
        total_downtime_hours = 0.0
        total_downtime_cost = 0.0
        total_customer_impact = 0.0
        total_savings = 0.0

        for incident in incidents:
            roi = self.calculate_incident_roi(incident, org_id)

            total_time_saved += roi.time_saved_minutes / 60
            total_engineer_cost += roi.engineer_cost_saved
            total_downtime_hours += roi.time_saved_minutes / 60
            total_downtime_cost += roi.downtime_cost_saved
            total_customer_impact += roi.customer_impact_avoided
            total_savings += roi.total_savings

        roi_by_category = {
            "engineer_time": total_engineer_cost,
            "downtime_prevented": total_downtime_cost,
            "customer_impact": total_customer_impact,
        }

        monthly_trend = self._calculate_monthly_trend(org_id, 6)

        return ROICalculation(
            period_start=period_start,
            period_end=now,
            total_incidents=len(incidents),
            total_time_saved_hours=total_time_saved,
            total_engineer_cost_saved=total_engineer_cost,
            total_downtime_prevented_hours=total_downtime_hours,
            total_downtime_cost_saved=total_downtime_cost,
            total_customer_impact_avoided=total_customer_impact,
            total_savings=total_savings,
            roi_by_category=roi_by_category,
            monthly_trend=monthly_trend,
            cost_model=model,
        )

    def _calculate_monthly_trend(
        self,
        org_id: str,
        months: int,
    ) -> list[dict[str, Any]]:
        """Calculate monthly trend."""
        trend = []
        now = datetime.now(timezone.utc)

        for i in range(months - 1, -1, -1):
            month_start = (now - timedelta(days=30 * (i + 1))).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_end = (now - timedelta(days=30 * i)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            month_incidents = [
                inc for inc in self._incidents
                if month_start <= inc.created_at < month_end
            ]

            month_savings = 0.0
            for inc in month_incidents:
                roi = self.calculate_incident_roi(inc, org_id)
                month_savings += roi.total_savings

            trend.append({
                "month": month_start.strftime("%Y-%m"),
                "incidents": len(month_incidents),
                "savings": round(month_savings, 2),
            })

        return trend

    def compare_with_without_copilot(
        self,
        incident: IncidentData,
        org_id: str,
    ) -> dict[str, Any]:
        """Compare incident timeline with and without Copilot."""
        model = self.get_cost_model(org_id)

        with_copilot = {
            "detection": incident.detection_minutes,
            "triage": 2,
            "investigation": incident.investigation_minutes,
            "fix": incident.fix_minutes,
            "verification": incident.verification_minutes,
            "total": (
                incident.detection_minutes +
                2 +
                incident.investigation_minutes +
                incident.fix_minutes +
                incident.verification_minutes
            ),
        }

        without_copilot = {
            "detection": incident.detection_minutes,
            "triage": 15,
            "investigation": model.baseline_investigation_minutes,
            "fix": model.baseline_fix_minutes,
            "verification": 20,
            "total": (
                incident.detection_minutes +
                15 +
                model.baseline_investigation_minutes +
                model.baseline_fix_minutes +
                20
            ),
        }

        time_saved = without_copilot["total"] - with_copilot["total"]
        time_saved_hours = time_saved / 60

        engineer_cost_saved = (
            time_saved_hours *
            model.hourly_engineer_cost *
            model.oncall_multiplier
        )

        severity_cost = model.get_severity_cost(incident.severity)
        downtime_cost_saved = time_saved * severity_cost

        customer_impact = 0.0
        if incident.severity == "P1":
            customers_saved = int(
                incident.affected_customers * model.churn_probability_per_p1
            )
            customer_impact = customers_saved * model.average_customer_ltv

        return {
            "with_copilot": with_copilot,
            "without_copilot": without_copilot,
            "savings": {
                "time_saved_minutes": round(time_saved, 1),
                "time_saved_formatted": self._format_duration(time_saved),
                "engineer_cost": round(engineer_cost_saved, 2),
                "downtime_cost": round(downtime_cost_saved, 2),
                "customer_impact": round(customer_impact, 2),
                "total": round(
                    engineer_cost_saved + downtime_cost_saved + customer_impact, 2
                ),
                "fewer_affected_customers": incident.affected_customers,
            },
        }

    def _format_duration(self, minutes: float) -> str:
        """Format duration as human-readable string."""
        hours = int(minutes // 60)
        mins = int(minutes % 60)

        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    def get_executive_summary(
        self,
        org_id: str,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Get executive summary for dashboards."""
        roi = self.calculate_period_roi(org_id, period_days)

        previous_roi = self.calculate_period_roi(org_id, period_days * 2)
        previous_savings = previous_roi.total_savings / 2

        change_percent = 0.0
        if previous_savings > 0:
            change_percent = (
                (roi.total_savings - previous_savings) / previous_savings * 100
            )

        return {
            "headline": {
                "total_savings": round(roi.total_savings, 0),
                "change_percent": round(change_percent, 1),
                "period_days": period_days,
            },
            "breakdown": {
                "engineer_time_saved": {
                    "hours": round(roi.total_time_saved_hours, 1),
                    "dollars": round(roi.total_engineer_cost_saved, 0),
                },
                "downtime_prevented": {
                    "hours": round(roi.total_downtime_prevented_hours, 1),
                    "dollars": round(roi.total_downtime_cost_saved, 0),
                },
                "customer_impact_avoided": {
                    "dollars": round(roi.total_customer_impact_avoided, 0),
                },
            },
            "per_incident": {
                "average_savings": round(roi.per_incident_average, 2),
                "total_incidents": roi.total_incidents,
            },
        }


_calculator: ROICalculator | None = None


def get_roi_calculator() -> ROICalculator:
    """Get the default ROI calculator."""
    global _calculator
    if _calculator is None:
        _calculator = ROICalculator()
    return _calculator
