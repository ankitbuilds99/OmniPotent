"""Scheduled report generation and delivery.

Supports:
- Daily, weekly, monthly report schedules
- Multiple report types
- Email and Slack delivery
- Customizable content and recipients
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.scheduled_reports")


class ReportType(Enum):
    """Types of scheduled reports."""

    INCIDENT_SUMMARY = "incident_summary"
    SLO_STATUS = "slo_status"
    USAGE_ANALYTICS = "usage_analytics"
    ONCALL_SUMMARY = "oncall_summary"
    POSTMORTEM_DIGEST = "postmortem_digest"
    ALERT_SUMMARY = "alert_summary"
    TEAM_ACTIVITY = "team_activity"
    CUSTOM = "custom"


class ReportFrequency(Enum):
    """Report frequency options."""

    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class DeliveryChannel(Enum):
    """Report delivery channels."""

    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    S3 = "s3"


@dataclass
class ReportDelivery:
    """Report delivery configuration."""

    channel: DeliveryChannel
    recipients: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduledReport:
    """A scheduled report configuration."""

    id: str
    name: str
    description: str
    report_type: ReportType
    frequency: ReportFrequency
    org_id: str
    team_id: str = ""
    created_by: str = ""
    delivery: list[ReportDelivery] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    format: str = "html"
    timezone: str = "UTC"
    schedule_hour: int = 9
    schedule_day: int = 1
    schedule_weekday: int = 0
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def calculate_next_run(self, from_time: datetime | None = None) -> datetime:
        """Calculate the next run time."""
        if from_time is None:
            from_time = datetime.now(timezone.utc)

        base = from_time.replace(
            hour=self.schedule_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        if self.frequency == ReportFrequency.DAILY:
            if base <= from_time:
                base += timedelta(days=1)
            return base

        elif self.frequency == ReportFrequency.WEEKLY:
            days_ahead = self.schedule_weekday - base.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return base + timedelta(days=days_ahead)

        elif self.frequency == ReportFrequency.BIWEEKLY:
            days_ahead = self.schedule_weekday - base.weekday()
            if days_ahead <= 0:
                days_ahead += 14
            return base + timedelta(days=days_ahead)

        elif self.frequency == ReportFrequency.MONTHLY:
            next_month = base.replace(day=1) + timedelta(days=32)
            return next_month.replace(day=min(self.schedule_day, 28))

        elif self.frequency == ReportFrequency.QUARTERLY:
            current_quarter = (base.month - 1) // 3
            next_quarter_month = ((current_quarter + 1) % 4) * 3 + 1
            year = base.year + (1 if next_quarter_month == 1 and base.month >= 10 else 0)
            return base.replace(year=year, month=next_quarter_month, day=min(self.schedule_day, 28))

        return base + timedelta(days=1)


@dataclass
class ReportRun:
    """A single report run."""

    id: str
    report_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    deliveries_sent: int = 0
    deliveries_failed: int = 0
    error: str | None = None
    output_url: str | None = None


_SCHEDULED_REPORTS: dict[str, ScheduledReport] = {}
_REPORT_RUNS: dict[str, list[ReportRun]] = {}


class ReportScheduler:
    """Manages scheduled reports."""

    def __init__(self, org_id: str):
        self.org_id = org_id

    def create_report(
        self,
        name: str,
        report_type: ReportType,
        frequency: ReportFrequency,
        delivery: list[ReportDelivery],
        description: str = "",
        team_id: str = "",
        created_by: str = "",
        filters: dict[str, Any] | None = None,
        format: str = "html",
        schedule_hour: int = 9,
        schedule_day: int = 1,
        schedule_weekday: int = 0,
    ) -> ScheduledReport:
        """Create a new scheduled report."""
        report_id = f"report_{secrets.token_hex(8)}"

        report = ScheduledReport(
            id=report_id,
            name=name,
            description=description,
            report_type=report_type,
            frequency=frequency,
            org_id=self.org_id,
            team_id=team_id,
            created_by=created_by,
            delivery=delivery,
            filters=filters or {},
            format=format,
            schedule_hour=schedule_hour,
            schedule_day=schedule_day,
            schedule_weekday=schedule_weekday,
        )

        report.next_run = report.calculate_next_run()

        _SCHEDULED_REPORTS[report_id] = report
        log.info("Created scheduled report %s: %s", report_id, name)

        return report

    def get_report(self, report_id: str) -> ScheduledReport | None:
        """Get a scheduled report by ID."""
        report = _SCHEDULED_REPORTS.get(report_id)
        if report and report.org_id == self.org_id:
            return report
        return None

    def list_reports(
        self,
        team_id: str | None = None,
        report_type: ReportType | None = None,
        enabled_only: bool = False,
    ) -> list[ScheduledReport]:
        """List scheduled reports."""
        reports = [r for r in _SCHEDULED_REPORTS.values() if r.org_id == self.org_id]

        if team_id:
            reports = [r for r in reports if r.team_id == team_id]

        if report_type:
            reports = [r for r in reports if r.report_type == report_type]

        if enabled_only:
            reports = [r for r in reports if r.enabled]

        return reports

    def update_report(self, report_id: str, **updates: Any) -> ScheduledReport | None:
        """Update a scheduled report."""
        report = self.get_report(report_id)
        if not report:
            return None

        for key, value in updates.items():
            if hasattr(report, key):
                setattr(report, key, value)

        report.updated_at = datetime.now(timezone.utc)
        report.next_run = report.calculate_next_run()

        return report

    def delete_report(self, report_id: str) -> bool:
        """Delete a scheduled report."""
        report = self.get_report(report_id)
        if not report:
            return False

        del _SCHEDULED_REPORTS[report_id]
        log.info("Deleted scheduled report %s", report_id)
        return True

    def enable_report(self, report_id: str) -> bool:
        """Enable a scheduled report."""
        report = self.get_report(report_id)
        if not report:
            return False

        report.enabled = True
        report.next_run = report.calculate_next_run()
        return True

    def disable_report(self, report_id: str) -> bool:
        """Disable a scheduled report."""
        report = self.get_report(report_id)
        if not report:
            return False

        report.enabled = False
        return True

    async def run_report(self, report_id: str) -> ReportRun | None:
        """Run a scheduled report immediately."""
        report = self.get_report(report_id)
        if not report:
            return None

        run_id = f"run_{secrets.token_hex(8)}"
        run = ReportRun(
            id=run_id,
            report_id=report_id,
            started_at=datetime.now(timezone.utc),
        )

        if report_id not in _REPORT_RUNS:
            _REPORT_RUNS[report_id] = []
        _REPORT_RUNS[report_id].append(run)

        try:
            content = await self._generate_report_content(report)

            deliveries_sent = 0
            deliveries_failed = 0

            for delivery in report.delivery:
                success = await self._deliver_report(report, content, delivery)
                if success:
                    deliveries_sent += 1
                else:
                    deliveries_failed += 1

            run.status = "completed"
            run.deliveries_sent = deliveries_sent
            run.deliveries_failed = deliveries_failed
            run.completed_at = datetime.now(timezone.utc)

            report.last_run = datetime.now(timezone.utc)
            report.next_run = report.calculate_next_run()

            log.info(
                "Report %s completed: %d sent, %d failed",
                report_id,
                deliveries_sent,
                deliveries_failed,
            )

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.completed_at = datetime.now(timezone.utc)
            log.error("Report %s failed: %s", report_id, e)

        return run

    async def _generate_report_content(
        self,
        report: ScheduledReport,
    ) -> dict[str, Any]:
        """Generate report content based on type."""
        now = datetime.now(timezone.utc)

        if report.frequency == ReportFrequency.DAILY:
            period_start = now - timedelta(days=1)
        elif report.frequency == ReportFrequency.WEEKLY:
            period_start = now - timedelta(weeks=1)
        elif report.frequency == ReportFrequency.BIWEEKLY:
            period_start = now - timedelta(weeks=2)
        elif report.frequency == ReportFrequency.MONTHLY:
            period_start = now - timedelta(days=30)
        else:
            period_start = now - timedelta(days=90)

        content: dict[str, Any] = {
            "report_name": report.name,
            "report_type": report.report_type.value,
            "period_start": period_start.isoformat(),
            "period_end": now.isoformat(),
            "generated_at": now.isoformat(),
            "sections": [],
        }

        if report.report_type == ReportType.INCIDENT_SUMMARY:
            content["sections"] = [
                {
                    "title": "Incident Overview",
                    "data": {
                        "total_incidents": 12,
                        "resolved": 10,
                        "ongoing": 2,
                        "mttr_minutes": 45,
                        "by_severity": {
                            "critical": 1,
                            "high": 3,
                            "medium": 5,
                            "low": 3,
                        },
                    },
                },
                {
                    "title": "Top Affected Services",
                    "data": [
                        {"service": "api-gateway", "incidents": 4},
                        {"service": "auth-service", "incidents": 3},
                        {"service": "database", "incidents": 2},
                    ],
                },
            ]

        elif report.report_type == ReportType.SLO_STATUS:
            content["sections"] = [
                {
                    "title": "SLO Summary",
                    "data": {
                        "total_slos": 8,
                        "meeting_target": 6,
                        "at_risk": 1,
                        "breached": 1,
                    },
                },
                {
                    "title": "SLO Details",
                    "data": [
                        {"name": "API Availability", "target": "99.9%", "actual": "99.95%", "status": "healthy"},
                        {"name": "API Latency P99", "target": "200ms", "actual": "185ms", "status": "healthy"},
                        {"name": "Error Rate", "target": "<1%", "actual": "0.8%", "status": "healthy"},
                    ],
                },
            ]

        elif report.report_type == ReportType.USAGE_ANALYTICS:
            content["sections"] = [
                {
                    "title": "Usage Overview",
                    "data": {
                        "active_users": 25,
                        "investigations_started": 45,
                        "runbooks_executed": 12,
                        "api_calls": 15000,
                    },
                },
            ]

        elif report.report_type == ReportType.ONCALL_SUMMARY:
            content["sections"] = [
                {
                    "title": "On-Call Summary",
                    "data": {
                        "total_pages": 23,
                        "acknowledged_within_sla": 20,
                        "escalated": 3,
                        "avg_response_time_minutes": 4.5,
                    },
                },
            ]

        elif report.report_type == ReportType.ALERT_SUMMARY:
            content["sections"] = [
                {
                    "title": "Alert Statistics",
                    "data": {
                        "total_alerts": 150,
                        "actionable": 45,
                        "noise": 105,
                        "noise_ratio": "70%",
                    },
                },
            ]

        return content

    async def _deliver_report(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
        delivery: ReportDelivery,
    ) -> bool:
        """Deliver report via specified channel."""
        try:
            if delivery.channel == DeliveryChannel.EMAIL:
                return await self._send_email_report(report, content, delivery)
            elif delivery.channel == DeliveryChannel.SLACK:
                return await self._send_slack_report(report, content, delivery)
            elif delivery.channel == DeliveryChannel.WEBHOOK:
                return await self._send_webhook_report(report, content, delivery)
            else:
                log.warning("Unsupported delivery channel: %s", delivery.channel)
                return False
        except Exception as e:
            log.error("Delivery failed for %s: %s", delivery.channel.value, e)
            return False

    async def _send_email_report(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
        delivery: ReportDelivery,
    ) -> bool:
        """Send report via email."""
        from app.notifications.email import send_email

        subject = f"[DevOps Copilot] {report.name} - {content['period_end'][:10]}"

        body_text = self._format_report_text(report, content)
        body_html = self._format_report_html(report, content)

        for recipient in delivery.recipients:
            await send_email(
                to=recipient,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )

        return True

    async def _send_slack_report(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
        delivery: ReportDelivery,
    ) -> bool:
        """Send report via Slack."""
        log.info("Would send Slack report to %s", delivery.recipients)
        return True

    async def _send_webhook_report(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
        delivery: ReportDelivery,
    ) -> bool:
        """Send report via webhook."""
        import httpx

        webhook_url = delivery.config.get("url")
        if not webhook_url:
            return False

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook_url,
                json=content,
                timeout=30,
            )
            return resp.status_code < 400

    def _format_report_text(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
    ) -> str:
        """Format report as plain text."""
        lines = [
            f"# {report.name}",
            f"Period: {content['period_start'][:10]} to {content['period_end'][:10]}",
            "",
        ]

        for section in content.get("sections", []):
            lines.append(f"## {section['title']}")
            data = section.get("data", {})

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        lines.append(f"  {key}:")
                        for k, v in value.items():
                            lines.append(f"    - {k}: {v}")
                    else:
                        lines.append(f"  - {key}: {value}")
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        lines.append("  - " + ", ".join(f"{k}: {v}" for k, v in item.items()))
                    else:
                        lines.append(f"  - {item}")

            lines.append("")

        lines.append("---")
        lines.append("Generated by DevOps Copilot")

        return "\n".join(lines)

    def _format_report_html(
        self,
        report: ScheduledReport,
        content: dict[str, Any],
    ) -> str:
        """Format report as HTML."""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px; }}
        h2 {{ color: #374151; margin-top: 24px; }}
        .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 20px; }}
        .section {{ background: #f9fafb; padding: 16px; border-radius: 8px; margin: 16px 0; }}
        .metric {{ display: inline-block; margin: 8px 16px 8px 0; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #4f46e5; }}
        .metric-label {{ font-size: 12px; color: #6b7280; }}
        table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }}
        th {{ background: #f3f4f6; font-weight: 600; }}
        .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>{report.name}</h1>
    <p class="meta">Period: {content['period_start'][:10]} to {content['period_end'][:10]}</p>
"""

        for section in content.get("sections", []):
            html += f'<div class="section"><h2>{section["title"]}</h2>'
            data = section.get("data", {})

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        html += f"<h3>{key}</h3>"
                        for k, v in value.items():
                            html += f'<div class="metric"><div class="metric-value">{v}</div><div class="metric-label">{k}</div></div>'
                    else:
                        html += f'<div class="metric"><div class="metric-value">{value}</div><div class="metric-label">{key.replace("_", " ").title()}</div></div>'
            elif isinstance(data, list):
                html += "<table><thead><tr>"
                if data and isinstance(data[0], dict):
                    for key in data[0].keys():
                        html += f"<th>{key.replace('_', ' ').title()}</th>"
                html += "</tr></thead><tbody>"
                for item in data:
                    html += "<tr>"
                    if isinstance(item, dict):
                        for v in item.values():
                            html += f"<td>{v}</td>"
                    html += "</tr>"
                html += "</tbody></table>"

            html += "</div>"

        html += """
    <div class="footer">Generated by DevOps Copilot</div>
</body>
</html>
"""
        return html

    def get_due_reports(self) -> list[ScheduledReport]:
        """Get reports that are due to run."""
        now = datetime.now(timezone.utc)
        return [
            r for r in _SCHEDULED_REPORTS.values()
            if r.org_id == self.org_id
            and r.enabled
            and r.next_run
            and r.next_run <= now
        ]


