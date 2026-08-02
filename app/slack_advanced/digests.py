"""Scheduled Digests for Slack.

Sends daily/weekly incident summaries to channels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_advanced.digests")


class DigestFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DigestType(str, Enum):
    INCIDENT_SUMMARY = "incident_summary"
    RELIABILITY_REPORT = "reliability_report"
    TEAM_METRICS = "team_metrics"
    ON_CALL_HANDOFF = "on_call_handoff"


@dataclass
class IncidentSummary:
    """Summary of an incident for the digest."""

    incident_id: str
    title: str
    severity: str
    duration_minutes: float
    service: str
    root_cause: str
    resolution: str
    cost_estimate: float
    occurred_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.incident_id,
            "title": self.title,
            "severity": self.severity,
            "duration_minutes": round(self.duration_minutes, 1),
            "service": self.service,
            "root_cause": self.root_cause[:100],
            "resolution": self.resolution[:100],
            "cost": round(self.cost_estimate, 2),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass
class TeamMetrics:
    """Team metrics for the digest."""

    team_name: str
    incident_count: int
    total_downtime_minutes: float
    mttr_minutes: float
    cost: float
    trend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team_name,
            "incidents": self.incident_count,
            "downtime_minutes": round(self.total_downtime_minutes, 1),
            "mttr_minutes": round(self.mttr_minutes, 1),
            "cost": round(self.cost, 2),
            "trend": self.trend,
        }


@dataclass
class DigestSchedule:
    """A scheduled digest configuration."""

    schedule_id: str
    org_id: str
    channel_id: str
    digest_type: DigestType
    frequency: DigestFrequency
    send_hour_utc: int
    send_day_of_week: int | None
    enabled: bool = True
    last_sent: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.schedule_id,
            "org": self.org_id,
            "channel": self.channel_id,
            "type": self.digest_type.value,
            "frequency": self.frequency.value,
            "send_hour": self.send_hour_utc,
            "send_day": self.send_day_of_week,
            "enabled": self.enabled,
            "last_sent": self.last_sent.isoformat() if self.last_sent else None,
        }


@dataclass
class IncidentDigest:
    """A generated incident digest."""

    digest_id: str
    schedule_id: str
    digest_type: DigestType
    period_start: datetime
    period_end: datetime
    incidents: list[IncidentSummary]
    team_metrics: list[TeamMetrics]
    total_incidents: int
    total_downtime_minutes: float
    total_cost: float
    mttr_minutes: float
    top_services: list[dict[str, Any]]
    improvements: list[str]
    concerns: list[str]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest_id": self.digest_id,
            "schedule_id": self.schedule_id,
            "type": self.digest_type.value,
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "summary": {
                "incidents": self.total_incidents,
                "downtime_minutes": round(self.total_downtime_minutes, 1),
                "cost": round(self.total_cost, 2),
                "mttr_minutes": round(self.mttr_minutes, 1),
            },
            "incidents": [i.to_dict() for i in self.incidents],
            "team_metrics": [t.to_dict() for t in self.team_metrics],
            "top_services": self.top_services,
            "improvements": self.improvements,
            "concerns": self.concerns,
            "generated_at": self.generated_at.isoformat(),
        }


class DigestManager:
    """Manages scheduled digests."""

    def __init__(self) -> None:
        self._schedules: dict[str, DigestSchedule] = {}
        self._generated: dict[str, IncidentDigest] = {}
        self._incident_data: dict[str, list[IncidentSummary]] = {}

    def create_schedule(
        self,
        org_id: str,
        channel_id: str,
        digest_type: DigestType,
        frequency: DigestFrequency,
        send_hour_utc: int = 9,
        send_day_of_week: int | None = None,
    ) -> DigestSchedule:
        """Create a new digest schedule."""
        schedule_id = f"schedule_{org_id}_{channel_id}_{digest_type.value}"

        if frequency == DigestFrequency.WEEKLY and send_day_of_week is None:
            send_day_of_week = 0

        schedule = DigestSchedule(
            schedule_id=schedule_id,
            org_id=org_id,
            channel_id=channel_id,
            digest_type=digest_type,
            frequency=frequency,
            send_hour_utc=send_hour_utc,
            send_day_of_week=send_day_of_week,
        )

        self._schedules[schedule_id] = schedule

        log.info(
            "Created digest schedule: id=%s type=%s freq=%s",
            schedule_id,
            digest_type.value,
            frequency.value,
        )

        return schedule

    def update_schedule(
        self,
        schedule_id: str,
        enabled: bool | None = None,
        send_hour_utc: int | None = None,
        send_day_of_week: int | None = None,
    ) -> DigestSchedule | None:
        """Update a digest schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            return None

        if enabled is not None:
            schedule.enabled = enabled
        if send_hour_utc is not None:
            schedule.send_hour_utc = send_hour_utc
        if send_day_of_week is not None:
            schedule.send_day_of_week = send_day_of_week

        return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a digest schedule."""
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            return True
        return False

    def record_incident(
        self,
        org_id: str,
        incident_id: str,
        title: str,
        severity: str,
        duration_minutes: float,
        service: str,
        root_cause: str,
        resolution: str,
        cost_estimate: float,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record an incident for digest generation."""
        if org_id not in self._incident_data:
            self._incident_data[org_id] = []

        self._incident_data[org_id].append(IncidentSummary(
            incident_id=incident_id,
            title=title,
            severity=severity,
            duration_minutes=duration_minutes,
            service=service,
            root_cause=root_cause,
            resolution=resolution,
            cost_estimate=cost_estimate,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        ))

    def generate_digest(
        self,
        schedule_id: str,
    ) -> IncidentDigest | None:
        """Generate a digest for a schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            return None

        now = datetime.now(timezone.utc)

        if schedule.frequency == DigestFrequency.DAILY:
            period_start = now - timedelta(days=1)
        elif schedule.frequency == DigestFrequency.WEEKLY:
            period_start = now - timedelta(weeks=1)
        else:
            period_start = now - timedelta(days=30)

        incidents = self._get_incidents_in_period(
            schedule.org_id,
            period_start,
            now,
        )

        team_metrics = self._calculate_team_metrics(incidents)

        total_downtime = sum(i.duration_minutes for i in incidents)
        total_cost = sum(i.cost_estimate for i in incidents)
        mttr = total_downtime / len(incidents) if incidents else 0

        top_services = self._get_top_services(incidents)

        improvements, concerns = self._analyze_trends(incidents)

        digest = IncidentDigest(
            digest_id=f"digest_{schedule_id}_{now.timestamp()}",
            schedule_id=schedule_id,
            digest_type=schedule.digest_type,
            period_start=period_start,
            period_end=now,
            incidents=incidents[:10],
            team_metrics=team_metrics,
            total_incidents=len(incidents),
            total_downtime_minutes=total_downtime,
            total_cost=total_cost,
            mttr_minutes=mttr,
            top_services=top_services,
            improvements=improvements,
            concerns=concerns,
        )

        self._generated[digest.digest_id] = digest

        schedule.last_sent = now

        log.info(
            "Generated digest: id=%s incidents=%d",
            digest.digest_id,
            len(incidents),
        )

        return digest

    def _get_incidents_in_period(
        self,
        org_id: str,
        start: datetime,
        end: datetime,
    ) -> list[IncidentSummary]:
        """Get incidents in a time period."""
        all_incidents = self._incident_data.get(org_id, [])

        return [
            i for i in all_incidents
            if start <= i.occurred_at <= end
        ]

    def _calculate_team_metrics(
        self,
        incidents: list[IncidentSummary],
    ) -> list[TeamMetrics]:
        """Calculate metrics by team/service."""
        by_service: dict[str, list[IncidentSummary]] = {}

        for incident in incidents:
            if incident.service not in by_service:
                by_service[incident.service] = []
            by_service[incident.service].append(incident)

        metrics: list[TeamMetrics] = []

        for service, service_incidents in by_service.items():
            total_downtime = sum(i.duration_minutes for i in service_incidents)
            total_cost = sum(i.cost_estimate for i in service_incidents)
            mttr = total_downtime / len(service_incidents) if service_incidents else 0

            metrics.append(TeamMetrics(
                team_name=service,
                incident_count=len(service_incidents),
                total_downtime_minutes=total_downtime,
                mttr_minutes=mttr,
                cost=total_cost,
                trend="stable",
            ))

        metrics.sort(key=lambda m: m.incident_count, reverse=True)

        return metrics[:5]

    def _get_top_services(
        self,
        incidents: list[IncidentSummary],
    ) -> list[dict[str, Any]]:
        """Get top affected services."""
        by_service: dict[str, int] = {}

        for incident in incidents:
            by_service[incident.service] = by_service.get(incident.service, 0) + 1

        sorted_services = sorted(
            by_service.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        return [
            {"service": s, "incidents": c}
            for s, c in sorted_services[:5]
        ]

    def _analyze_trends(
        self,
        incidents: list[IncidentSummary],
    ) -> tuple[list[str], list[str]]:
        """Analyze trends and generate insights."""
        improvements: list[str] = []
        concerns: list[str] = []

        if len(incidents) < 5:
            improvements.append("Low incident count this period")

        sev1_count = sum(1 for i in incidents if "SEV1" in i.severity.upper())
        if sev1_count == 0:
            improvements.append("No SEV1 incidents this period")
        elif sev1_count >= 3:
            concerns.append(f"{sev1_count} SEV1 incidents - investigate systemic issues")

        avg_duration = (
            sum(i.duration_minutes for i in incidents) / len(incidents)
            if incidents else 0
        )
        if avg_duration < 30:
            improvements.append(f"Good MTTR: {avg_duration:.0f} minutes average")
        elif avg_duration > 60:
            concerns.append(f"High MTTR: {avg_duration:.0f} minutes - review resolution processes")

        return improvements, concerns

    def get_schedule(self, schedule_id: str) -> DigestSchedule | None:
        """Get a schedule by ID."""
        return self._schedules.get(schedule_id)

    def get_schedules_for_org(self, org_id: str) -> list[DigestSchedule]:
        """Get all schedules for an org."""
        return [
            s for s in self._schedules.values()
            if s.org_id == org_id
        ]

    def get_due_schedules(self) -> list[DigestSchedule]:
        """Get schedules that are due to run."""
        now = datetime.now(timezone.utc)
        due: list[DigestSchedule] = []

        for schedule in self._schedules.values():
            if not schedule.enabled:
                continue

            if now.hour != schedule.send_hour_utc:
                continue

            if schedule.frequency == DigestFrequency.WEEKLY:
                if now.weekday() != schedule.send_day_of_week:
                    continue

            if schedule.frequency == DigestFrequency.MONTHLY:
                if now.day != 1:
                    continue

            if schedule.last_sent:
                if schedule.frequency == DigestFrequency.DAILY:
                    if (now - schedule.last_sent).total_seconds() < 82800:
                        continue
                elif schedule.frequency == DigestFrequency.WEEKLY:
                    if (now - schedule.last_sent).days < 6:
                        continue

            due.append(schedule)

        return due

    def get_slack_message(
        self,
        digest: IncidentDigest,
    ) -> dict[str, Any]:
        """Generate Slack message for digest."""
        period_name = "Daily" if (digest.period_end - digest.period_start).days <= 1 else "Weekly"

        incidents_text = ""
        for i, incident in enumerate(digest.incidents[:5], 1):
            incidents_text += (
                f"{i}. *{incident.title}* ({incident.severity})\n"
                f"   Service: {incident.service} | Duration: {incident.duration_minutes:.0f}m\n"
            )

        improvements_text = "\n".join(f":white_check_mark: {i}" for i in digest.improvements)
        concerns_text = "\n".join(f":warning: {c}" for c in digest.concerns)

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{period_name} Incident Digest",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Period:* {digest.period_start.strftime('%b %d')} - {digest.period_end.strftime('%b %d, %Y')}\n\n"
                        f"*Summary:*\n"
                        f"- Incidents: {digest.total_incidents}\n"
                        f"- Total Downtime: {digest.total_downtime_minutes:.0f} minutes\n"
                        f"- MTTR: {digest.mttr_minutes:.0f} minutes\n"
                        f"- Estimated Cost: ${digest.total_cost:,.0f}"
                    ),
                },
            },
        ]

        if digest.incidents:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Top Incidents:*\n{incidents_text}",
                },
            })

        if improvements_text or concerns_text:
            insights_text = ""
            if improvements_text:
                insights_text += f"*Improvements:*\n{improvements_text}\n\n"
            if concerns_text:
                insights_text += f"*Concerns:*\n{concerns_text}"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": insights_text.strip(),
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Full Report"},
                    "action_id": f"view_report_{digest.digest_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Manage Subscriptions"},
                    "action_id": "manage_digests",
                },
            ],
        })

        return {
            "blocks": blocks,
            "text": f"{period_name} Incident Digest: {digest.total_incidents} incidents",
        }


_manager: DigestManager | None = None


def get_digest_manager() -> DigestManager:
    """Get the default digest manager."""
    global _manager
    if _manager is None:
        _manager = DigestManager()
    return _manager
