"""Fatigue detection for on-call engineers.

Monitors engineer workload and alerting patterns to:
- Detect fatigue before it impacts response quality
- Alert when engineers are overloaded
- Recommend coverage swaps
- Track sleep disruption patterns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.oncall.fatigue")


class FatigueLevel(Enum):
    """Fatigue severity levels."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(Enum):
    """Types of fatigue alerts."""

    EXCESSIVE_PAGES = "excessive_pages"
    SLEEP_DISRUPTION = "sleep_disruption"
    LONG_SHIFT = "long_shift"
    CONSECUTIVE_DAYS = "consecutive_days"
    HIGH_MTTR = "high_mttr"
    WEEKEND_OVERLOAD = "weekend_overload"


@dataclass
class PageEvent:
    """A paging event for an engineer."""

    event_id: str
    engineer_id: str
    timestamp: datetime
    incident_id: str
    severity: str
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    escalated: bool = False

    @property
    def response_time_seconds(self) -> float | None:
        if self.acknowledged_at:
            return (self.acknowledged_at - self.timestamp).total_seconds()
        return None

    @property
    def resolution_time_seconds(self) -> float | None:
        if self.resolved_at:
            return (self.resolved_at - self.timestamp).total_seconds()
        return None

    @property
    def was_sleep_hours(self) -> bool:
        hour = self.timestamp.hour
        return hour >= 22 or hour < 7


@dataclass
class FatigueScore:
    """Fatigue assessment for an engineer."""

    engineer_id: str
    engineer_name: str
    score: float
    level: FatigueLevel
    factors: dict[str, float]
    metrics: dict[str, Any]
    recommendations: list[str]
    assessed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_id": self.engineer_id,
            "engineer_name": self.engineer_name,
            "score": round(self.score, 2),
            "level": self.level.value,
            "factors": {k: round(v, 2) for k, v in self.factors.items()},
            "metrics": self.metrics,
            "recommendations": self.recommendations,
            "assessed_at": self.assessed_at.isoformat(),
        }


@dataclass
class FatigueAlert:
    """Alert for engineer fatigue."""

    alert_id: str
    engineer_id: str
    engineer_name: str
    alert_type: AlertType
    severity: FatigueLevel
    title: str
    description: str
    created_at: datetime
    metrics: dict[str, Any]
    recommendations: list[str]
    acknowledged: bool = False
    acknowledged_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "engineer_id": self.engineer_id,
            "engineer_name": self.engineer_name,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "metrics": self.metrics,
            "recommendations": self.recommendations,
            "acknowledged": self.acknowledged,
        }


@dataclass
class EngineerStats:
    """Statistics for an engineer's on-call activity."""

    engineer_id: str
    name: str
    #: Empty when unknown. Stats created from a bare engineer id used to invent
    #: `{engineer_id}@company.com` — a fabricated corporate address served to
    #: callers as if it were a directory lookup.
    email: str = ""
    pages_this_week: int = 0
    pages_this_month: int = 0
    sleep_disruptions: int = 0
    hours_on_call_this_week: float = 0
    hours_on_call_this_month: float = 0
    avg_response_time_seconds: float = 0
    avg_resolution_time_seconds: float = 0
    consecutive_on_call_days: int = 0
    weekend_shifts_this_month: int = 0
    last_page_at: datetime | None = None
    typical_sleep_hours: tuple[int, int] = (22, 7)


