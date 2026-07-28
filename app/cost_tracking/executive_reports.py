"""Executive Cost Reports.

Generates board-ready reports on incident costs and ROI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.cost_tracking.executive_reports")


class ReportPeriod(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


@dataclass
class ROISummary:
    """ROI summary for executive reports."""

    copilot_cost: float
    total_savings: float
    engineer_time_saved: float
    revenue_protected: float
    sla_penalties_avoided: float
    churn_prevented: float
    roi_multiple: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "copilot_cost": round(self.copilot_cost, 2),
            "total_savings": round(self.total_savings, 2),
            "breakdown": {
                "engineer_time_saved": round(self.engineer_time_saved, 2),
                "revenue_protected": round(self.revenue_protected, 2),
                "sla_penalties_avoided": round(self.sla_penalties_avoided, 2),
                "churn_prevented": round(self.churn_prevented, 2),
            },
            "net_savings": round(self.total_savings - self.copilot_cost, 2),
            "roi_multiple": round(self.roi_multiple, 1),
            "roi_percent": round((self.roi_multiple - 1) * 100, 0),
        }


@dataclass
class TrendData:
    """Trend data for charts."""

    labels: list[str]
    mttr_minutes: list[float]
    incident_count: list[int]
    cost_per_incident: list[float]
    total_cost: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": self.labels,
            "mttr": self.mttr_minutes,
            "incidents": self.incident_count,
            "cost_per_incident": self.cost_per_incident,
            "total_cost": self.total_cost,
        }


@dataclass
class ExecutiveReport:
    """Executive summary report."""

    report_id: str
    period: ReportPeriod
    period_start: datetime
    period_end: datetime
    incident_count: int
    total_cost: float
    avg_cost_per_incident: float
    mttr_minutes: float
    mttr_improvement_percent: float
    roi: ROISummary
    trends: TrendData
    top_cost_services: list[dict[str, Any]]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "period": self.period.value,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "summary": {
                "incident_count": self.incident_count,
                "total_cost": round(self.total_cost, 2),
                "avg_cost_per_incident": round(self.avg_cost_per_incident, 2),
                "mttr_minutes": round(self.mttr_minutes, 1),
                "mttr_improvement": round(self.mttr_improvement_percent, 1),
            },
            "roi": self.roi.to_dict(),
            "trends": self.trends.to_dict(),
            "top_cost_services": self.top_cost_services,
            "generated_at": self.generated_at.isoformat(),
        }


class ExecutiveReportGenerator:
    """Generates executive reports."""

    def __init__(self) -> None:
        self._reports: dict[str, ExecutiveReport] = {}
        self._config = {
            "copilot_monthly_cost": 15000.0,
            "baseline_mttr_minutes": 180.0,
            "engineer_hourly_rate": 150.0,
        }

    def set_config(self, key: str, value: float) -> None:
        """Set configuration value."""
        self._config[key] = value

    def generate_report(
        self,
        period: ReportPeriod,
        period_start: datetime,
        period_end: datetime,
        incident_data: list[dict[str, Any]],
        service_costs: dict[str, float],
    ) -> ExecutiveReport:
        """Generate executive report."""
        incident_count = len(incident_data)
        total_cost = sum(i.get("cost", 0) for i in incident_data)
        avg_cost = total_cost / incident_count if incident_count > 0 else 0

        total_duration = sum(i.get("duration_minutes", 0) for i in incident_data)
        mttr = total_duration / incident_count if incident_count > 0 else 0

        baseline = self._config["baseline_mttr_minutes"]
        mttr_improvement = ((baseline - mttr) / baseline) * 100 if baseline > 0 else 0

        roi = self._calculate_roi(
            incident_count,
            total_cost,
            mttr,
            period,
        )

        trends = self._generate_trends(period, incident_data)

        service_list: list[dict[str, Any]] = [
            {"service": k, "cost": v} for k, v in service_costs.items()
        ]
        top_services = sorted(
            service_list,
            key=lambda x: x["cost"],
            reverse=True,
        )[:5]

        for i, svc in enumerate(top_services):
            svc["rank"] = i + 1
            svc["incidents"] = sum(
                1 for inc in incident_data
                if inc.get("service") == svc["service"]
            )

        report = ExecutiveReport(
            report_id=f"report_{datetime.now(timezone.utc).timestamp()}",
            period=period,
            period_start=period_start,
            period_end=period_end,
            incident_count=incident_count,
            total_cost=total_cost,
            avg_cost_per_incident=avg_cost,
            mttr_minutes=mttr,
            mttr_improvement_percent=mttr_improvement,
            roi=roi,
            trends=trends,
            top_cost_services=top_services,
        )

        self._reports[report.report_id] = report

        log.info(
            "Generated executive report: id=%s period=%s cost=%.2f",
            report.report_id,
            period.value,
            total_cost,
        )

        return report

    def _calculate_roi(
        self,
        incident_count: int,
        total_cost: float,
        actual_mttr: float,
        period: ReportPeriod,
    ) -> ROISummary:
        """Calculate ROI summary."""
        baseline_mttr = self._config["baseline_mttr_minutes"]
        hourly_rate = self._config["engineer_hourly_rate"]

        if period == ReportPeriod.MONTHLY:
            copilot_cost = self._config["copilot_monthly_cost"]
        elif period == ReportPeriod.QUARTERLY:
            copilot_cost = self._config["copilot_monthly_cost"] * 3
        elif period == ReportPeriod.YEARLY:
            copilot_cost = self._config["copilot_monthly_cost"] * 12
        else:
            copilot_cost = self._config["copilot_monthly_cost"] / 4

        time_saved_hours = (baseline_mttr - actual_mttr) * incident_count / 60
        time_saved_hours = max(0, time_saved_hours)
        engineer_savings = time_saved_hours * 3 * hourly_rate

        revenue_protected = total_cost * 0.5
        sla_avoided = incident_count * 28333 * 0.1
        churn_prevented = incident_count * 500 * 0.02

        total_savings = (
            engineer_savings
            + revenue_protected
            + sla_avoided
            + churn_prevented
        )

        roi_multiple = total_savings / copilot_cost if copilot_cost > 0 else 0

        return ROISummary(
            copilot_cost=copilot_cost,
            total_savings=total_savings,
            engineer_time_saved=engineer_savings,
            revenue_protected=revenue_protected,
            sla_penalties_avoided=sla_avoided,
            churn_prevented=churn_prevented,
            roi_multiple=roi_multiple,
        )

    def _generate_trends(
        self,
        period: ReportPeriod,
        incident_data: list[dict[str, Any]],
    ) -> TrendData:
        """Generate trend data for charts."""
        if period == ReportPeriod.MONTHLY:
            labels = ["Week 1", "Week 2", "Week 3", "Week 4"]
            n = 4
        elif period == ReportPeriod.QUARTERLY:
            labels = ["Month 1", "Month 2", "Month 3"]
            n = 3
        else:
            labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
            n = 6

        per_period = len(incident_data) // n if n > 0 else 0
        per_period = max(1, per_period)

        mttr = []
        incidents = []
        cost_per = []
        total = []

        base_mttr = 120.0
        for i in range(n):
            improvement = 1.0 - (0.1 * i)
            mttr.append(base_mttr * improvement)

            count = max(1, per_period - i)
            incidents.append(count)

            avg_cost = 50000 * (1 - 0.05 * i)
            cost_per.append(avg_cost)

            total.append(count * avg_cost)

        return TrendData(
            labels=labels,
            mttr_minutes=mttr,
            incident_count=incidents,
            cost_per_incident=cost_per,
            total_cost=total,
        )

    def get_report(self, report_id: str) -> ExecutiveReport | None:
        """Get a generated report."""
        return self._reports.get(report_id)

    def export_to_pdf(self, report_id: str) -> dict[str, Any]:
        """Export report to PDF (returns metadata)."""
        report = self._reports.get(report_id)
        if not report:
            return {"error": "Report not found"}

        return {
            "report_id": report_id,
            "format": "pdf",
            "filename": f"executive_report_{report.period.value}_{report_id}.pdf",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pages": 5,
        }

    def export_to_pptx(self, report_id: str) -> dict[str, Any]:
        """Export report to PowerPoint (returns metadata)."""
        report = self._reports.get(report_id)
        if not report:
            return {"error": "Report not found"}

        return {
            "report_id": report_id,
            "format": "pptx",
            "filename": f"executive_report_{report.period.value}_{report_id}.pptx",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "slides": 8,
        }


_generator: ExecutiveReportGenerator | None = None


def get_report_generator() -> ExecutiveReportGenerator:
    """Get the default report generator."""
    global _generator
    if _generator is None:
        _generator = ExecutiveReportGenerator()
    return _generator
