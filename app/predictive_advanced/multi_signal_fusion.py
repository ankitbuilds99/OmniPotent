"""Multi-Signal Fusion for Predictive Incidents.

Combines multiple metric signals with correlation analysis
to produce high-confidence predictions.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.predictive_advanced.multi_signal_fusion")


class SignalSource(str, Enum):
    MEMORY = "memory"
    CPU = "cpu"
    DISK = "disk"
    NETWORK = "network"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"
    CONNECTIONS = "connections"
    GC_PAUSE = "gc_pause"
    QUEUE_DEPTH = "queue_depth"
    SATURATION = "saturation"
    CUSTOM = "custom"


class SignalTrend(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    VOLATILE = "volatile"


@dataclass
class SignalDataPoint:
    """A single data point in a signal series."""

    timestamp: datetime
    value: float
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "value": round(self.value, 2),
            "unit": self.unit,
        }


@dataclass
class CorrelatedSignal:
    """A signal with its correlation to the primary signal."""

    signal_id: str
    source: SignalSource
    name: str
    current_value: float
    projected_value: float
    threshold: float
    unit: str
    trend: SignalTrend
    correlation_coefficient: float
    data_points: list[SignalDataPoint] = field(default_factory=list)
    is_primary: bool = False

    @property
    def is_critical(self) -> bool:
        return self.projected_value >= self.threshold

    @property
    def time_to_threshold_minutes(self) -> float | None:
        if self.trend != SignalTrend.INCREASING:
            return None
        if self.current_value >= self.threshold:
            return 0
        if len(self.data_points) < 2:
            return None

        rate = self._calculate_rate()
        if rate <= 0:
            return None

        remaining = self.threshold - self.current_value
        return remaining / rate

    def _calculate_rate(self) -> float:
        if len(self.data_points) < 2:
            return 0

        first = self.data_points[0]
        last = self.data_points[-1]
        time_diff = (last.timestamp - first.timestamp).total_seconds() / 60

        if time_diff <= 0:
            return 0

        return (last.value - first.value) / time_diff

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "name": self.name,
            "current": round(self.current_value, 2),
            "projected": round(self.projected_value, 2),
            "threshold": round(self.threshold, 2),
            "unit": self.unit,
            "trend": self.trend.value,
            "correlation": round(self.correlation_coefficient, 2),
            "is_primary": self.is_primary,
            "is_critical": self.is_critical,
            "time_to_threshold_minutes": (
                round(self.time_to_threshold_minutes, 1)
                if self.time_to_threshold_minutes is not None
                else None
            ),
        }


@dataclass
class FusedPrediction:
    """A prediction created by fusing multiple signals."""

    prediction_id: str
    service: str
    predicted_event: str
    confidence: float
    calibrated_accuracy: float
    signals: list[CorrelatedSignal]
    time_to_impact_minutes: float
    impact_range_minutes: tuple[float, float]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def primary_signal(self) -> CorrelatedSignal | None:
        for signal in self.signals:
            if signal.is_primary:
                return signal
        return None

    @property
    def combined_confidence(self) -> float:
        if not self.signals:
            return 0.0

        weighted_sum = 0.0
        weight_total = 0.0

        for signal in self.signals:
            weight = abs(signal.correlation_coefficient)
            signal_confidence = 1.0 if signal.is_critical else 0.5
            weighted_sum += weight * signal_confidence
            weight_total += weight

        if weight_total == 0:
            return 0.0

        return min(0.99, weighted_sum / weight_total)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "service": self.service,
            "predicted_event": self.predicted_event,
            "confidence": round(self.confidence, 2),
            "calibrated_accuracy": round(self.calibrated_accuracy, 2),
            "signals": [s.to_dict() for s in self.signals],
            "time_to_impact_minutes": round(self.time_to_impact_minutes, 1),
            "impact_range": {
                "min_minutes": round(self.impact_range_minutes[0], 1),
                "max_minutes": round(self.impact_range_minutes[1], 1),
            },
            "signal_count": len(self.signals),
            "combined_confidence": round(self.combined_confidence, 2),
            "created_at": self.created_at.isoformat(),
        }


class MultiSignalFusion:
    """Fuses multiple metric signals into unified predictions."""

    def __init__(self) -> None:
        self._signal_history: dict[str, list[SignalDataPoint]] = {}
        self._correlation_cache: dict[str, dict[str, float]] = {}
        self._predictions: dict[str, FusedPrediction] = {}
        self._signal_thresholds: dict[SignalSource, float] = {
            SignalSource.MEMORY: 95.0,
            SignalSource.CPU: 90.0,
            SignalSource.DISK: 85.0,
            SignalSource.ERROR_RATE: 5.0,
            SignalSource.LATENCY: 500.0,
            SignalSource.CONNECTIONS: 1000,
            SignalSource.GC_PAUSE: 200.0,
            SignalSource.THROUGHPUT: 100.0,
            SignalSource.QUEUE_DEPTH: 10000,
            SignalSource.SATURATION: 0.9,
        }
        self._historical_accuracy: dict[str, list[bool]] = {}

    def set_threshold(self, source: SignalSource, threshold: float) -> None:
        """Set threshold for a signal source."""
        self._signal_thresholds[source] = threshold

    def ingest_signal(
        self,
        org_id: str,
        service: str,
        source: SignalSource,
        value: float,
        unit: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        """Ingest a signal data point."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        key = f"{org_id}:{service}:{source.value}"

        if key not in self._signal_history:
            self._signal_history[key] = []

        self._signal_history[key].append(SignalDataPoint(
            timestamp=timestamp,
            value=value,
            unit=unit,
        ))

        if len(self._signal_history[key]) > 1000:
            self._signal_history[key] = self._signal_history[key][-500:]

    def compute_correlation(
        self,
        org_id: str,
        service: str,
        source_a: SignalSource,
        source_b: SignalSource,
    ) -> float:
        """Compute Pearson correlation between two signals."""
        key_a = f"{org_id}:{service}:{source_a.value}"
        key_b = f"{org_id}:{service}:{source_b.value}"

        history_a = self._signal_history.get(key_a, [])
        history_b = self._signal_history.get(key_b, [])

        if len(history_a) < 10 or len(history_b) < 10:
            return 0

        values_a = [p.value for p in history_a[-100:]]
        values_b = [p.value for p in history_b[-100:]]

        min_len = min(len(values_a), len(values_b))
        values_a = values_a[-min_len:]
        values_b = values_b[-min_len:]

        return self._pearson_correlation(values_a, values_b)

    def _pearson_correlation(
        self,
        x: list[float],
        y: list[float],
    ) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))

        ss_x = sum((xi - mean_x) ** 2 for xi in x)
        ss_y = sum((yi - mean_y) ** 2 for yi in y)

        denominator = math.sqrt(ss_x * ss_y)

        if denominator == 0:
            return 0

        return numerator / denominator

    def analyze_signals(
        self,
        org_id: str,
        service: str,
        primary_source: SignalSource,
    ) -> list[CorrelatedSignal]:
        """Analyze all signals correlated with the primary signal."""
        signals: list[CorrelatedSignal] = []

        primary_key = f"{org_id}:{service}:{primary_source.value}"
        primary_history = self._signal_history.get(primary_key, [])

        if not primary_history:
            return signals

        primary_current = primary_history[-1].value if primary_history else 0
        primary_threshold = self._signal_thresholds.get(primary_source, 100)
        primary_projected = self._project_value(primary_history)

        signals.append(CorrelatedSignal(
            signal_id=f"sig_{primary_source.value}_primary",
            source=primary_source,
            name=primary_source.value.replace("_", " ").title(),
            current_value=primary_current,
            projected_value=primary_projected,
            threshold=primary_threshold,
            unit=self._get_unit_for_source(primary_source),
            trend=self._detect_trend(primary_history),
            correlation_coefficient=1.0,
            data_points=primary_history[-50:],
            is_primary=True,
        ))

        for source in SignalSource:
            if source == primary_source:
                continue

            key = f"{org_id}:{service}:{source.value}"
            history = self._signal_history.get(key, [])

            if not history:
                continue

            correlation = self.compute_correlation(
                org_id, service, primary_source, source
            )

            if abs(correlation) < 0.3:
                continue

            current = history[-1].value if history else 0
            projected = self._project_value(history)
            threshold = self._signal_thresholds.get(source, 100)

            signals.append(CorrelatedSignal(
                signal_id=f"sig_{source.value}",
                source=source,
                name=source.value.replace("_", " ").title(),
                current_value=current,
                projected_value=projected,
                threshold=threshold,
                unit=self._get_unit_for_source(source),
                trend=self._detect_trend(history),
                correlation_coefficient=correlation,
                data_points=history[-50:],
                is_primary=False,
            ))

        signals.sort(key=lambda s: abs(s.correlation_coefficient), reverse=True)

        return signals

    def _project_value(
        self,
        history: list[SignalDataPoint],
        hours_ahead: float = 2,
    ) -> float:
        """Project value based on trend."""
        if len(history) < 2:
            return history[-1].value if history else 0

        recent = history[-20:]
        first = recent[0]
        last = recent[-1]

        time_diff_hours = (last.timestamp - first.timestamp).total_seconds() / 3600
        if time_diff_hours <= 0:
            return last.value

        rate = (last.value - first.value) / time_diff_hours
        projected = last.value + (rate * hours_ahead)

        return max(0, projected)

    def _detect_trend(self, history: list[SignalDataPoint]) -> SignalTrend:
        """Detect trend in signal history."""
        if len(history) < 5:
            return SignalTrend.STABLE

        recent = [p.value for p in history[-10:]]
        mean_first_half = sum(recent[:5]) / 5
        mean_second_half = sum(recent[5:]) / len(recent[5:])

        diff = mean_second_half - mean_first_half
        threshold = mean_first_half * 0.05

        variance = sum((v - sum(recent) / len(recent)) ** 2 for v in recent) / len(recent)
        if variance > (mean_first_half * 0.2) ** 2:
            return SignalTrend.VOLATILE

        if diff > threshold:
            return SignalTrend.INCREASING
        elif diff < -threshold:
            return SignalTrend.DECREASING
        else:
            return SignalTrend.STABLE

    def _get_unit_for_source(self, source: SignalSource) -> str:
        """Get unit string for a signal source."""
        units = {
            SignalSource.MEMORY: "%",
            SignalSource.CPU: "%",
            SignalSource.DISK: "%",
            SignalSource.ERROR_RATE: "%",
            SignalSource.LATENCY: "ms",
            SignalSource.CONNECTIONS: "conn",
            SignalSource.GC_PAUSE: "ms",
            SignalSource.THROUGHPUT: "req/s",
            SignalSource.QUEUE_DEPTH: "items",
            SignalSource.SATURATION: "ratio",
        }
        return units.get(source, "")

    def create_fused_prediction(
        self,
        org_id: str,
        service: str,
        predicted_event: str,
        primary_source: SignalSource,
    ) -> FusedPrediction | None:
        """Create a fused prediction from multiple signals."""
        signals = self.analyze_signals(org_id, service, primary_source)

        if not signals:
            return None

        primary = signals[0]
        if not primary.is_critical and primary.time_to_threshold_minutes is None:
            return None

        time_to_impact = primary.time_to_threshold_minutes or 0

        confidence = self._calculate_fusion_confidence(signals)

        calibrated = self._get_calibrated_accuracy(org_id, confidence)

        variance_factor = 0.2
        impact_range = (
            time_to_impact * (1 - variance_factor),
            time_to_impact * (1 + variance_factor),
        )

        prediction = FusedPrediction(
            prediction_id=f"pred_{org_id}_{service}_{datetime.now(timezone.utc).timestamp()}",
            service=service,
            predicted_event=predicted_event,
            confidence=confidence,
            calibrated_accuracy=calibrated,
            signals=signals,
            time_to_impact_minutes=time_to_impact,
            impact_range_minutes=impact_range,
        )

        self._predictions[prediction.prediction_id] = prediction

        log.info(
            "Created fused prediction: service=%s event=%s confidence=%.2f tti=%.1fm",
            service,
            predicted_event,
            confidence,
            time_to_impact,
        )

        return prediction

    def _calculate_fusion_confidence(
        self,
        signals: list[CorrelatedSignal],
    ) -> float:
        """Calculate fused confidence from multiple signals."""
        if not signals:
            return 0

        base_confidence = 0.5

        critical_count = sum(1 for s in signals if s.is_critical)
        critical_boost = min(0.3, critical_count * 0.1)

        correlation_boost = 0.0
        for signal in signals[1:5]:
            if signal.correlation_coefficient > 0.7:
                correlation_boost += 0.05
            elif signal.correlation_coefficient > 0.5:
                correlation_boost += 0.03

        increasing_count = sum(
            1 for s in signals if s.trend == SignalTrend.INCREASING
        )
        trend_boost = min(0.15, increasing_count * 0.03)

        confidence = base_confidence + critical_boost + correlation_boost + trend_boost

        return min(0.99, confidence)

    def _get_calibrated_accuracy(
        self,
        org_id: str,
        confidence: float,
    ) -> float:
        """Get calibrated accuracy based on historical performance."""
        history = self._historical_accuracy.get(org_id, [])

        if len(history) < 10:
            return confidence * 0.9

        recent = history[-50:]
        actual_accuracy = sum(1 for r in recent if r) / len(recent)

        return actual_accuracy

    def record_outcome(
        self,
        org_id: str,
        prediction_id: str,
        was_correct: bool,
    ) -> None:
        """Record whether a prediction was correct."""
        if org_id not in self._historical_accuracy:
            self._historical_accuracy[org_id] = []

        self._historical_accuracy[org_id].append(was_correct)

        if len(self._historical_accuracy[org_id]) > 200:
            self._historical_accuracy[org_id] = (
                self._historical_accuracy[org_id][-100:]
            )

        log.info(
            "Recorded prediction outcome: prediction=%s correct=%s",
            prediction_id,
            was_correct,
        )

    def get_prediction(self, prediction_id: str) -> FusedPrediction | None:
        """Get a prediction by ID."""
        return self._predictions.get(prediction_id)

    def get_active_predictions(
        self,
        org_id: str,
        service: str | None = None,
    ) -> list[FusedPrediction]:
        """Get all active predictions for an org."""
        predictions = []

        for pred in self._predictions.values():
            if not pred.prediction_id.startswith(f"pred_{org_id}_"):
                continue

            if service and pred.service != service:
                continue

            age_minutes = (
                datetime.now(timezone.utc) - pred.created_at
            ).total_seconds() / 60

            if age_minutes < pred.time_to_impact_minutes + 30:
                predictions.append(pred)

        return predictions


_fusion: MultiSignalFusion | None = None


def get_multi_signal_fusion() -> MultiSignalFusion:
    """Get the default multi-signal fusion instance."""
    global _fusion
    if _fusion is None:
        _fusion = MultiSignalFusion()
    return _fusion
