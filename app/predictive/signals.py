"""Multi-signal correlation for predictive incident detection.

Tracks multiple metrics and correlates them to predict failures.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.predictive.signals")


class SignalType(Enum):
    """Types of signals that can indicate impending incidents."""

    MEMORY_USAGE = "memory_usage"
    CPU_USAGE = "cpu_usage"
    DISK_USAGE = "disk_usage"
    CONNECTION_POOL = "connection_pool"
    REQUEST_QUEUE = "request_queue"
    ERROR_RATE = "error_rate"
    LATENCY_P99 = "latency_p99"
    GC_PAUSE = "gc_pause"
    THREAD_COUNT = "thread_count"
    OPEN_FILES = "open_files"
    NETWORK_CONNECTIONS = "network_connections"
    CACHE_HIT_RATE = "cache_hit_rate"
    CUSTOM = "custom"


class SignalTrend(Enum):
    """Trend direction for a signal."""

    STABLE = "stable"
    INCREASING = "increasing"
    DECREASING = "decreasing"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


@dataclass
class SignalDataPoint:
    """A single data point for a signal."""

    timestamp: datetime
    value: float
    service: str
    signal_type: SignalType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricSignal:
    """A metric signal being tracked for anomaly detection."""

    signal_id: str
    signal_type: SignalType
    service: str
    current_value: float
    threshold_warning: float
    threshold_critical: float
    trend: SignalTrend
    trend_rate: float
    time_to_threshold: timedelta | None
    baseline_mean: float
    baseline_stddev: float
    data_points: list[SignalDataPoint] = field(default_factory=list)

    @property
    def severity_score(self) -> float:
        """Calculate severity score 0-100."""
        if self.threshold_critical == 0:
            return 0.0

        ratio = self.current_value / self.threshold_critical
        base_score = min(100.0, ratio * 100)

        if self.trend == SignalTrend.INCREASING:
            base_score *= 1.2
        elif self.trend == SignalTrend.VOLATILE:
            base_score *= 1.1

        return min(100.0, base_score)

    @property
    def is_anomalous(self) -> bool:
        """Check if current value is anomalous based on baseline."""
        if self.baseline_stddev == 0:
            return False
        z_score = abs(self.current_value - self.baseline_mean) / self.baseline_stddev
        return z_score > 3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type.value,
            "service": self.service,
            "current_value": self.current_value,
            "threshold_warning": self.threshold_warning,
            "threshold_critical": self.threshold_critical,
            "trend": self.trend.value,
            "trend_rate": self.trend_rate,
            "time_to_threshold_minutes": (
                self.time_to_threshold.total_seconds() / 60
                if self.time_to_threshold
                else None
            ),
            "severity_score": self.severity_score,
            "is_anomalous": self.is_anomalous,
        }


@dataclass
class SignalCorrelation:
    """Correlation between two signals."""

    signal_a: str
    signal_b: str
    correlation_coefficient: float
    lag_seconds: int
    confidence: float

    @property
    def is_strongly_correlated(self) -> bool:
        return abs(self.correlation_coefficient) > 0.7 and self.confidence > 0.8


class SignalCorrelator:
    """Correlates multiple signals to detect patterns leading to failures."""

    def __init__(self, history_window_hours: int = 24) -> None:
        self.history_window = timedelta(hours=history_window_hours)
        self._signals: dict[str, MetricSignal] = {}
        self._data_points: list[SignalDataPoint] = []
        self._correlations: list[SignalCorrelation] = []
        self._signal_counter = 0

    def register_signal(
        self,
        signal_type: SignalType,
        service: str,
        threshold_warning: float,
        threshold_critical: float,
        baseline_mean: float | None = None,
        baseline_stddev: float | None = None,
    ) -> MetricSignal:
        """Register a new signal to track."""
        self._signal_counter += 1
        signal_id = f"sig_{self._signal_counter}"

        signal = MetricSignal(
            signal_id=signal_id,
            signal_type=signal_type,
            service=service,
            current_value=0.0,
            threshold_warning=threshold_warning,
            threshold_critical=threshold_critical,
            trend=SignalTrend.UNKNOWN,
            trend_rate=0.0,
            time_to_threshold=None,
            baseline_mean=baseline_mean or threshold_warning * 0.5,
            baseline_stddev=baseline_stddev or threshold_warning * 0.1,
        )

        self._signals[signal_id] = signal

        log.info(
            "signal registered",
            extra={
                "signal_id": signal_id,
                "signal_type": signal_type.value,
                "service": service,
            },
        )

        return signal

    def record_data_point(
        self,
        signal_id: str,
        value: float,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record a data point for a signal."""
        signal = self._signals.get(signal_id)
        if not signal:
            return False

        ts = timestamp or datetime.now(timezone.utc)
        point = SignalDataPoint(
            timestamp=ts,
            value=value,
            service=signal.service,
            signal_type=signal.signal_type,
            metadata=metadata or {},
        )

        signal.data_points.append(point)
        self._data_points.append(point)

        signal.current_value = value
        self._update_trend(signal)
        self._prune_old_data(signal)

        return True

    def _update_trend(self, signal: MetricSignal) -> None:
        """Update trend analysis for a signal."""
        points = signal.data_points
        if len(points) < 3:
            signal.trend = SignalTrend.UNKNOWN
            return

        recent = points[-10:] if len(points) >= 10 else points
        values = [p.value for p in recent]

        if len(recent) < 2:
            signal.trend = SignalTrend.UNKNOWN
            return

        time_span = (recent[-1].timestamp - recent[0].timestamp).total_seconds()
        if time_span == 0:
            signal.trend = SignalTrend.UNKNOWN
            return

        value_change = values[-1] - values[0]
        signal.trend_rate = value_change / (time_span / 3600)

        mean_val = sum(values) / len(values)
        variance = sum((v - mean_val) ** 2 for v in values) / len(values)
        cv = math.sqrt(variance) / mean_val if mean_val > 0 else 0

        if cv > 0.3:
            signal.trend = SignalTrend.VOLATILE
        elif signal.trend_rate > signal.threshold_warning * 0.01:
            signal.trend = SignalTrend.INCREASING
        elif signal.trend_rate < -signal.threshold_warning * 0.01:
            signal.trend = SignalTrend.DECREASING
        else:
            signal.trend = SignalTrend.STABLE

        if signal.trend == SignalTrend.INCREASING and signal.trend_rate > 0:
            remaining = signal.threshold_critical - signal.current_value
            if remaining > 0:
                hours_to_threshold = remaining / signal.trend_rate
                signal.time_to_threshold = timedelta(hours=hours_to_threshold)
            else:
                signal.time_to_threshold = timedelta(seconds=0)

    def _prune_old_data(self, signal: MetricSignal) -> None:
        """Remove data points outside the history window."""
        cutoff = datetime.now(timezone.utc) - self.history_window
        signal.data_points = [p for p in signal.data_points if p.timestamp >= cutoff]

        if len(signal.data_points) > 1000:
            signal.data_points = signal.data_points[-1000:]

    def get_signal(self, signal_id: str) -> MetricSignal | None:
        """Get a signal by ID."""
        return self._signals.get(signal_id)

    def get_signals_for_service(self, service: str) -> list[MetricSignal]:
        """Get all signals for a service."""
        return [s for s in self._signals.values() if s.service == service]

    def get_critical_signals(self) -> list[MetricSignal]:
        """Get signals that are at or above critical threshold."""
        return [
            s
            for s in self._signals.values()
            if s.current_value >= s.threshold_critical
        ]

    def get_warning_signals(self) -> list[MetricSignal]:
        """Get signals that are at or above warning threshold."""
        return [
            s
            for s in self._signals.values()
            if s.current_value >= s.threshold_warning
            and s.current_value < s.threshold_critical
        ]

    def get_trending_signals(self) -> list[MetricSignal]:
        """Get signals that are trending toward failure."""
        return [
            s
            for s in self._signals.values()
            if s.trend == SignalTrend.INCREASING
            and s.time_to_threshold is not None
            and s.time_to_threshold < timedelta(hours=4)
        ]

    def calculate_correlation(
        self,
        signal_a_id: str,
        signal_b_id: str,
        max_lag_seconds: int = 3600,
    ) -> SignalCorrelation | None:
        """Calculate correlation between two signals."""
        signal_a = self._signals.get(signal_a_id)
        signal_b = self._signals.get(signal_b_id)

        if not signal_a or not signal_b:
            return None

        if len(signal_a.data_points) < 10 or len(signal_b.data_points) < 10:
            return None

        values_a = [p.value for p in signal_a.data_points[-100:]]
        values_b = [p.value for p in signal_b.data_points[-100:]]

        min_len = min(len(values_a), len(values_b))
        values_a = values_a[-min_len:]
        values_b = values_b[-min_len:]

        if min_len < 5:
            return None

        corr = self._pearson_correlation(values_a, values_b)

        return SignalCorrelation(
            signal_a=signal_a_id,
            signal_b=signal_b_id,
            correlation_coefficient=corr,
            lag_seconds=0,
            confidence=min(1.0, min_len / 50),
        )

    def _pearson_correlation(self, x: list[float], y: list[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        if n != len(y) or n == 0:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

        if std_x == 0 or std_y == 0:
            return 0.0

        return cov / (std_x * std_y)

    def find_correlated_signals(
        self,
        signal_id: str,
        min_correlation: float = 0.7,
    ) -> list[SignalCorrelation]:
        """Find all signals that correlate with the given signal."""
        correlations = []

        for other_id in self._signals:
            if other_id == signal_id:
                continue

            corr = self.calculate_correlation(signal_id, other_id)
            if corr and abs(corr.correlation_coefficient) >= min_correlation:
                correlations.append(corr)

        return sorted(
            correlations,
            key=lambda c: abs(c.correlation_coefficient),
            reverse=True,
        )

    def detect_anomalies(self) -> list[MetricSignal]:
        """Detect anomalous signals based on baseline deviation."""
        return [s for s in self._signals.values() if s.is_anomalous]

    def get_system_health_score(self) -> float:
        """Calculate overall system health score 0-100."""
        if not self._signals:
            return 100.0

        total_severity = sum(s.severity_score for s in self._signals.values())
        avg_severity = total_severity / len(self._signals)

        return max(0.0, 100.0 - avg_severity)

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the predictive dashboard."""
        critical = self.get_critical_signals()
        warning = self.get_warning_signals()
        trending = self.get_trending_signals()
        anomalies = self.detect_anomalies()

        return {
            "health_score": round(self.get_system_health_score(), 1),
            "total_signals": len(self._signals),
            "critical_count": len(critical),
            "warning_count": len(warning),
            "trending_count": len(trending),
            "anomaly_count": len(anomalies),
            "critical_signals": [s.to_dict() for s in critical[:5]],
            "trending_signals": [s.to_dict() for s in trending[:5]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_correlator: SignalCorrelator | None = None


def get_signal_correlator() -> SignalCorrelator:
    """Get the default signal correlator."""
    global _correlator
    if _correlator is None:
        _correlator = SignalCorrelator()
    return _correlator
