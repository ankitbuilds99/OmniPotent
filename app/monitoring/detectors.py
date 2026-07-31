"""Anomaly Detectors - Various detection algorithms.

Detectors:
- StatisticalDetector: Z-score based anomaly detection
- ThresholdDetector: Fixed threshold breaches
- TrendDetector: Sudden trend changes
- PatternDetector: Pattern-based anomalies (e.g., error bursts)
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from collections import deque

log = logging.getLogger("devcopilot.monitoring.detectors")


class AnomalyType(str, Enum):
    """Types of detected anomalies."""
    THRESHOLD_BREACH = "threshold_breach"
    STATISTICAL_OUTLIER = "statistical_outlier"
    TREND_CHANGE = "trend_change"
    ERROR_BURST = "error_burst"
    LATENCY_SPIKE = "latency_spike"
    TRAFFIC_DROP = "traffic_drop"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    PATTERN_ANOMALY = "pattern_anomaly"


@dataclass
class Anomaly:
    """A detected anomaly."""
    anomaly_type: AnomalyType
    metric_name: str
    service: str
    severity: str  # low, medium, high, critical
    confidence: float  # 0.0 - 1.0
    current_value: float
    expected_value: float | None = None
    threshold: float | None = None
    description: str = ""
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type.value,
            "metric_name": self.metric_name,
            "service": self.service,
            "severity": self.severity,
            "confidence": self.confidence,
            "current_value": self.current_value,
            "expected_value": self.expected_value,
            "threshold": self.threshold,
            "description": self.description,
            "detected_at": self.detected_at.isoformat(),
            "context": self.context,
        }


class AnomalyDetector(ABC):
    """Base class for anomaly detectors."""

    @abstractmethod
    def detect(
        self,
        metric_name: str,
        service: str,
        values: list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[Anomaly]:
        """Detect anomalies in a metric series.

        Args:
            metric_name: Name of the metric
            service: Service the metric belongs to
            values: Time series values (oldest to newest)
            timestamps: Optional timestamps for each value

        Returns:
            List of detected anomalies
        """
        pass

    @property
    @abstractmethod
    def detector_type(self) -> str:
        """Return the detector type name."""
        pass


class StatisticalDetector(AnomalyDetector):
    """Detects anomalies using statistical methods (z-score).

    Uses a rolling window to compute mean and standard deviation,
    then flags values that deviate significantly.
    """

    def __init__(
        self,
        z_threshold: float = 3.0,
        window_size: int = 100,
        min_samples: int = 10,
    ):
        """Initialize the statistical detector.

        Args:
            z_threshold: Z-score threshold for anomaly detection
            window_size: Rolling window size for statistics
            min_samples: Minimum samples needed before detection
        """
        self.z_threshold = z_threshold
        self.window_size = window_size
        self.min_samples = min_samples

    @property
    def detector_type(self) -> str:
        return "statistical"

    def detect(
        self,
        metric_name: str,
        service: str,
        values: list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[Anomaly]:
        if len(values) < self.min_samples:
            return []

        anomalies = []

        # Use rolling window for adaptive baseline
        window: deque[float] = deque(maxlen=self.window_size)

        for i, value in enumerate(values):
            if len(window) >= self.min_samples:
                mean = sum(window) / len(window)
                variance = sum((x - mean) ** 2 for x in window) / len(window)
                std = math.sqrt(variance) if variance > 0 else 0.001

                z_score = abs(value - mean) / std

                if z_score > self.z_threshold:
                    severity = self._severity_from_zscore(z_score)
                    confidence = min(0.99, 0.5 + (z_score - self.z_threshold) * 0.1)

                    anomalies.append(Anomaly(
                        anomaly_type=AnomalyType.STATISTICAL_OUTLIER,
                        metric_name=metric_name,
                        service=service,
                        severity=severity,
                        confidence=confidence,
                        current_value=value,
                        expected_value=mean,
                        threshold=mean + self.z_threshold * std,
                        description=f"Value {value:.2f} is {z_score:.1f} standard deviations from mean {mean:.2f}",
                        detected_at=timestamps[i] if timestamps else datetime.now(timezone.utc),
                        context={"z_score": z_score, "std": std, "window_size": len(window)},
                    ))

            window.append(value)

        return anomalies

    def _severity_from_zscore(self, z_score: float) -> str:
        if z_score > 5:
            return "critical"
        elif z_score > 4:
            return "high"
        elif z_score > 3.5:
            return "medium"
        else:
            return "low"


class ThresholdDetector(AnomalyDetector):
    """Detects threshold breaches based on fixed or dynamic thresholds."""

    def __init__(self, thresholds: dict[str, dict[str, Any]] | None = None):
        """Initialize with metric thresholds.

        Args:
            thresholds: Dict of metric_name -> {warning, critical, direction}
                       direction: "above" or "below"
        """
        self.thresholds: dict[str, dict[str, Any]] = thresholds or {}

        # Default thresholds for common metrics
        self.default_thresholds: dict[str, dict[str, Any]] = {
            "error_rate": {"warning": 0.01, "critical": 0.05, "direction": "above"},
            "latency_p99": {"warning": 500, "critical": 2000, "direction": "above"},
            "latency_p95": {"warning": 200, "critical": 1000, "direction": "above"},
            "cpu_usage": {"warning": 70, "critical": 90, "direction": "above"},
            "memory_usage": {"warning": 80, "critical": 95, "direction": "above"},
            "disk_usage": {"warning": 80, "critical": 95, "direction": "above"},
            "request_rate": {"warning": 10, "critical": 5, "direction": "below"},
        }

    @property
    def detector_type(self) -> str:
        return "threshold"

    def detect(
        self,
        metric_name: str,
        service: str,
        values: list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[Anomaly]:
        if not values:
            return []

        # Get thresholds for this metric
        thresholds = self.thresholds.get(metric_name)
        if not thresholds:
            # Check default thresholds
            for key, defaults in self.default_thresholds.items():
                if key in metric_name.lower():
                    thresholds = defaults
                    break

        if not thresholds:
            return []

        anomalies = []
        direction = thresholds.get("direction", "above")
        warning = thresholds.get("warning")
        critical = thresholds.get("critical")

        # Check most recent value
        current = values[-1]
        ts = timestamps[-1] if timestamps else datetime.now(timezone.utc)

        breach_type = None
        severity = None

        if direction == "above":
            if critical and current > critical:
                breach_type = "critical"
                severity = "critical"
            elif warning and current > warning:
                breach_type = "warning"
                severity = "medium"
        else:  # below
            if critical and current < critical:
                breach_type = "critical"
                severity = "critical"
            elif warning and current < warning:
                breach_type = "warning"
                severity = "medium"

        if breach_type and severity:
            threshold = critical if breach_type == "critical" else warning
            anomalies.append(Anomaly(
                anomaly_type=AnomalyType.THRESHOLD_BREACH,
                metric_name=metric_name,
                service=service,
                severity=severity,
                confidence=0.95,
                current_value=current,
                threshold=threshold,
                description=f"{metric_name} at {current:.2f} breached {breach_type} threshold of {threshold}",
                detected_at=ts,
                context={"breach_type": breach_type, "direction": direction},
            ))

        return anomalies


class TrendDetector(AnomalyDetector):
    """Detects sudden trend changes in metrics.

    Uses linear regression on short and long windows to detect
    when the trend direction or magnitude changes significantly.
    """

    def __init__(
        self,
        short_window: int = 10,
        long_window: int = 60,
        slope_change_threshold: float = 2.0,
    ):
        """Initialize the trend detector.

        Args:
            short_window: Short window for recent trend
            long_window: Long window for baseline trend
            slope_change_threshold: Multiplier for significant slope change
        """
        self.short_window = short_window
        self.long_window = long_window
        self.slope_change_threshold = slope_change_threshold

    @property
    def detector_type(self) -> str:
        return "trend"

    def detect(
        self,
        metric_name: str,
        service: str,
        values: list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[Anomaly]:
        if len(values) < self.long_window:
            return []

        # Calculate slopes for short and long windows
        short_slope = self._calculate_slope(values[-self.short_window:])
        long_slope = self._calculate_slope(values[-self.long_window:])

        anomalies = []

        # Detect significant trend change
        if abs(long_slope) > 0.001:  # Avoid division by near-zero
            slope_ratio = short_slope / long_slope

            if abs(slope_ratio) > self.slope_change_threshold:
                # Trend reversal or acceleration
                if slope_ratio < 0:
                    description = f"Trend reversed: was {'increasing' if long_slope > 0 else 'decreasing'}, now {'decreasing' if short_slope < 0 else 'increasing'}"
                    severity = "high"
                else:
                    description = f"Trend accelerated {abs(slope_ratio):.1f}x"
                    severity = "medium"

                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.TREND_CHANGE,
                    metric_name=metric_name,
                    service=service,
                    severity=severity,
                    confidence=0.8,
                    current_value=values[-1],
                    description=description,
                    detected_at=timestamps[-1] if timestamps else datetime.now(timezone.utc),
                    context={
                        "short_slope": short_slope,
                        "long_slope": long_slope,
                        "slope_ratio": slope_ratio,
                    },
                ))

        # Detect sudden spike/drop in recent window
        if len(values) >= 2:
            recent_change = (values[-1] - values[-2]) / max(abs(values[-2]), 0.001)
            if abs(recent_change) > 0.5:  # 50% change in one step
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.LATENCY_SPIKE if recent_change > 0 else AnomalyType.TRAFFIC_DROP,
                    metric_name=metric_name,
                    service=service,
                    severity="high" if abs(recent_change) > 1.0 else "medium",
                    confidence=0.85,
                    current_value=values[-1],
                    expected_value=values[-2],
                    description=f"Sudden {'increase' if recent_change > 0 else 'decrease'} of {abs(recent_change)*100:.0f}%",
                    detected_at=timestamps[-1] if timestamps else datetime.now(timezone.utc),
                    context={"percent_change": recent_change * 100},
                ))

        return anomalies

    def _calculate_slope(self, values: list[float]) -> float:
        """Calculate linear regression slope."""
        n = len(values)
        if n < 2:
            return 0.0

        # Simple linear regression
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        return numerator / denominator if denominator != 0 else 0.0


class PatternDetector(AnomalyDetector):
    """Detects pattern-based anomalies like error bursts.

    Looks for specific patterns in the data that indicate problems:
    - Error bursts (sudden increase in error count)
    - Periodic anomalies (issues at specific times)
    - Cascading failures (correlated issues across services)
    """

    def __init__(
        self,
        burst_threshold: int = 5,
        burst_window: int = 10,
    ):
        """Initialize the pattern detector.

        Args:
            burst_threshold: Number of consecutive anomalies for a burst
            burst_window: Window size to check for bursts
        """
        self.burst_threshold = burst_threshold
        self.burst_window = burst_window

    @property
    def detector_type(self) -> str:
        return "pattern"

    def detect(
        self,
        metric_name: str,
        service: str,
        values: list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[Anomaly]:
        anomalies = []

        # Detect error bursts (for error-related metrics)
        if "error" in metric_name.lower():
            burst = self._detect_burst(values)
            if burst:
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.ERROR_BURST,
                    metric_name=metric_name,
                    service=service,
                    severity="high",
                    confidence=0.9,
                    current_value=values[-1],
                    description=f"Error burst detected: {burst['count']} errors in last {burst['window']} samples",
                    detected_at=timestamps[-1] if timestamps else datetime.now(timezone.utc),
                    context=burst,
                ))

        # Detect resource exhaustion patterns
        if any(x in metric_name.lower() for x in ["cpu", "memory", "disk"]):
            exhaustion = self._detect_exhaustion(values)
            if exhaustion:
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.RESOURCE_EXHAUSTION,
                    metric_name=metric_name,
                    service=service,
                    severity="critical" if exhaustion["rate"] > 0.01 else "high",
                    confidence=0.85,
                    current_value=values[-1],
                    description=f"Resource approaching exhaustion: {values[-1]:.1f}% with growth rate {exhaustion['rate']:.2f}%/min",
                    detected_at=timestamps[-1] if timestamps else datetime.now(timezone.utc),
                    context=exhaustion,
                ))

        return anomalies

    def _detect_burst(self, values: list[float]) -> dict[str, Any] | None:
        """Detect error burst pattern."""
        if len(values) < self.burst_window:
            return None

        recent = values[-self.burst_window:]

        # Count values above baseline
        baseline = sum(values[:-self.burst_window]) / max(1, len(values) - self.burst_window) if len(values) > self.burst_window else 0
        above_baseline = sum(1 for v in recent if v > baseline * 2)

        if above_baseline >= self.burst_threshold:
            return {
                "count": above_baseline,
                "window": self.burst_window,
                "baseline": baseline,
                "peak": max(recent),
            }

        return None

    def _detect_exhaustion(self, values: list[float]) -> dict[str, Any] | None:
        """Detect resource exhaustion pattern."""
        if len(values) < 10:
            return None

        current = values[-1]
        if current < 75:  # Below 75% is not concerning
            return None

        # Calculate growth rate
        recent = values[-10:]
        growth = (recent[-1] - recent[0]) / 10  # Per sample

        # Time to exhaustion
        remaining = 100 - current
        if growth > 0:
            time_to_exhaust = remaining / growth
            if time_to_exhaust < 60:  # Less than 60 samples until 100%
                return {
                    "current": current,
                    "rate": growth,
                    "time_to_exhaust": time_to_exhaust,
                }

        return None
