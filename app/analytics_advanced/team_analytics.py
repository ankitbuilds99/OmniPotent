"""Team and Service Analytics.

Team performance comparison, service health, and on-call burden analysis.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.analytics_advanced.team_analytics")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class TeamIncident:
    """Incident data for team analytics."""

    incident_id: str
    team: str
    service: str
    severity: str
    mttr_minutes: float
    ai_assisted: bool
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass
class OnCallPage:
    """On-call page record."""

    page_id: str
    user_id: str
    team: str
    severity: str
    timestamp: datetime
    is_night: bool
    is_weekend: bool
    acknowledged_at: datetime | None = None


@dataclass
class TeamMetrics:
    """Metrics for a team."""

    team: str
    incident_count: int
    mttr_minutes: float
    ai_usage_rate: float
    trend_percent: float
    p1_count: int
    p2_count: int
    repeat_incident_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "incidents": self.incident_count,
            "mttr_minutes": round(self.mttr_minutes, 1),
            "ai_usage_percent": round(self.ai_usage_rate * 100, 1),
            "trend_percent": round(self.trend_percent, 1),
            "p1_count": self.p1_count,
            "p2_count": self.p2_count,
            "repeat_incident_rate": round(self.repeat_incident_rate * 100, 1),
        }


@dataclass
class ServiceHealth:
    """Health metrics for a service."""

    service: str
    status: HealthStatus
    incident_count: int
    mttr_minutes: float
    ai_usage_rate: float
    blast_radius: str
    reliability_score: float
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "status": self.status.value,
            "incidents": self.incident_count,
            "mttr_minutes": round(self.mttr_minutes, 1),
            "ai_usage_percent": round(self.ai_usage_rate * 100, 1),
            "blast_radius": self.blast_radius,
            "reliability_score": round(self.reliability_score, 2),
            "issues": self.issues,
        }


@dataclass
class OnCallBurden:
    """On-call burden metrics for a user."""

    user_id: str
    team: str
    total_pages: int
    p1_pages: int
    night_pages: int
    weekend_pages: int
    burden_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.user_id,
            "team": self.team,
            "pages": {
                "total": self.total_pages,
                "p1": self.p1_pages,
                "night": self.night_pages,
                "weekend": self.weekend_pages,
            },
            "burden_score": round(self.burden_score, 0),
        }


@dataclass
class TeamComparison:
    """Team comparison results."""

    period_start: datetime
    period_end: datetime
    teams: list[TeamMetrics]
    company_average: TeamMetrics
    insights: list[str]
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "teams": [t.to_dict() for t in self.teams],
            "company_average": self.company_average.to_dict(),
            "insights": self.insights,
            "recommendations": self.recommendations,
        }


class TeamAnalytics:
    """Analyzes team and service performance."""

    def __init__(self) -> None:
        self._incidents: list[TeamIncident] = []
        self._pages: list[OnCallPage] = []
        self._service_dependencies: dict[str, list[str]] = {}

    def record_incident(self, incident: TeamIncident) -> None:
        """Record an incident."""
        self._incidents.append(incident)

        if len(self._incidents) > 50000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._incidents = [i for i in self._incidents if i.created_at >= cutoff]

    def record_page(self, page: OnCallPage) -> None:
        """Record an on-call page."""
        self._pages.append(page)

        if len(self._pages) > 100000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._pages = [p for p in self._pages if p.timestamp >= cutoff]

    def set_service_dependencies(
        self,
        service: str,
        dependencies: list[str],
    ) -> None:
        """Set service dependencies for blast radius calculation."""
        self._service_dependencies[service] = dependencies

    def compare_teams(
        self,
        period_days: int = 30,
    ) -> TeamComparison:
        """Compare team performance."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents if i.created_at >= period_start
        ]

        by_team: dict[str, list[TeamIncident]] = {}
        for inc in incidents:
            if inc.team not in by_team:
                by_team[inc.team] = []
            by_team[inc.team].append(inc)

        teams = []
        for team, team_incidents in by_team.items():
            metrics = self._calculate_team_metrics(
                team, team_incidents, period_days
            )
            teams.append(metrics)

        teams.sort(key=lambda t: t.mttr_minutes)

        company_avg = self._calculate_company_average(teams)

        insights = self._generate_insights(teams, company_avg)
        recommendations = self._generate_recommendations(teams)

        return TeamComparison(
            period_start=period_start,
            period_end=now,
            teams=teams,
            company_average=company_avg,
            insights=insights,
            recommendations=recommendations,
        )

    def _calculate_team_metrics(
        self,
        team: str,
        incidents: list[TeamIncident],
        period_days: int,
    ) -> TeamMetrics:
        """Calculate metrics for a team."""
        mttr_values = [i.mttr_minutes for i in incidents if i.mttr_minutes > 0]
        avg_mttr = statistics.mean(mttr_values) if mttr_values else 0.0

        ai_assisted = sum(1 for i in incidents if i.ai_assisted)
        ai_rate = ai_assisted / len(incidents) if incidents else 0.0

        p1_count = sum(1 for i in incidents if i.severity == "P1")
        p2_count = sum(1 for i in incidents if i.severity == "P2")

        trend = self._calculate_trend(team, period_days)

        repeat_rate = self._calculate_repeat_rate(incidents)

        return TeamMetrics(
            team=team,
            incident_count=len(incidents),
            mttr_minutes=avg_mttr,
            ai_usage_rate=ai_rate,
            trend_percent=trend,
            p1_count=p1_count,
            p2_count=p2_count,
            repeat_incident_rate=repeat_rate,
        )

    def _calculate_trend(self, team: str, period_days: int) -> float:
        """Calculate incident trend vs previous period."""
        now = datetime.now(timezone.utc)
        current_start = now - timedelta(days=period_days)
        previous_start = current_start - timedelta(days=period_days)

        current = sum(
            1 for i in self._incidents
            if i.team == team and i.created_at >= current_start
        )

        previous = sum(
            1 for i in self._incidents
            if i.team == team and previous_start <= i.created_at < current_start
        )

        if previous == 0:
            return 0.0

        return ((current - previous) / previous) * 100

    def _calculate_repeat_rate(
        self,
        incidents: list[TeamIncident],
    ) -> float:
        """Calculate rate of repeat incidents."""
        service_counts: dict[str, int] = {}

        for inc in incidents:
            service_counts[inc.service] = service_counts.get(inc.service, 0) + 1

        repeats = sum(1 for count in service_counts.values() if count > 1)
        return repeats / len(service_counts) if service_counts else 0.0

    def _calculate_company_average(
        self,
        teams: list[TeamMetrics],
    ) -> TeamMetrics:
        """Calculate company-wide averages."""
        if not teams:
            return TeamMetrics(
                team="Company Average",
                incident_count=0,
                mttr_minutes=0,
                ai_usage_rate=0,
                trend_percent=0,
                p1_count=0,
                p2_count=0,
                repeat_incident_rate=0,
            )

        return TeamMetrics(
            team="Company Average",
            incident_count=int(statistics.mean(t.incident_count for t in teams)),
            mttr_minutes=statistics.mean(t.mttr_minutes for t in teams),
            ai_usage_rate=statistics.mean(t.ai_usage_rate for t in teams),
            trend_percent=statistics.mean(t.trend_percent for t in teams),
            p1_count=int(statistics.mean(t.p1_count for t in teams)),
            p2_count=int(statistics.mean(t.p2_count for t in teams)),
            repeat_incident_rate=statistics.mean(t.repeat_incident_rate for t in teams),
        )

    def _generate_insights(
        self,
        teams: list[TeamMetrics],
        avg: TeamMetrics,
    ) -> list[str]:
        """Generate insights from team comparison."""
        insights: list[str] = []

        if not teams:
            return insights

        best_team = min(teams, key=lambda t: t.mttr_minutes)
        worst_team = max(teams, key=lambda t: t.mttr_minutes)

        if best_team.mttr_minutes < avg.mttr_minutes * 0.7:
            insights.append(
                f"{best_team.team} is best performer with {best_team.mttr_minutes:.0f}m MTTR - "
                "share their practices with other teams"
            )

        if worst_team.mttr_minutes > avg.mttr_minutes * 1.5:
            insights.append(
                f"{worst_team.team} has longest MTTR ({worst_team.mttr_minutes:.0f}m) - "
                "schedule training or review runbooks"
            )

        low_ai_teams = [t for t in teams if t.ai_usage_rate < 0.7]
        for team in low_ai_teams:
            insights.append(
                f"{team.team} has low AI usage ({team.ai_usage_rate * 100:.0f}%) - "
                "team may need onboarding"
            )

        high_incident_teams = [t for t in teams if t.incident_count > avg.incident_count * 1.5]
        for team in high_incident_teams:
            insights.append(
                f"{team.team} has {team.incident_count} incidents (highest) - "
                "investigate root causes"
            )

        return insights

    def _generate_recommendations(
        self,
        teams: list[TeamMetrics],
    ) -> list[str]:
        """Generate recommendations."""
        recommendations = []

        for team in teams:
            if team.repeat_incident_rate > 0.2:
                recommendations.append(
                    f"{team.team}: High repeat incident rate ({team.repeat_incident_rate * 100:.0f}%). "
                    "Create Jira tickets for systemic issues."
                )

            if team.p1_count >= 3:
                recommendations.append(
                    f"{team.team}: {team.p1_count} P1 incidents. "
                    "Schedule architecture review."
                )

            if team.trend_percent > 20:
                recommendations.append(
                    f"{team.team}: Incidents trending up {team.trend_percent:.0f}%. "
                    "Review recent changes and add monitoring."
                )

        return recommendations

    def get_service_health_matrix(
        self,
        period_days: int = 30,
    ) -> list[ServiceHealth]:
        """Get service health matrix."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents if i.created_at >= period_start
        ]

        by_service: dict[str, list[TeamIncident]] = {}
        for inc in incidents:
            if inc.service not in by_service:
                by_service[inc.service] = []
            by_service[inc.service].append(inc)

        health_matrix = []

        for service, service_incidents in by_service.items():
            health = self._calculate_service_health(service, service_incidents)
            health_matrix.append(health)

        health_matrix.sort(key=lambda h: h.reliability_score)

        return health_matrix

    def _calculate_service_health(
        self,
        service: str,
        incidents: list[TeamIncident],
    ) -> ServiceHealth:
        """Calculate health for a service."""
        mttr_values = [i.mttr_minutes for i in incidents if i.mttr_minutes > 0]
        avg_mttr = statistics.mean(mttr_values) if mttr_values else 0.0

        ai_assisted = sum(1 for i in incidents if i.ai_assisted)
        ai_rate = ai_assisted / len(incidents) if incidents else 0.0

        p1_count = sum(1 for i in incidents if i.severity == "P1")

        if p1_count >= 3 or avg_mttr > 60:
            status = HealthStatus.CRITICAL
        elif p1_count >= 1 or avg_mttr > 30:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        deps = self._service_dependencies.get(service, [])
        if len(deps) >= 5:
            blast_radius = "high"
        elif len(deps) >= 2:
            blast_radius = "medium"
        else:
            blast_radius = "low"

        reliability = 1.0 - (len(incidents) * 0.02) - (p1_count * 0.1)
        reliability = max(0.0, min(1.0, reliability))

        issues = self._identify_service_issues(service, incidents, avg_mttr, ai_rate)

        return ServiceHealth(
            service=service,
            status=status,
            incident_count=len(incidents),
            mttr_minutes=avg_mttr,
            ai_usage_rate=ai_rate,
            blast_radius=blast_radius,
            reliability_score=reliability,
            issues=issues,
        )

    def _identify_service_issues(
        self,
        service: str,
        incidents: list[TeamIncident],
        mttr: float,
        ai_rate: float,
    ) -> list[str]:
        """Identify issues for a service."""
        issues = []

        if mttr > 60:
            issues.append(f"Longest MTTR in org ({mttr:.0f}m vs 35m avg)")

        if ai_rate < 0.65:
            issues.append(f"Low AI usage ({ai_rate * 100:.0f}%) - team may need training")

        deps = self._service_dependencies.get(service, [])
        if len(deps) >= 5:
            issues.append(f"High blast radius (affects {len(deps)} downstream services)")

        p1_count = sum(1 for i in incidents if i.severity == "P1")
        if p1_count >= 2:
            issues.append(f"Recurring P1 incidents ({p1_count} in period)")

        return issues

    def get_oncall_burden_analysis(
        self,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Analyze on-call burden across users."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        pages = [p for p in self._pages if p.timestamp >= period_start]

        by_user: dict[str, list[OnCallPage]] = {}
        for page in pages:
            if page.user_id not in by_user:
                by_user[page.user_id] = []
            by_user[page.user_id].append(page)

        burdens = []
        for user_id, user_pages in by_user.items():
            burden = self._calculate_user_burden(user_id, user_pages)
            burdens.append(burden)

        burdens.sort(key=lambda b: b.burden_score, reverse=True)

        pages_by_hour = self._calculate_pages_by_hour(pages)

        fairness_analysis = self._analyze_fairness(burdens)

        return {
            "users": [b.to_dict() for b in burdens],
            "pages_by_hour": pages_by_hour,
            "fairness": fairness_analysis,
            "recommendations": self._generate_oncall_recommendations(burdens),
        }

    def _calculate_user_burden(
        self,
        user_id: str,
        pages: list[OnCallPage],
    ) -> OnCallBurden:
        """Calculate burden score for a user."""
        team = pages[0].team if pages else ""

        p1_pages = sum(1 for p in pages if p.severity == "P1")
        night_pages = sum(1 for p in pages if p.is_night)
        weekend_pages = sum(1 for p in pages if p.is_weekend)

        burden_score = (
            len(pages) +
            p1_pages * 2 +
            night_pages * 1.5 +
            weekend_pages * 1.5
        )

        return OnCallBurden(
            user_id=user_id,
            team=team,
            total_pages=len(pages),
            p1_pages=p1_pages,
            night_pages=night_pages,
            weekend_pages=weekend_pages,
            burden_score=burden_score,
        )

    def _calculate_pages_by_hour(
        self,
        pages: list[OnCallPage],
    ) -> list[dict[str, Any]]:
        """Calculate page distribution by hour."""
        by_hour: dict[int, int] = {h: 0 for h in range(24)}

        for page in pages:
            by_hour[page.timestamp.hour] += 1

        return [
            {"hour": h, "count": c, "is_off_hours": h < 9 or h > 17}
            for h, c in sorted(by_hour.items())
        ]

    def _analyze_fairness(
        self,
        burdens: list[OnCallBurden],
    ) -> dict[str, Any]:
        """Analyze fairness of on-call distribution."""
        if len(burdens) < 2:
            return {"balanced": True, "ratio": 1.0, "alert": None}

        scores = [b.burden_score for b in burdens]
        max_score = max(scores)
        min_score = min(scores) if min(scores) > 0 else 1

        ratio = max_score / min_score

        alert = None
        if ratio > 2.0:
            highest = burdens[0]
            lowest = burdens[-1]
            alert = (
                f"{highest.user_id} has {ratio:.1f}x higher burden than {lowest.user_id}"
            )

        return {
            "balanced": ratio < 1.5,
            "ratio": round(ratio, 1),
            "alert": alert,
        }

    def _generate_oncall_recommendations(
        self,
        burdens: list[OnCallBurden],
    ) -> list[str]:
        """Generate on-call recommendations."""
        recommendations = []

        for burden in burdens:
            if burden.night_pages > burden.total_pages * 0.4:
                recommendations.append(
                    f"{burden.user_id}: High night page rate. "
                    "Consider adding non-urgent alert deferral."
                )

            if burden.p1_pages > 5:
                recommendations.append(
                    f"{burden.user_id}: {burden.p1_pages} P1 pages. "
                    "Review incident prevention for their services."
                )

        if len(burdens) >= 2:
            ratio = burdens[0].burden_score / max(burdens[-1].burden_score, 1)
            if ratio > 2.5:
                recommendations.append(
                    "Significant burden imbalance detected. "
                    "Consider rebalancing on-call rotations."
                )

        return recommendations


_analytics: TeamAnalytics | None = None


def get_team_analytics() -> TeamAnalytics:
    """Get the default team analytics."""
    global _analytics
    if _analytics is None:
        _analytics = TeamAnalytics()
    return _analytics
