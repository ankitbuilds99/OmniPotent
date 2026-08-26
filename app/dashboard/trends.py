"""Trend analysis for dashboard metrics.

Provides:
- Time-series trend detection
- Anomaly detection (basic)
- Forecasting (simple)
- Comparative analysis
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.dashboard.trends")


class TrendDirection(str, Enum):
    """Direction of a trend, in terms of whether it is good or bad.

    Not the direction of the number itself — see `MetricPolarity`. For MTTR,
    error rate or cost, a rising value is DECLINING health.
    """

    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    UNKNOWN = "unknown"


class MetricPolarity(str, Enum):
    """Whether a rising value is good or bad for a given metric.

    Without this every metric was treated as HIGHER_IS_BETTER, so a 30% rise in
    mean-time-to-resolution was reported to the user as "mttr_minutes improved by
    30.0%" — the exact opposite of what happened, in the human-readable sentence
    that goes on the dashboard.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


# Metrics where a rising number is worse. Matched as a substring against the
# metric name, so "mttr_minutes" and "p90_mttr" both resolve.
_LOWER_IS_BETTER_HINTS = (
    "mttr",
    "duration",
    "latency",
    "error",
    "failure",
    "cost",
    "spend",
    "abstention",
    "escalation",
    "downtime",
    "incident",
)


def infer_polarity(metric_name: str) -> MetricPolarity:
    """Guess a metric's polarity from its name.

    A guess, and named like one. Callers that know the answer should pass
    `polarity` to `analyze` explicitly rather than rely on this.
    """
    lowered = metric_name.lower()
    if any(hint in lowered for hint in _LOWER_IS_BETTER_HINTS):
        return MetricPolarity.LOWER_IS_BETTER
    return MetricPolarity.HIGHER_IS_BETTER


class AnomalyType(str, Enum):
    """Type of anomaly detected."""

    SPIKE = "spike"
    DROP = "drop"
    PATTERN_BREAK = "pattern_break"
    NONE = "none"


@dataclass
class DataPoint:
    """A single data point in a time series."""

    timestamp: datetime
    value: float
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trend:
    """Analyzed trend for a metric."""

    metric_name: str
    direction: TrendDirection
    change_percent: float = 0.0
    current_value: float = 0.0
    previous_value: float = 0.0
    confidence: float = 0.0
    description: str = ""
    data_points: list[DataPoint] = field(default_factory=list)
    anomaly: AnomalyType = AnomalyType.NONE
    forecast_next: float | None = None
    # Recorded so a consumer can tell why a falling number was called an
    # improvement. Inferred from the metric name when not supplied.
    polarity: MetricPolarity | None = None

    def __post_init__(self) -> None:
        if self.polarity is None:
            self.polarity = infer_polarity(self.metric_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "direction": self.direction.value,
            # Signed and raw: `direction` already says whether the change is good,
            # so a consumer that wants "which way did the number move" needs the
            # sign preserved here.
            "change_percent": round(self.change_percent, 1),
            "polarity": (self.polarity or infer_polarity(self.metric_name)).value,
            "current_value": round(self.current_value, 2),
            "previous_value": round(self.previous_value, 2),
            "confidence": round(self.confidence, 2),
            "description": self.description,
            "anomaly": self.anomaly.value,
            "forecast_next": round(self.forecast_next, 2) if self.forecast_next else None,
        }