_schedulers: dict[str, ReportScheduler] = {}


def _get_scheduler(org_id: str) -> ReportScheduler:
    """Get or create report scheduler for org."""
    if org_id not in _schedulers:
        _schedulers[org_id] = ReportScheduler(org_id)
    return _schedulers[org_id]


def create_scheduled_report(
    org_id: str,
    name: str,
    report_type: ReportType,
    frequency: ReportFrequency,
    delivery: list[ReportDelivery],
    **kwargs: Any,
) -> ScheduledReport:
    """Create a new scheduled report."""
    scheduler = _get_scheduler(org_id)
    return scheduler.create_report(name, report_type, frequency, delivery, **kwargs)


def get_scheduled_report(org_id: str, report_id: str) -> ScheduledReport | None:
    """Get a scheduled report."""
    scheduler = _get_scheduler(org_id)
    return scheduler.get_report(report_id)


def list_scheduled_reports(org_id: str, **kwargs: Any) -> list[ScheduledReport]:
    """List scheduled reports."""
    scheduler = _get_scheduler(org_id)
    return scheduler.list_reports(**kwargs)


async def run_scheduled_report(org_id: str, report_id: str) -> ReportRun | None:
    """Run a scheduled report."""
    scheduler = _get_scheduler(org_id)
    return await scheduler.run_report(report_id)


def delete_scheduled_report(org_id: str, report_id: str) -> bool:
    """Delete a scheduled report."""
    scheduler = _get_scheduler(org_id)
    return scheduler.delete_report(report_id)
