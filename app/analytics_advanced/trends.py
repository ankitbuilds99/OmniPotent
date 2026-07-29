"""Trend Analysis and Forecasting.

Time-series analysis, pattern detection, and incident forecasting.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.analytics_advanced.trends")


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    STABLE = "stable"


class PatternType(str, Enum):
    WEEKLY_CYCLE = "weekly_cycle"
    RELEASE_CORRELATED = "release_correlated"
    SEASONAL = "seasonal"
    TRAFFIC_CORRELATED = "traffic_correlated"
    DEPLOY_SPIKE = "deploy_spike"
    WEEKEND_DIP = "weekend_dip"
    MONDAY_SPIKE = "monday_spike"


@dataclass
class TimeSeriesPoint:
    """A point in a time series."""

    timestamp: datetime
    value: float
    label: str | None = None


@dataclass
class TrendAnalysis:
    """Trend analysis result."""

    metric_name: str
    direction: TrendDirection
    change_percent: float
    slope: float
    confidence: float
    period_days: int
    data_points: list[TimeSeriesPoint]
    is_significant: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "direction": self.direction.value,
            "change_percent": round(self.change_percent, 1),
            "slope": round(self.slope, 4),
            "confidence": round(self.confidence, 2),
            "period_days": self.period_days,
            "is_significant": self.is_significant,
            "data": [
                {"timestamp": p.timestamp.isoformat(), "value": round(p.value, 2)}
                for p in self.data_points
            ],
        }


@dataclass
class Forecast:
    """Forecast result."""

    metric_name: str
    forecast_horizon_days: int
    predictions: list[TimeSeriesPoint]
    confidence_interval: tuple[float, float]
    trend_assumption: TrendDirection
    expected_incidents: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "horizon_days": self.forecast_horizon_days,
            "predictions": [
                {"date": p.timestamp.strftime("%Y-%m-%d"), "value": round(p.value, 1)}
                for p in self.predictions
            ],
            "confidence_interval": {
                "lower": round(self.confidence_interval[0], 1),
                "upper": round(self.confidence_interval[1], 1),
            },
            "trend": self.trend_assumption.value,
            "expected_incidents": self.expected_incidents,
        }


@dataclass
class IncidentPattern:
    """Detected incident pattern."""

    pattern_type: PatternType
    description: str
    confidence: float
    evidence: list[str]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.pattern_type.value,
            "description": self.description,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
        }


@dataclass
class IncidentRecord:
    """Incident record for trend analysis."""

    incident_id: str
    service: str
    severity: str
    created_at: datetime
    resolved_at: datetime | None = None
    mttr_minutes: float = 0.0
    is_deploy_related: bool = False
    traffic_level: str = "normal"


class TrendAnalyzer:
    """Analyzes trends and patterns in incident data."""

    def __init__(self) -> None:
        self._incidents: list[IncidentRecord] = []
        self._deploy_timestamps: list[datetime] = []

    def record_incident(self, incident: IncidentRecord) -> None:
        """Record an incident."""
        self._incidents.append(incident)

        if len(self._incidents) > 50000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._incidents = [i for i in self._incidents if i.created_at >= cutoff]

    def record_deploy(self, timestamp: datetime) -> None:
        """Record a deployment timestamp."""
        self._deploy_timestamps.append(timestamp)

        if len(self._deploy_timestamps) > 10000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._deploy_timestamps = [
                t for t in self._deploy_timestamps if t >= cutoff
            ]

    def analyze_incident_trend(
        self,
        period_days: int = 30,
        service: str | None = None,
    ) -> TrendAnalysis:
        """Analyze incident count trend."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents
            if i.created_at >= period_start
            and (service is None or i.service == service)
        ]

        daily_counts = self._aggregate_daily(incidents, period_days)

        direction, change, slope, confidence = self._calculate_trend(daily_counts)

        is_significant = abs(change) > 10 and confidence > 0.6

        return TrendAnalysis(
            metric_name="incident_count",
            direction=direction,
            change_percent=change,
            slope=slope,
            confidence=confidence,
            period_days=period_days,
            data_points=daily_counts,
            is_significant=is_significant,
        )

    def analyze_mttr_trend(
        self,
        period_days: int = 30,
        service: str | None = None,
    ) -> TrendAnalysis:
        """Analyze MTTR trend."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents
            if i.created_at >= period_start
            and i.mttr_minutes > 0
            and (service is None or i.service == service)
        ]

        daily_mttr = self._aggregate_daily_mttr(incidents, period_days)

        direction, change, slope, confidence = self._calculate_trend(daily_mttr)

        is_significant = abs(change) > 15 and confidence > 0.5

        return TrendAnalysis(
            metric_name="mttr_minutes",
            direction=direction,
            change_percent=change,
            slope=slope,
            confidence=confidence,
            period_days=period_days,
            data_points=daily_mttr,
            is_significant=is_significant,
        )

    def _aggregate_daily(
        self,
        incidents: list[IncidentRecord],
        period_days: int,
    ) -> list[TimeSeriesPoint]:
        """Aggregate incidents by day."""
        now = datetime.now(timezone.utc)
        points = []

        for i in range(period_days - 1, -1, -1):
            day_start = (now - timedelta(days=i + 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = (now - timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            count = sum(
                1 for inc in incidents
                if day_start <= inc.created_at < day_end
            )

            points.append(TimeSeriesPoint(
                timestamp=day_start,
                value=float(count),
                label=day_start.strftime("%Y-%m-%d"),
            ))

        return points

    def _aggregate_daily_mttr(
        self,
        incidents: list[IncidentRecord],
        period_days: int,
    ) -> list[TimeSeriesPoint]:
        """Aggregate MTTR by day."""
        now = datetime.now(timezone.utc)
        points = []

        for i in range(period_days - 1, -1, -1):
            day_start = (now - timedelta(days=i + 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = (now - timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            day_incidents = [
                inc for inc in incidents
                if day_start <= inc.created_at < day_end and inc.mttr_minutes > 0
            ]

            avg_mttr = (
                statistics.mean(inc.mttr_minutes for inc in day_incidents)
                if day_incidents else 0.0
            )

            points.append(TimeSeriesPoint(
                timestamp=day_start,
                value=avg_mttr,
                label=day_start.strftime("%Y-%m-%d"),
            ))

        return points

    def _calculate_trend(
        self,
        points: list[TimeSeriesPoint],
    ) -> tuple[TrendDirection, float, float, float]:
        """Calculate trend direction, change, slope, and confidence."""
        if len(points) < 3:
            return TrendDirection.STABLE, 0.0, 0.0, 0.0

        values = [p.value for p in points]

        x: list[int | float] = list(range(len(values)))
        slope, intercept = self._linear_regression(x, values)

        first_half = values[:len(values) // 2]
        second_half = values[len(values) // 2:]

        avg_first = statistics.mean(first_half) if first_half else 0
        avg_second = statistics.mean(second_half) if second_half else 0

        if avg_first > 0:
            change = ((avg_second - avg_first) / avg_first) * 100
        else:
            change = 0.0

        if slope > 0.1:
            direction = TrendDirection.UP
        elif slope < -0.1:
            direction = TrendDirection.DOWN
        else:
            direction = TrendDirection.STABLE

        r_squared = self._calculate_r_squared(x, values, slope, intercept)
        confidence = max(0.0, min(1.0, r_squared))

        return direction, change, slope, confidence

    def _linear_regression(
        self,
        x: list[int | float],
        y: list[float],
    ) -> tuple[float, float]:
        """Simple linear regression."""
        n = len(x)
        if n == 0:
            return 0.0, 0.0

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0, y_mean

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        return slope, intercept

    def _calculate_r_squared(
        self,
        x: list[int | float],
        y: list[float],
        slope: float,
        intercept: float,
    ) -> float:
        """Calculate R-squared for regression."""
        if not y:
            return 0.0

        y_mean = sum(y) / len(y)

        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))

        if ss_tot == 0:
            return 1.0

        return 1 - (ss_res / ss_tot)

    def forecast_incidents(
        self,
        horizon_days: int = 7,
        service: str | None = None,
    ) -> Forecast:
        """Forecast future incidents."""
        trend = self.analyze_incident_trend(period_days=30, service=service)

        last_week_avg = 0.0
        if trend.data_points:
            last_week = trend.data_points[-7:]
            last_week_avg = statistics.mean(p.value for p in last_week)

        predictions = []
        now = datetime.now(timezone.utc)

        for i in range(1, horizon_days + 1):
            forecast_date = now + timedelta(days=i)

            predicted_value = last_week_avg + (trend.slope * i)
            predicted_value = max(0, predicted_value)

            predictions.append(TimeSeriesPoint(
                timestamp=forecast_date,
                value=predicted_value,
            ))

        predicted_values = [p.value for p in predictions]
        std_dev = statistics.stdev(predicted_values) if len(predicted_values) > 1 else 0
        mean_pred = statistics.mean(predicted_values) if predicted_values else 0

        confidence_interval = (
            max(0, mean_pred - 1.96 * std_dev),
            mean_pred + 1.96 * std_dev,
        )

        expected_incidents = int(sum(p.value for p in predictions))

        return Forecast(
            metric_name="incident_count",
            forecast_horizon_days=horizon_days,
            predictions=predictions,
            confidence_interval=confidence_interval,
            trend_assumption=trend.direction,
            expected_incidents=expected_incidents,
        )

    def detect_patterns(
        self,
        period_days: int = 90,
    ) -> list[IncidentPattern]:
        """Detect patterns in incident data."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents if i.created_at >= period_start
        ]

        if not incidents:
            return []

        patterns = []

        weekly_pattern = self._detect_weekly_pattern(incidents)
        if weekly_pattern:
            patterns.append(weekly_pattern)

        deploy_pattern = self._detect_deploy_correlation(incidents)
        if deploy_pattern:
            patterns.append(deploy_pattern)

        monday_spike = self._detect_monday_spike(incidents)
        if monday_spike:
            patterns.append(monday_spike)

        weekend_dip = self._detect_weekend_dip(incidents)
        if weekend_dip:
            patterns.append(weekend_dip)

        return patterns

    def _detect_weekly_pattern(
        self,
        incidents: list[IncidentRecord],
    ) -> IncidentPattern | None:
        """Detect weekly cyclical patterns."""
        by_weekday: dict[int, int] = {i: 0 for i in range(7)}

        for inc in incidents:
            by_weekday[inc.created_at.weekday()] += 1

        if not any(by_weekday.values()):
            return None

        max_day = max(by_weekday, key=by_weekday.get)  # type: ignore[arg-type]
        min_day = min(by_weekday, key=by_weekday.get)  # type: ignore[arg-type]

        variance_ratio = (
            by_weekday[max_day] / max(by_weekday[min_day], 1)
        )

        if variance_ratio < 1.5:
            return None

        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        return IncidentPattern(
            pattern_type=PatternType.WEEKLY_CYCLE,
            description=(
                f"Incidents peak on {day_names[max_day]}s and dip on {day_names[min_day]}s"
            ),
            confidence=min(0.9, variance_ratio / 3),
            evidence=[
                f"{day_names[max_day]}: {by_weekday[max_day]} incidents",
                f"{day_names[min_day]}: {by_weekday[min_day]} incidents",
                f"Variance ratio: {variance_ratio:.1f}x",
            ],
            recommendation=(
                f"Consider adding monitoring coverage on {day_names[max_day]}s "
                "and reviewing deployment schedules"
            ),
        )

    def _detect_deploy_correlation(
        self,
        incidents: list[IncidentRecord],
    ) -> IncidentPattern | None:
        """Detect correlation between deploys and incidents."""
        deploy_related = sum(1 for i in incidents if i.is_deploy_related)

        if not incidents:
            return None

        correlation_rate = deploy_related / len(incidents)

        if correlation_rate < 0.2:
            return None

        return IncidentPattern(
            pattern_type=PatternType.RELEASE_CORRELATED,
            description=(
                f"{correlation_rate * 100:.0f}% of incidents occur within 2 hours of deployments"
            ),
            confidence=min(0.95, correlation_rate + 0.3),
            evidence=[
                f"Deploy-related incidents: {deploy_related}/{len(incidents)}",
                f"Correlation rate: {correlation_rate * 100:.1f}%",
            ],
            recommendation=(
                "Consider implementing canary deployments, feature flags, "
                "and enhanced post-deploy monitoring"
            ),
        )

    def _detect_monday_spike(
        self,
        incidents: list[IncidentRecord],
    ) -> IncidentPattern | None:
        """Detect Monday morning incident spikes."""
        monday_incidents = [
            i for i in incidents
            if i.created_at.weekday() == 0 and i.created_at.hour < 12
        ]

        other_days = [
            i for i in incidents
            if i.created_at.weekday() != 0 or i.created_at.hour >= 12
        ]

        if not other_days:
            return None

        monday_rate = len(monday_incidents) / max(1, len(incidents))
        expected_rate = 1 / 14

        if monday_rate < expected_rate * 1.5:
            return None

        return IncidentPattern(
            pattern_type=PatternType.MONDAY_SPIKE,
            description="Elevated incident rate on Monday mornings",
            confidence=min(0.85, monday_rate / expected_rate / 3),
            evidence=[
                f"Monday morning incidents: {len(monday_incidents)}",
                f"Rate: {monday_rate * 100:.1f}% (expected ~7%)",
            ],
            recommendation=(
                "Review weekend batch jobs, implement Monday morning health checks, "
                "and consider spreading weekend maintenance"
            ),
        )

    def _detect_weekend_dip(
        self,
        incidents: list[IncidentRecord],
    ) -> IncidentPattern | None:
        """Detect weekend incident reduction."""
        weekend = [i for i in incidents if i.created_at.weekday() >= 5]
        weekday = [i for i in incidents if i.created_at.weekday() < 5]

        if not weekday:
            return None

        weekend_rate = len(weekend) / 2
        weekday_rate = len(weekday) / 5

        if weekday_rate == 0:
            return None

        ratio = weekend_rate / weekday_rate

        if ratio > 0.7:
            return None

        return IncidentPattern(
            pattern_type=PatternType.WEEKEND_DIP,
            description="Incidents drop significantly on weekends",
            confidence=min(0.8, 1 - ratio),
            evidence=[
                f"Weekend avg: {weekend_rate:.1f}/day",
                f"Weekday avg: {weekday_rate:.1f}/day",
                f"Ratio: {ratio:.2f}",
            ],
            recommendation=(
                "This is healthy! Consider scheduling non-critical maintenance "
                "and major releases for weekends when incident load is lower"
            ),
        )

    def get_comparison_report(
        self,
        current_period_days: int = 30,
    ) -> dict[str, Any]:
        """Generate period-over-period comparison."""
        now = datetime.now(timezone.utc)

        current_start = now - timedelta(days=current_period_days)
        previous_start = current_start - timedelta(days=current_period_days)

        current = [i for i in self._incidents if i.created_at >= current_start]
        previous = [
            i for i in self._incidents
            if previous_start <= i.created_at < current_start
        ]

        current_count = len(current)
        previous_count = len(previous)

        count_change = 0.0
        if previous_count > 0:
            count_change = ((current_count - previous_count) / previous_count) * 100

        current_mttr = (
            statistics.mean(i.mttr_minutes for i in current if i.mttr_minutes > 0)
            if any(i.mttr_minutes > 0 for i in current) else 0
        )
        previous_mttr = (
            statistics.mean(i.mttr_minutes for i in previous if i.mttr_minutes > 0)
            if any(i.mttr_minutes > 0 for i in previous) else 0
        )

        mttr_change = 0.0
        if previous_mttr > 0:
            mttr_change = ((current_mttr - previous_mttr) / previous_mttr) * 100

        return {
            "period_days": current_period_days,
            "incident_count": {
                "current": current_count,
                "previous": previous_count,
                "change_percent": round(count_change, 1),
                "trend": "up" if count_change > 5 else ("down" if count_change < -5 else "stable"),
            },
            "mttr_minutes": {
                "current": round(current_mttr, 1),
                "previous": round(previous_mttr, 1),
                "change_percent": round(mttr_change, 1),
                "trend": "up" if mttr_change > 5 else ("down" if mttr_change < -5 else "stable"),
            },
            "insights": self._generate_comparison_insights(
                current_count, previous_count,
                current_mttr, previous_mttr,
            ),
        }

    def _generate_comparison_insights(
        self,
        current_count: int,
        previous_count: int,
        current_mttr: float,
        previous_mttr: float,
    ) -> list[str]:
        """Generate insights from comparison."""
        insights = []

        if current_count < previous_count * 0.8:
            insights.append(
                f"Incident count down {((previous_count - current_count) / previous_count) * 100:.0f}% - "
                "recent changes are improving stability"
            )
        elif current_count > previous_count * 1.2:
            insights.append(
                f"Incident count up {((current_count - previous_count) / previous_count) * 100:.0f}% - "
                "investigate recent deployments or traffic changes"
            )

        if current_mttr < previous_mttr * 0.8:
            insights.append(
                f"MTTR improved by {((previous_mttr - current_mttr) / previous_mttr) * 100:.0f}% - "
                "investigation and resolution becoming faster"
            )
        elif current_mttr > previous_mttr * 1.2:
            insights.append(
                f"MTTR increased by {((current_mttr - previous_mttr) / previous_mttr) * 100:.0f}% - "
                "review runbooks and consider additional training"
            )

        if not insights:
            insights.append("Metrics are stable compared to previous period")

        return insights


_analyzer: TrendAnalyzer | None = None


def get_trend_analyzer() -> TrendAnalyzer:
    """Get the default trend analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = TrendAnalyzer()
    return _analyzer