class TrendAnalyzer:
    """Analyzes time series data for trends and anomalies."""

    def __init__(
        self,
        significance_threshold: float = 0.1,
        anomaly_std_threshold: float = 2.0,
    ):
        """Initialize the trend analyzer.

        Args:
            significance_threshold: Minimum change % to be significant
            anomaly_std_threshold: Standard deviations for anomaly detection
        """
        self.significance_threshold = significance_threshold
        self.anomaly_std_threshold = anomaly_std_threshold

    def analyze(
        self,
        metric_name: str,
        data_points: list[DataPoint],
        comparison_window: int = 7,
        polarity: MetricPolarity | None = None,
    ) -> Trend:
        """Analyze a time series for trends.

        Args:
            metric_name: Name of the metric
            data_points: List of data points (oldest first)
            comparison_window: Days to use for comparison
            polarity: Whether a rising value is good or bad for this metric.
                Inferred from the name when omitted.

        Returns:
            Trend analysis result
        """
        if polarity is None:
            polarity = infer_polarity(metric_name)
        if len(data_points) < 2:
            return Trend(
                metric_name=metric_name,
                direction=TrendDirection.UNKNOWN,
                description="Insufficient data for trend analysis",
            )

        sorted_points = sorted(data_points, key=lambda x: x.timestamp)
        values = [p.value for p in sorted_points]

        current = self._calculate_window_avg(values, min(comparison_window, len(values)))
        previous = self._calculate_window_avg(
            values[:-comparison_window] if len(values) > comparison_window else values[:1],
            min(comparison_window, max(1, len(values) - comparison_window)),
        )

        if previous != 0:
            change_percent = ((current - previous) / abs(previous)) * 100
        else:
            change_percent = 100.0 if current > 0 else 0.0

        if abs(change_percent) < self.significance_threshold * 100:
            direction = TrendDirection.STABLE
        else:
            rising = change_percent > 0
            better = rising == (polarity == MetricPolarity.HIGHER_IS_BETTER)
            direction = TrendDirection.IMPROVING if better else TrendDirection.DECLINING

        confidence = self._calculate_confidence(values)

        anomaly = self._detect_anomaly(values)

        forecast = self._simple_forecast(values)

        description = self._generate_description(
            metric_name, direction, change_percent, anomaly
        )

        return Trend(
            metric_name=metric_name,
            direction=direction,
            change_percent=change_percent,
            current_value=current,
            previous_value=previous,
            confidence=confidence,
            description=description,
            data_points=sorted_points[-30:],
            anomaly=anomaly,
            forecast_next=forecast,
            polarity=polarity,
        )

    def _calculate_window_avg(self, values: list[float], window: int) -> float:
        """Calculate average of last N values."""
        if not values or window <= 0:
            return 0.0
        window_values = values[-window:]
        return sum(window_values) / len(window_values)

    def _calculate_confidence(self, values: list[float]) -> float:
        """Calculate confidence in the trend based on data consistency."""
        if len(values) < 3:
            return 0.3

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = math.sqrt(variance) if variance > 0 else 0

        if mean == 0:
            cv = 0.0
        else:
            cv = std_dev / abs(mean)

        confidence = max(0.1, min(1.0, 1 - cv))

        data_points_factor = min(1.0, len(values) / 30)
        confidence *= (0.5 + 0.5 * data_points_factor)

        return confidence

    def _detect_anomaly(self, values: list[float]) -> AnomalyType:
        """Detect anomalies in the data."""
        if len(values) < 5:
            return AnomalyType.NONE

        mean = sum(values[:-1]) / len(values[:-1])
        variance = sum((v - mean) ** 2 for v in values[:-1]) / len(values[:-1])
        std_dev = math.sqrt(variance) if variance > 0 else 0

        if std_dev == 0:
            return AnomalyType.NONE

        latest = values[-1]
        z_score = (latest - mean) / std_dev

        if z_score > self.anomaly_std_threshold:
            return AnomalyType.SPIKE
        elif z_score < -self.anomaly_std_threshold:
            return AnomalyType.DROP

        return AnomalyType.NONE

    def _simple_forecast(self, values: list[float]) -> float | None:
        """Simple linear forecast for next value."""
        if len(values) < 3:
            return None

        recent = values[-7:] if len(values) >= 7 else values

        n = len(recent)
        sum_x = sum(range(n))
        sum_y = sum(recent)
        sum_xy = sum(i * y for i, y in enumerate(recent))
        sum_x2 = sum(i * i for i in range(n))

        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return recent[-1]

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n

        return intercept + slope * n

    def _generate_description(
        self,
        metric_name: str,
        direction: TrendDirection,
        change_percent: float,
        anomaly: AnomalyType,
    ) -> str:
        """Generate human-readable trend description."""
        parts = []

        if direction == TrendDirection.IMPROVING:
            parts.append(f"{metric_name} improved by {abs(change_percent):.1f}%")
        elif direction == TrendDirection.DECLINING:
            parts.append(f"{metric_name} declined by {abs(change_percent):.1f}%")
        else:
            parts.append(f"{metric_name} remained stable")

        if anomaly == AnomalyType.SPIKE:
            parts.append("(unusual spike detected)")
        elif anomaly == AnomalyType.DROP:
            parts.append("(unusual drop detected)")

        return " ".join(parts)


def calculate_trends(
    metrics: dict[str, list[tuple[datetime, float]]],
    comparison_days: int = 7,
    polarities: dict[str, MetricPolarity] | None = None,
) -> dict[str, Trend]:
    """Calculate trends for multiple metrics.

    Args:
        metrics: Dictionary of metric name -> list of (timestamp, value)
        comparison_days: Days to compare
        polarities: Optional metric name -> polarity. Any metric not listed has
            its polarity inferred from its name, which is a guess — supply this
            for metrics whose names don't say which direction is good.

    Returns:
        Dictionary of metric name -> Trend
    """
    analyzer = TrendAnalyzer()
    results = {}
    polarities = polarities or {}

    for metric_name, data in metrics.items():
        data_points = [
            DataPoint(timestamp=ts, value=val)
            for ts, val in data
        ]
        results[metric_name] = analyzer.analyze(
            metric_name,
            data_points,
            comparison_days,
            polarity=polarities.get(metric_name),
        )

    return results


def generate_trend_report(trends: dict[str, Trend]) -> dict[str, Any]:
    """Generate a summary report of trends.

    Args:
        trends: Dictionary of analyzed trends

    Returns:
        Report dictionary
    """
    improving = []
    declining = []
    anomalies = []

    for name, trend in trends.items():
        if trend.direction == TrendDirection.IMPROVING:
            improving.append({
                "metric": name,
                "change_percent": trend.change_percent,
            })
        elif trend.direction == TrendDirection.DECLINING:
            declining.append({
                "metric": name,
                "change_percent": trend.change_percent,
            })

        if trend.anomaly != AnomalyType.NONE:
            anomalies.append({
                "metric": name,
                "anomaly_type": trend.anomaly.value,
                "current_value": trend.current_value,
            })

    return {
        "summary": {
            "total_metrics": len(trends),
            "improving": len(improving),
            "declining": len(declining),
            "stable": len(trends) - len(improving) - len(declining),
            "anomalies_detected": len(anomalies),
        },
        "improving_metrics": sorted(
            improving, key=lambda x: x["change_percent"], reverse=True
        ),
        "declining_metrics": sorted(
            declining, key=lambda x: x["change_percent"]
        ),
        "anomalies": anomalies,
        "trends": {name: trend.to_dict() for name, trend in trends.items()},
    }
