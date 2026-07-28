"""Seasonal pattern detection for predictive incidents.

Detects and predicts recurring patterns based on time-based factors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.predictive.seasonal")


class PatternType(Enum):
    """Type of seasonal pattern."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    EVENT_BASED = "event_based"
    CUSTOM = "custom"


class PatternSeverity(Enum):
    """Severity of the pattern impact."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class HistoricalIncident:
    """Historical incident data for pattern analysis."""

    incident_id: str
    occurred_at: datetime
    incident_type: str
    affected_services: list[str]
    duration_minutes: int
    severity: str
    root_cause: str | None = None


@dataclass
class PatternOccurrence:
    """A predicted occurrence of a seasonal pattern."""

    occurrence_id: str
    pattern_id: str
    expected_start: datetime
    expected_end: datetime
    confidence: float
    prepared: bool = False
    preparation_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "pattern_id": self.pattern_id,
            "expected_start": self.expected_start.isoformat(),
            "expected_end": self.expected_end.isoformat(),
            "confidence": round(self.confidence, 2),
            "prepared": self.prepared,
            "preparation_actions": self.preparation_actions,
        }


@dataclass
class SeasonalPattern:
    """A detected seasonal pattern."""

    pattern_id: str
    name: str
    description: str
    pattern_type: PatternType
    severity: PatternSeverity
    traffic_multiplier: float
    historical_incident_count: int
    historical_incidents: list[str]
    affected_services: list[str]
    recommendations: list[str]
    next_occurrence: datetime | None
    recurrence_schedule: str
    detected_at: datetime
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "description": self.description,
            "pattern_type": self.pattern_type.value,
            "severity": self.severity.value,
            "traffic_multiplier": self.traffic_multiplier,
            "historical_incident_count": self.historical_incident_count,
            "historical_incidents": self.historical_incidents,
            "affected_services": self.affected_services,
            "recommendations": self.recommendations,
            "next_occurrence": (
                self.next_occurrence.isoformat() if self.next_occurrence else None
            ),
            "recurrence_schedule": self.recurrence_schedule,
            "detected_at": self.detected_at.isoformat(),
            "confidence": round(self.confidence, 2),
        }


@dataclass
class HighRiskPeriod:
    """A high-risk period identified from patterns."""

    period_id: str
    name: str
    start: datetime
    end: datetime
    risk_score: float
    patterns: list[str]
    historical_revenue_impact: float
    recommendations: list[str]
    runbook_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_id": self.period_id,
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "risk_score": round(self.risk_score, 2),
            "patterns": self.patterns,
            "historical_revenue_impact": self.historical_revenue_impact,
            "recommendations": self.recommendations,
            "runbook_id": self.runbook_id,
        }


class SeasonalDetector:
    """Detects and predicts seasonal patterns."""

    def __init__(self) -> None:
        self._patterns: dict[str, SeasonalPattern] = {}
        self._incidents: list[HistoricalIncident] = []
        self._occurrences: dict[str, PatternOccurrence] = {}
        self._high_risk_periods: dict[str, HighRiskPeriod] = {}
        self._pattern_counter = 0
        self._occurrence_counter = 0
        self._period_counter = 0
        self._setup_known_patterns()

    def _setup_known_patterns(self) -> None:
        """Set up commonly known patterns."""
        now = datetime.now(timezone.utc)

        patterns = [
            SeasonalPattern(
                pattern_id="pattern_monday_surge",
                name="Monday Morning Traffic Surge",
                description="Traffic increases 180% compared to weekend baseline",
                pattern_type=PatternType.WEEKLY,
                severity=PatternSeverity.MEDIUM,
                traffic_multiplier=1.8,
                historical_incident_count=3,
                historical_incidents=["INC-4521", "INC-4234", "INC-3987"],
                affected_services=["api-gateway", "user-service", "cache"],
                recommendations=[
                    "Schedule cache warmup job at 7:30 AM each timezone",
                    "Scale API pods to 150% before 8 AM",
                    "Avoid deploys Monday 8-10 AM",
                ],
                next_occurrence=self._next_monday(now),
                recurrence_schedule="Every Monday 8:00-10:00 AM",
                detected_at=now - timedelta(days=60),
                confidence=0.92,
            ),
            SeasonalPattern(
                pattern_id="pattern_eom_billing",
                name="End-of-Month Billing Spike",
                description="Billing processing increases 340% in last 3 days of month",
                pattern_type=PatternType.MONTHLY,
                severity=PatternSeverity.HIGH,
                traffic_multiplier=3.4,
                historical_incident_count=4,
                historical_incidents=["INC-5123", "INC-4891", "INC-4567", "INC-4123"],
                affected_services=["billing-service", "payment-service", "invoice-api"],
                recommendations=[
                    "Scale billing-service to 8 pods",
                    "Increase database connection pool to 100",
                    "Enable billing-specific rate limiting",
                    "Freeze non-critical billing deploys",
                ],
                next_occurrence=self._end_of_month(now),
                recurrence_schedule="Last 3 days of each month",
                detected_at=now - timedelta(days=90),
                confidence=0.95,
            ),
            SeasonalPattern(
                pattern_id="pattern_black_friday",
                name="Black Friday Traffic Event",
                description="Annual peak traffic event with 12x normal load",
                pattern_type=PatternType.ANNUAL,
                severity=PatternSeverity.CRITICAL,
                traffic_multiplier=12.0,
                historical_incident_count=3,
                historical_incidents=["INC-3456", "INC-2345", "INC-1234"],
                affected_services=[
                    "checkout-api",
                    "payment-service",
                    "inventory-service",
                    "cart-service",
                ],
                recommendations=[
                    "Scale all services to 5x current baseline",
                    "Pre-warm all caches 24 hours before",
                    "Double on-call staffing",
                    "Pre-position rollbacks for last 5 deploys",
                    "Enable aggressive rate limiting for non-critical paths",
                    "Freeze all deploys 48 hours before",
                ],
                next_occurrence=self._next_black_friday(now),
                recurrence_schedule="Fourth Friday of November",
                detected_at=now - timedelta(days=365),
                confidence=0.99,
            ),
            SeasonalPattern(
                pattern_id="pattern_quarter_end",
                name="Quarter-End Report Generation",
                description="Quarterly report batch jobs cause 200% CPU spike",
                pattern_type=PatternType.QUARTERLY,
                severity=PatternSeverity.MEDIUM,
                traffic_multiplier=2.0,
                historical_incident_count=2,
                historical_incidents=["INC-4789", "INC-3890"],
                affected_services=["reporting-service", "analytics-db", "export-api"],
                recommendations=[
                    "Schedule batch jobs during off-peak hours",
                    "Increase reporting pod CPU limits",
                    "Enable read replicas for analytics",
                ],
                next_occurrence=self._next_quarter_end(now),
                recurrence_schedule="Last day of each quarter",
                detected_at=now - timedelta(days=180),
                confidence=0.88,
            ),
            SeasonalPattern(
                pattern_id="pattern_friday_deploys",
                name="Friday Deployment Risk",
                description="Friday deploys have 2.3x higher incident rate",
                pattern_type=PatternType.WEEKLY,
                severity=PatternSeverity.HIGH,
                traffic_multiplier=1.0,
                historical_incident_count=8,
                historical_incidents=[
                    "INC-5234",
                    "INC-5123",
                    "INC-4987",
                    "INC-4876",
                ],
                affected_services=["all"],
                recommendations=[
                    "No deploys after Thursday 2 PM",
                    "Mandatory senior review for Friday deploys",
                    "Require 2 hour bake time before weekend",
                ],
                next_occurrence=self._next_friday(now),
                recurrence_schedule="Every Friday",
                detected_at=now - timedelta(days=120),
                confidence=0.94,
            ),
        ]

        for pattern in patterns:
            self._patterns[pattern.pattern_id] = pattern

    def _next_monday(self, from_date: datetime) -> datetime:
        """Get next Monday."""
        days_ahead = (7 - from_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return from_date.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(
            days=days_ahead
        )

    def _next_friday(self, from_date: datetime) -> datetime:
        """Get next Friday."""
        days_ahead = (4 - from_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return from_date.replace(
            hour=14, minute=0, second=0, microsecond=0
        ) + timedelta(days=days_ahead)

    def _end_of_month(self, from_date: datetime) -> datetime:
        """Get end of current/next month."""
        if from_date.month == 12:
            next_month = from_date.replace(year=from_date.year + 1, month=1, day=1)
        else:
            next_month = from_date.replace(month=from_date.month + 1, day=1)
        return next_month - timedelta(days=3)

    def _next_quarter_end(self, from_date: datetime) -> datetime:
        """Get next quarter end."""
        quarter_ends = [
            (3, 31),
            (6, 30),
            (9, 30),
            (12, 31),
        ]

        for month, day in quarter_ends:
            candidate = from_date.replace(month=month, day=day, hour=0, minute=0)
            if candidate > from_date:
                return candidate

        return from_date.replace(
            year=from_date.year + 1, month=3, day=31, hour=0, minute=0
        )

    def _next_black_friday(self, from_date: datetime) -> datetime:
        """Get next Black Friday (4th Friday of November)."""
        year = from_date.year
        nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)

        first_friday = nov_1 + timedelta(days=(4 - nov_1.weekday()) % 7)
        fourth_friday = first_friday + timedelta(weeks=3)

        if fourth_friday <= from_date:
            year += 1
            nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
            first_friday = nov_1 + timedelta(days=(4 - nov_1.weekday()) % 7)
            fourth_friday = first_friday + timedelta(weeks=3)

        return fourth_friday

    def record_incident(
        self,
        incident_id: str,
        occurred_at: datetime,
        incident_type: str,
        affected_services: list[str],
        duration_minutes: int,
        severity: str,
        root_cause: str | None = None,
    ) -> None:
        """Record a historical incident for pattern analysis."""
        incident = HistoricalIncident(
            incident_id=incident_id,
            occurred_at=occurred_at,
            incident_type=incident_type,
            affected_services=affected_services,
            duration_minutes=duration_minutes,
            severity=severity,
            root_cause=root_cause,
        )
        self._incidents.append(incident)

    def detect_patterns(
        self,
        min_occurrences: int = 3,
        confidence_threshold: float = 0.7,
    ) -> list[SeasonalPattern]:
        """Analyze incidents to detect new patterns."""
        detected = []

        by_hour: dict[int, list[HistoricalIncident]] = {}
        for incident in self._incidents:
            hour = incident.occurred_at.hour
            if hour not in by_hour:
                by_hour[hour] = []
            by_hour[hour].append(incident)

        for hour, incidents in by_hour.items():
            if len(incidents) >= min_occurrences:
                self._pattern_counter += 1
                pattern = SeasonalPattern(
                    pattern_id=f"pattern_{self._pattern_counter}",
                    name=f"Hour {hour} Incident Pattern",
                    description=f"Incidents tend to occur around {hour}:00",
                    pattern_type=PatternType.DAILY,
                    severity=PatternSeverity.MEDIUM,
                    traffic_multiplier=1.0,
                    historical_incident_count=len(incidents),
                    historical_incidents=[i.incident_id for i in incidents],
                    affected_services=list(
                        set(s for i in incidents for s in i.affected_services)
                    ),
                    recommendations=[
                        f"Increase monitoring around {hour}:00",
                        "Consider shifting batch jobs to off-peak hours",
                    ],
                    next_occurrence=self._next_hour_occurrence(hour),
                    recurrence_schedule=f"Daily around {hour}:00",
                    detected_at=datetime.now(timezone.utc),
                    confidence=min(1.0, len(incidents) / 10),
                )

                if pattern.confidence >= confidence_threshold:
                    self._patterns[pattern.pattern_id] = pattern
                    detected.append(pattern)

        return detected

    def _next_hour_occurrence(self, hour: int) -> datetime:
        """Get next occurrence of a specific hour."""
        now = datetime.now(timezone.utc)
        next_occ = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_occ <= now:
            next_occ += timedelta(days=1)
        return next_occ

    def get_pattern(self, pattern_id: str) -> SeasonalPattern | None:
        """Get a pattern by ID."""
        return self._patterns.get(pattern_id)

    def get_all_patterns(self) -> list[SeasonalPattern]:
        """Get all detected patterns."""
        return list(self._patterns.values())

    def get_upcoming_occurrences(
        self,
        days_ahead: int = 14,
    ) -> list[PatternOccurrence]:
        """Get upcoming pattern occurrences."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)
        occurrences = []

        for pattern in self._patterns.values():
            if pattern.next_occurrence and pattern.next_occurrence <= cutoff:
                self._occurrence_counter += 1
                occ = PatternOccurrence(
                    occurrence_id=f"occ_{self._occurrence_counter}",
                    pattern_id=pattern.pattern_id,
                    expected_start=pattern.next_occurrence,
                    expected_end=pattern.next_occurrence + timedelta(hours=2),
                    confidence=pattern.confidence,
                    preparation_actions=pattern.recommendations,
                )
                occurrences.append(occ)
                self._occurrences[occ.occurrence_id] = occ

        return sorted(occurrences, key=lambda o: o.expected_start)

    def mark_occurrence_prepared(
        self,
        occurrence_id: str,
        actions_taken: list[str] | None = None,
    ) -> bool:
        """Mark an occurrence as prepared for."""
        occ = self._occurrences.get(occurrence_id)
        if occ:
            occ.prepared = True
            if actions_taken:
                occ.preparation_actions = actions_taken
            return True
        return False

    def identify_high_risk_periods(
        self,
        days_ahead: int = 90,
    ) -> list[HighRiskPeriod]:
        """Identify upcoming high-risk periods."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)

        high_severity = [
            p
            for p in self._patterns.values()
            if p.severity in (PatternSeverity.HIGH, PatternSeverity.CRITICAL)
            and p.next_occurrence
            and p.next_occurrence <= cutoff
        ]

        periods = []
        for pattern in high_severity:
            if not pattern.next_occurrence:
                continue

            self._period_counter += 1
            period = HighRiskPeriod(
                period_id=f"period_{self._period_counter}",
                name=pattern.name,
                start=pattern.next_occurrence,
                end=pattern.next_occurrence + timedelta(hours=4),
                risk_score=pattern.traffic_multiplier * 10,
                patterns=[pattern.pattern_id],
                historical_revenue_impact=2400000 * (pattern.traffic_multiplier - 1),
                recommendations=pattern.recommendations,
            )
            periods.append(period)
            self._high_risk_periods[period.period_id] = period

        return sorted(periods, key=lambda p: p.risk_score, reverse=True)

    def get_patterns_for_date(self, date: datetime) -> list[SeasonalPattern]:
        """Get patterns that apply to a specific date."""
        matching = []

        for pattern in self._patterns.values():
            if pattern.pattern_type == PatternType.WEEKLY:
                if pattern.pattern_id == "pattern_monday_surge" and date.weekday() == 0:
                    matching.append(pattern)
                elif (
                    pattern.pattern_id == "pattern_friday_deploys"
                    and date.weekday() == 4
                ):
                    matching.append(pattern)

            elif pattern.pattern_type == PatternType.MONTHLY:
                days_until_eom = (
                    self._end_of_month(date) - date.replace(hour=0, minute=0)
                ).days
                if days_until_eom <= 3:
                    matching.append(pattern)

        return matching

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the seasonal patterns dashboard."""
        patterns = self.get_all_patterns()
        upcoming = self.get_upcoming_occurrences(days_ahead=14)
        high_risk = self.identify_high_risk_periods(days_ahead=30)

        by_type: dict[str, int] = {}
        for p in patterns:
            t = p.pattern_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total_patterns": len(patterns),
            "by_type": by_type,
            "upcoming_occurrences": [o.to_dict() for o in upcoming[:5]],
            "high_risk_periods": [p.to_dict() for p in high_risk[:3]],
            "patterns": [p.to_dict() for p in patterns],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_detector: SeasonalDetector | None = None


def get_seasonal_detector() -> SeasonalDetector:
    """Get the default seasonal detector."""
    global _detector
    if _detector is None:
        _detector = SeasonalDetector()
    return _detector
