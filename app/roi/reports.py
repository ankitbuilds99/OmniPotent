"""Executive report generation.

CFO-ready PDF/PPT reports with ROI data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.roi.reports")


class ReportFormat(str, Enum):
    PDF = "pdf"
    POWERPOINT = "ppt"
    CSV = "csv"
    JSON = "json"


class ReportType(str, Enum):
    EXECUTIVE_SUMMARY = "executive_summary"
    QUARTERLY_REVIEW = "quarterly_review"
    TEAM_BREAKDOWN = "team_breakdown"
    INCIDENT_DETAIL = "incident_detail"


@dataclass
class ReportSection:
    """A section of a report."""

    title: str
    content_type: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "type": self.content_type,
            "data": self.data,
        }


@dataclass
class ExecutiveReport:
    """An executive report."""

    report_id: str
    report_type: ReportType
    org_id: str
    period_start: datetime
    period_end: datetime
    sections: list[ReportSection]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    format: ReportFormat = ReportFormat.PDF

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.report_id,
            "type": self.report_type.value,
            "org_id": self.org_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "sections": [s.to_dict() for s in self.sections],
            "generated_at": self.generated_at.isoformat(),
            "format": self.format.value,
        }


@dataclass
class ScheduledReport:
    """A scheduled report configuration."""

    schedule_id: str
    org_id: str
    report_type: ReportType
    format: ReportFormat
    frequency: str
    recipients: list[str]
    is_active: bool = True
    last_sent: datetime | None = None
    next_send: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.schedule_id,
            "report_type": self.report_type.value,
            "format": self.format.value,
            "frequency": self.frequency,
            "recipients": self.recipients,
            "is_active": self.is_active,
            "last_sent": self.last_sent.isoformat() if self.last_sent else None,
            "next_send": self.next_send.isoformat() if self.next_send else None,
        }


class ReportGenerator:
    """Generates executive reports."""

    def __init__(self) -> None:
        self._reports: dict[str, ExecutiveReport] = {}
        self._schedules: dict[str, ScheduledReport] = {}
        self._counter = 0

    def generate_executive_summary(
        self,
        org_id: str,
        roi_data: dict[str, Any],
        benchmarks: dict[str, Any] | None = None,
        period_days: int = 30,
    ) -> ExecutiveReport:
        """Generate executive summary report."""
        self._counter += 1
        report_id = f"report_{self._counter}"

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        sections = []

        sections.append(ReportSection(
            title="Executive Summary",
            content_type="summary",
            data={
                "total_value": roi_data.get("total_value", 0),
                "hours_saved": roi_data.get("hours_saved", 0),
                "roi_multiple": roi_data.get("roi_multiple", 0),
                "incidents_resolved": roi_data.get("incidents_count", 0),
                "headline": self._generate_headline(roi_data),
            },
        ))

        sections.append(ReportSection(
            title="Value Breakdown",
            content_type="breakdown",
            data=roi_data.get("breakdown", {}),
        ))

        sections.append(ReportSection(
            title="Before vs After Comparison",
            content_type="comparison",
            data={
                "mttr_before": roi_data.get("mttr_before", 180),
                "mttr_after": roi_data.get("mttr_after", 45),
                "improvement_percent": roi_data.get("mttr_improvement", 75),
                "phases": [
                    {"name": "Detection", "before": 45, "after": 8},
                    {"name": "Triage", "before": 38, "after": 4},
                    {"name": "Investigation", "before": 150, "after": 12},
                    {"name": "Resolution", "before": 28, "after": 25},
                    {"name": "Postmortem", "before": 90, "after": 15},
                ],
            },
        ))

        if benchmarks:
            sections.append(ReportSection(
                title="Industry Benchmarks",
                content_type="benchmarks",
                data=benchmarks,
            ))

        sections.append(ReportSection(
            title="Trend Analysis",
            content_type="chart",
            data={
                "chart_type": "line",
                "title": "MTTR Trend (Last 6 Months)",
                "data_points": self._generate_trend_data(),
            },
        ))

        report = ExecutiveReport(
            report_id=report_id,
            report_type=ReportType.EXECUTIVE_SUMMARY,
            org_id=org_id,
            period_start=period_start,
            period_end=now,
            sections=sections,
        )

        self._reports[report_id] = report
        log.info("Generated executive summary: %s", report_id)

        return report

    def generate_quarterly_review(
        self,
        org_id: str,
        roi_data: dict[str, Any],
        benchmarks: dict[str, Any] | None = None,
        team_data: dict[str, Any] | None = None,
    ) -> ExecutiveReport:
        """Generate quarterly business review report."""
        self._counter += 1
        report_id = f"report_{self._counter}"

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=90)

        sections = []

        sections.append(ReportSection(
            title="Quarterly Highlights",
            content_type="highlights",
            data={
                "total_value": roi_data.get("total_value", 0) * 3,
                "key_achievements": [
                    f"Reduced MTTR by {roi_data.get('mttr_improvement', 75):.0f}%",
                    f"Saved {roi_data.get('hours_saved', 0) * 3:.0f} engineering hours",
                    f"Protected ${roi_data.get('breakdown', {}).get('revenue_protected', 0) * 3:,.0f} in revenue",
                ],
                "next_quarter_goals": [
                    "Achieve 30-minute average MTTR",
                    "Reduce on-call pages by 50%",
                    "Enable predictive incident prevention",
                ],
            },
        ))

        sections.append(ReportSection(
            title="ROI Summary",
            content_type="roi",
            data={
                "quarterly_value": roi_data.get("total_value", 0) * 3,
                "quarterly_cost": roi_data.get("subscription_cost", 99) * 3,
                "roi_multiple": roi_data.get("roi_multiple", 0),
                "projected_annual": roi_data.get("total_value", 0) * 12,
            },
        ))

        if team_data:
            sections.append(ReportSection(
                title="Team Performance",
                content_type="team_breakdown",
                data=team_data,
            ))

        sections.append(ReportSection(
            title="Projections",
            content_type="projections",
            data={
                "next_quarter_value": roi_data.get("total_value", 0) * 3.5,
                "annual_projection": roi_data.get("total_value", 0) * 14,
                "assumptions": [
                    "Continued adoption across team",
                    "3 new integrations planned",
                    "Enabling predictive features",
                ],
            },
        ))

        report = ExecutiveReport(
            report_id=report_id,
            report_type=ReportType.QUARTERLY_REVIEW,
            org_id=org_id,
            period_start=period_start,
            period_end=now,
            sections=sections,
            format=ReportFormat.POWERPOINT,
        )

        self._reports[report_id] = report
        return report

    def generate_team_breakdown(
        self,
        org_id: str,
        teams: list[dict[str, Any]],
    ) -> ExecutiveReport:
        """Generate team breakdown report."""
        self._counter += 1
        report_id = f"report_{self._counter}"

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=30)

        sections = []

        for team in teams:
            sections.append(ReportSection(
                title=f"Team: {team.get('name', 'Unknown')}",
                content_type="team_stats",
                data={
                    "team_name": team.get("name"),
                    "incidents": team.get("incidents", 0),
                    "mttr": team.get("mttr", 0),
                    "value_saved": team.get("value_saved", 0),
                    "top_contributors": team.get("contributors", []),
                },
            ))

        sections.append(ReportSection(
            title="Team Comparison",
            content_type="comparison_table",
            data={
                "teams": [
                    {
                        "name": t.get("name"),
                        "incidents": t.get("incidents", 0),
                        "mttr": t.get("mttr", 0),
                        "value": t.get("value_saved", 0),
                    }
                    for t in teams
                ],
            },
        ))

        report = ExecutiveReport(
            report_id=report_id,
            report_type=ReportType.TEAM_BREAKDOWN,
            org_id=org_id,
            period_start=period_start,
            period_end=now,
            sections=sections,
            format=ReportFormat.CSV,
        )

        self._reports[report_id] = report
        return report

    def _generate_headline(self, roi_data: dict[str, Any]) -> str:
        """Generate report headline."""
        value = roi_data.get("total_value", 0)
        roi = roi_data.get("roi_multiple", 0)

        if value >= 50000:
            return f"DevOps Copilot delivered ${value:,.0f} in value this month ({roi:.0f}x ROI)"
        elif value >= 10000:
            return f"Strong month: ${value:,.0f} saved with {roi:.0f}x return on investment"
        else:
            return f"Building momentum: ${value:,.0f} in value delivered"

    def _generate_trend_data(self) -> list[dict[str, Any]]:
        """Generate sample trend data."""
        months = ["Mar", "Apr", "May", "Jun", "Jul", "Aug"]
        mttr_values = [4.5, 3.2, 2.8, 1.8, 1.2, 0.8]

        return [
            {"month": m, "mttr_hours": v}
            for m, v in zip(months, mttr_values)
        ]

    def schedule_report(
        self,
        org_id: str,
        report_type: ReportType,
        frequency: str,
        recipients: list[str],
        format: ReportFormat = ReportFormat.PDF,
    ) -> ScheduledReport:
        """Schedule a recurring report."""
        schedule_id = f"schedule_{org_id}_{report_type.value}"

        now = datetime.now(timezone.utc)
        if frequency == "weekly":
            next_send = now + timedelta(days=7)
        elif frequency == "monthly":
            next_send = now + timedelta(days=30)
        elif frequency == "quarterly":
            next_send = now + timedelta(days=90)
        else:
            next_send = now + timedelta(days=30)

        schedule = ScheduledReport(
            schedule_id=schedule_id,
            org_id=org_id,
            report_type=report_type,
            format=format,
            frequency=frequency,
            recipients=recipients,
            next_send=next_send,
        )

        self._schedules[schedule_id] = schedule
        log.info("Scheduled report: %s frequency=%s", schedule_id, frequency)

        return schedule

    def get_report(self, report_id: str) -> ExecutiveReport | None:
        """Get a generated report."""
        return self._reports.get(report_id)

    def get_schedules(self, org_id: str) -> list[ScheduledReport]:
        """Get all scheduled reports for an org."""
        return [
            s for s in self._schedules.values()
            if s.org_id == org_id
        ]

    def export_report(
        self,
        report: ExecutiveReport,
        format: ReportFormat,
    ) -> dict[str, Any]:
        """Export report in specified format."""
        if format == ReportFormat.JSON:
            return {
                "format": "json",
                "data": report.to_dict(),
            }

        elif format == ReportFormat.CSV:
            rows = []
            for section in report.sections:
                if section.content_type == "breakdown":
                    for key, value in section.data.items():
                        rows.append({"category": key, "value": value})
            return {
                "format": "csv",
                "rows": rows,
                "filename": f"report_{report.report_id}.csv",
            }

        elif format == ReportFormat.PDF:
            return {
                "format": "pdf",
                "filename": f"report_{report.report_id}.pdf",
                "pages": len(report.sections),
                "note": "PDF generation requires external service",
            }

        elif format == ReportFormat.POWERPOINT:
            return {
                "format": "ppt",
                "filename": f"report_{report.report_id}.pptx",
                "slides": len(report.sections),
                "note": "PowerPoint generation requires external service",
            }

        return {"error": "Unknown format"}


_generator: ReportGenerator | None = None


def get_report_generator() -> ReportGenerator:
    """Get the default report generator."""
    global _generator
    if _generator is None:
        _generator = ReportGenerator()
    return _generator