class FatigueDetector:
    """Detects and monitors engineer fatigue."""

    def __init__(
        self,
        max_pages_per_week: int = 15,
        max_sleep_disruptions_per_week: int = 3,
        max_consecutive_days: int = 5,
        max_hours_per_week: int = 40,
        sleep_start_hour: int = 22,
        sleep_end_hour: int = 7,
    ):
        self.max_pages_per_week = max_pages_per_week
        self.max_sleep_disruptions_per_week = max_sleep_disruptions_per_week
        self.max_consecutive_days = max_consecutive_days
        self.max_hours_per_week = max_hours_per_week
        self.sleep_start_hour = sleep_start_hour
        self.sleep_end_hour = sleep_end_hour

        self._engineer_stats: dict[str, EngineerStats] = {}
        self._page_events: list[PageEvent] = []
        self._alerts: dict[str, FatigueAlert] = {}
        self._alert_counter = 0

    def record_page(self, event: PageEvent) -> None:
        """Record a paging event."""
        self._page_events.append(event)
        self._update_stats(event)

        score = self.assess_fatigue(event.engineer_id)
        if score.level in (FatigueLevel.HIGH, FatigueLevel.CRITICAL):
            self._create_alert(event.engineer_id, score)

    def _update_stats(self, event: PageEvent) -> None:
        """Update engineer stats from a page event."""
        stats = self._engineer_stats.get(event.engineer_id)
        if not stats:
            stats = EngineerStats(
                engineer_id=event.engineer_id,
                name=f"Engineer {event.engineer_id}",
            )
            self._engineer_stats[event.engineer_id] = stats

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        recent_pages = [
            p for p in self._page_events
            if p.engineer_id == event.engineer_id and p.timestamp > week_ago
        ]
        stats.pages_this_week = len(recent_pages)

        monthly_pages = [
            p for p in self._page_events
            if p.engineer_id == event.engineer_id and p.timestamp > month_ago
        ]
        stats.pages_this_month = len(monthly_pages)

        stats.sleep_disruptions = len([p for p in recent_pages if p.was_sleep_hours])
        stats.last_page_at = event.timestamp

        response_times = [
            p.response_time_seconds for p in recent_pages
            if p.response_time_seconds is not None
        ]
        if response_times:
            stats.avg_response_time_seconds = sum(response_times) / len(response_times)

        resolution_times = [
            p.resolution_time_seconds for p in recent_pages
            if p.resolution_time_seconds is not None
        ]
        if resolution_times:
            stats.avg_resolution_time_seconds = sum(resolution_times) / len(resolution_times)

    def assess_fatigue(self, engineer_id: str) -> FatigueScore:
        """Assess fatigue level for an engineer."""
        stats = self._engineer_stats.get(engineer_id)
        if not stats:
            stats = EngineerStats(
                engineer_id=engineer_id,
                name=f"Engineer {engineer_id}",
            )

        now = datetime.now(timezone.utc)
        factors: dict[str, float] = {}

        page_ratio = stats.pages_this_week / self.max_pages_per_week
        factors["pages"] = min(page_ratio, 1.0)

        sleep_ratio = stats.sleep_disruptions / self.max_sleep_disruptions_per_week
        factors["sleep_disruption"] = min(sleep_ratio * 1.5, 1.0)

        hours_ratio = stats.hours_on_call_this_week / self.max_hours_per_week
        factors["hours"] = min(hours_ratio, 1.0)

        consecutive_ratio = stats.consecutive_on_call_days / self.max_consecutive_days
        factors["consecutive_days"] = min(consecutive_ratio, 1.0)

        weekend_factor = stats.weekend_shifts_this_month / 4.0
        factors["weekend_shifts"] = min(weekend_factor, 1.0)

        if stats.avg_response_time_seconds > 300:
            factors["response_degradation"] = min(
                (stats.avg_response_time_seconds - 300) / 600, 1.0
            )
        else:
            factors["response_degradation"] = 0.0

        weights = {
            "pages": 2.0,
            "sleep_disruption": 3.0,
            "hours": 1.5,
            "consecutive_days": 2.0,
            "weekend_shifts": 1.0,
            "response_degradation": 1.5,
        }

        total_score = sum(factors[k] * weights.get(k, 1.0) for k in factors)
        max_score = sum(weights.values())
        normalized_score = (total_score / max_score) * 10

        if normalized_score < 2.5:
            level = FatigueLevel.LOW
        elif normalized_score < 5.0:
            level = FatigueLevel.MODERATE
        elif normalized_score < 7.5:
            level = FatigueLevel.HIGH
        else:
            level = FatigueLevel.CRITICAL

        recommendations = self._generate_recommendations(stats, factors, level)

        metrics = {
            "pages_this_week": stats.pages_this_week,
            "sleep_disruptions": stats.sleep_disruptions,
            "hours_on_call": stats.hours_on_call_this_week,
            "consecutive_days": stats.consecutive_on_call_days,
            "avg_response_time_seconds": round(stats.avg_response_time_seconds, 1),
            "weekend_shifts": stats.weekend_shifts_this_month,
        }

        return FatigueScore(
            engineer_id=engineer_id,
            engineer_name=stats.name,
            score=normalized_score,
            level=level,
            factors=factors,
            metrics=metrics,
            recommendations=recommendations,
            assessed_at=now,
        )

    def _generate_recommendations(
        self,
        stats: EngineerStats,
        factors: dict[str, float],
        level: FatigueLevel,
    ) -> list[str]:
        """Generate recommendations based on fatigue assessment."""
        recommendations = []

        if factors.get("pages", 0) > 0.7:
            recommendations.append(
                f"High page volume ({stats.pages_this_week} this week). "
                "Consider finding coverage for tonight."
            )

        if factors.get("sleep_disruption", 0) > 0.5:
            recommendations.append(
                f"{stats.sleep_disruptions} sleep disruptions this week. "
                "Schedule rest day tomorrow."
            )

        if factors.get("consecutive_days", 0) > 0.6:
            recommendations.append(
                f"{stats.consecutive_on_call_days} consecutive on-call days. "
                "Swap with teammate for next shift."
            )

        if factors.get("response_degradation", 0) > 0.3:
            recommendations.append(
                "Response time degrading. Take a break before next incident."
            )

        if level == FatigueLevel.CRITICAL:
            recommendations.insert(0, "CRITICAL: Immediate coverage swap recommended")

        if not recommendations:
            recommendations.append("Fatigue levels normal. Continue monitoring.")

        return recommendations

    def _create_alert(self, engineer_id: str, score: FatigueScore) -> FatigueAlert:
        """Create a fatigue alert."""
        self._alert_counter += 1
        alert_id = f"fatigue_{self._alert_counter}"

        alert_type = AlertType.EXCESSIVE_PAGES
        if score.factors.get("sleep_disruption", 0) > 0.6:
            alert_type = AlertType.SLEEP_DISRUPTION
        elif score.factors.get("consecutive_days", 0) > 0.7:
            alert_type = AlertType.CONSECUTIVE_DAYS

        alert = FatigueAlert(
            alert_id=alert_id,
            engineer_id=engineer_id,
            engineer_name=score.engineer_name,
            alert_type=alert_type,
            severity=score.level,
            title=f"Fatigue Alert: {score.engineer_name}",
            description=f"Fatigue score {score.score:.1f}/10 - {score.level.value}",
            created_at=datetime.now(timezone.utc),
            metrics=score.metrics,
            recommendations=score.recommendations,
        )

        self._alerts[alert_id] = alert

        log.warning(
            "fatigue alert created",
            extra={
                "alert_id": alert_id,
                "engineer_id": engineer_id,
                "score": score.score,
                "level": score.level.value,
            },
        )

        return alert

    def get_team_fatigue(self, team_ids: list[str]) -> list[FatigueScore]:
        """Get fatigue scores for a team."""
        scores = []
        for eng_id in team_ids:
            score = self.assess_fatigue(eng_id)
            scores.append(score)
        return sorted(scores, key=lambda s: s.score, reverse=True)

    def get_least_fatigued(self, team_ids: list[str]) -> str | None:
        """Get the least fatigued engineer from a team."""
        scores = self.get_team_fatigue(team_ids)
        if scores:
            return scores[-1].engineer_id
        return None

    def get_alerts(
        self,
        engineer_id: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[FatigueAlert]:
        """Get fatigue alerts."""
        alerts = list(self._alerts.values())

        if engineer_id:
            alerts = [a for a in alerts if a.engineer_id == engineer_id]

        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        return sorted(alerts, key=lambda a: a.created_at, reverse=True)

    def acknowledge_alert(self, alert_id: str, by: str) -> bool:
        """Acknowledge a fatigue alert."""
        alert = self._alerts.get(alert_id)
        if alert:
            alert.acknowledged = True
            alert.acknowledged_by = by
            return True
        return False

    def set_on_call_hours(
        self,
        engineer_id: str,
        hours_this_week: float,
        consecutive_days: int = 0,
    ) -> None:
        """Set on-call hours for an engineer."""
        stats = self._engineer_stats.get(engineer_id)
        if not stats:
            stats = EngineerStats(
                engineer_id=engineer_id,
                name=f"Engineer {engineer_id}",
            )
            self._engineer_stats[engineer_id] = stats

        stats.hours_on_call_this_week = hours_this_week
        stats.consecutive_on_call_days = consecutive_days

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get fatigue dashboard data."""
        all_scores = [
            self.assess_fatigue(eng_id)
            for eng_id in self._engineer_stats.keys()
        ]

        by_level = {level.value: 0 for level in FatigueLevel}
        for score in all_scores:
            by_level[score.level.value] += 1

        open_alerts = self.get_alerts(acknowledged=False)

        return {
            "total_engineers": len(self._engineer_stats),
            "by_level": by_level,
            "critical_count": by_level.get("critical", 0),
            "high_count": by_level.get("high", 0),
            "open_alerts": len(open_alerts),
            "top_fatigued": [s.to_dict() for s in all_scores[:5]],
            "recent_alerts": [a.to_dict() for a in open_alerts[:5]],
        }


_detector: FatigueDetector | None = None


def get_fatigue_detector() -> FatigueDetector:
    """Get the default fatigue detector."""
    global _detector
    if _detector is None:
        _detector = FatigueDetector()
    return _detector
