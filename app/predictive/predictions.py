"""Incident prediction with causal chain analysis.

Predicts incidents before they happen by analyzing signal patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.predictive.signals import (
    MetricSignal,
    SignalCorrelator,
    SignalTrend,
    get_signal_correlator,
)

log = logging.getLogger("devcopilot.predictive.predictions")


class PredictionConfidence(Enum):
    """Confidence level of a prediction."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"

    @property
    def threshold(self) -> float:
        return {
            PredictionConfidence.LOW: 0.5,
            PredictionConfidence.MEDIUM: 0.7,
            PredictionConfidence.HIGH: 0.85,
            PredictionConfidence.VERY_HIGH: 0.95,
        }.get(self, 0.5)


class IncidentType(Enum):
    """Type of predicted incident."""

    SERVICE_OUTAGE = "service_outage"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    CASCADE_FAILURE = "cascade_failure"
    DATA_ISSUE = "data_issue"
    NETWORK_ISSUE = "network_issue"
    UNKNOWN = "unknown"


@dataclass
class CausalStep:
    """A step in the causal chain leading to an incident."""

    step_number: int
    signal: MetricSignal
    description: str
    time_offset: timedelta
    probability: float
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "signal_type": self.signal.signal_type.value,
            "service": self.signal.service,
            "current_value": self.signal.current_value,
            "threshold": self.signal.threshold_critical,
            "description": self.description,
            "time_offset_minutes": self.time_offset.total_seconds() / 60,
            "probability": self.probability,
            "evidence": self.evidence,
        }


@dataclass
class CausalChain:
    """The causal chain of events leading to a predicted incident."""

    chain_id: str
    steps: list[CausalStep]
    root_cause_signal: MetricSignal
    root_cause_description: str
    total_probability: float

    @property
    def estimated_time_to_incident(self) -> timedelta:
        if not self.steps:
            return timedelta(hours=0)
        return sum((s.time_offset for s in self.steps), timedelta())

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "steps": [s.to_dict() for s in self.steps],
            "root_cause_service": self.root_cause_signal.service,
            "root_cause_signal": self.root_cause_signal.signal_type.value,
            "root_cause_description": self.root_cause_description,
            "total_probability": self.total_probability,
            "estimated_minutes_to_incident": (
                self.estimated_time_to_incident.total_seconds() / 60
            ),
        }


@dataclass
class PredictedIncident:
    """A predicted incident before it happens."""

    prediction_id: str
    incident_type: IncidentType
    affected_services: list[str]
    confidence: PredictionConfidence
    confidence_score: float
    predicted_at: datetime
    expected_incident_time: datetime
    causal_chain: CausalChain
    signals: list[MetricSignal]
    similar_past_incidents: list[str] = field(default_factory=list)
    acknowledged: bool = False
    prevented: bool = False

    @property
    def time_until_incident(self) -> timedelta:
        now = datetime.now(timezone.utc)
        return max(timedelta(0), self.expected_incident_time - now)

    @property
    def severity_estimate(self) -> str:
        if self.confidence_score >= 0.9 and len(self.affected_services) >= 3:
            return "SEV1"
        elif self.confidence_score >= 0.8 or len(self.affected_services) >= 2:
            return "SEV2"
        elif self.confidence_score >= 0.6:
            return "SEV3"
        return "SEV4"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "incident_type": self.incident_type.value,
            "affected_services": self.affected_services,
            "confidence": self.confidence.value,
            "confidence_score": round(self.confidence_score, 2),
            "predicted_at": self.predicted_at.isoformat(),
            "expected_incident_time": self.expected_incident_time.isoformat(),
            "time_until_incident_minutes": (
                self.time_until_incident.total_seconds() / 60
            ),
            "causal_chain": self.causal_chain.to_dict(),
            "severity_estimate": self.severity_estimate,
            "similar_past_incidents": self.similar_past_incidents,
            "acknowledged": self.acknowledged,
            "prevented": self.prevented,
        }


class IncidentPredictor:
    """Predicts incidents by analyzing signal patterns."""

    def __init__(
        self,
        correlator: SignalCorrelator | None = None,
        min_confidence: float = 0.6,
    ) -> None:
        self.correlator = correlator or get_signal_correlator()
        self.min_confidence = min_confidence
        self._predictions: dict[str, PredictedIncident] = {}
        self._prediction_counter = 0
        self._chain_counter = 0
        self._incident_patterns = self._setup_patterns()

    def _setup_patterns(self) -> dict[str, dict[str, Any]]:
        """Set up known incident patterns."""
        return {
            "memory_leak": {
                "signals": ["memory_usage", "gc_pause"],
                "type": IncidentType.RESOURCE_EXHAUSTION,
                "chain_template": [
                    ("Memory leak detected", 0),
                    ("GC thrashing begins", 45),
                    ("Latency spikes", 90),
                    ("Service outage", 134),
                ],
            },
            "connection_exhaustion": {
                "signals": ["connection_pool", "request_queue"],
                "type": IncidentType.SERVICE_OUTAGE,
                "chain_template": [
                    ("Connection pool filling", 0),
                    ("Request queue growing", 30),
                    ("Timeouts begin", 60),
                    ("Service unavailable", 90),
                ],
            },
            "disk_full": {
                "signals": ["disk_usage"],
                "type": IncidentType.RESOURCE_EXHAUSTION,
                "chain_template": [
                    ("Disk usage critical", 0),
                    ("Write failures", 15),
                    ("Database corruption risk", 30),
                ],
            },
            "cpu_saturation": {
                "signals": ["cpu_usage", "latency_p99"],
                "type": IncidentType.PERFORMANCE_DEGRADATION,
                "chain_template": [
                    ("CPU saturation", 0),
                    ("Request backlog", 20),
                    ("Latency breach", 40),
                    ("SLO violation", 60),
                ],
            },
            "cascade_failure": {
                "signals": ["error_rate", "latency_p99", "connection_pool"],
                "type": IncidentType.CASCADE_FAILURE,
                "chain_template": [
                    ("Primary service degraded", 0),
                    ("Downstream timeouts", 15),
                    ("Cascade propagation", 45),
                    ("Multiple service outage", 90),
                ],
            },
        }

    def analyze_signals(self) -> list[PredictedIncident]:
        """Analyze current signals and generate predictions."""
        predictions: list[PredictedIncident] = []

        critical = self.correlator.get_critical_signals()
        trending = self.correlator.get_trending_signals()
        anomalies = self.correlator.detect_anomalies()

        seen_ids: set[str] = set()
        concerning_signals = []
        for signal in critical + trending + anomalies:
            if signal.signal_id not in seen_ids:
                seen_ids.add(signal.signal_id)
                concerning_signals.append(signal)

        if not concerning_signals:
            return predictions

        for pattern_name, pattern in self._incident_patterns.items():
            matching_signals = self._find_matching_signals(
                concerning_signals,
                pattern["signals"],
            )

            if matching_signals:
                prediction = self._create_prediction(
                    pattern_name,
                    pattern,
                    matching_signals,
                )
                if prediction and prediction.confidence_score >= self.min_confidence:
                    predictions.append(prediction)
                    self._predictions[prediction.prediction_id] = prediction

        predictions.sort(key=lambda p: p.confidence_score, reverse=True)
        return predictions

    def _find_matching_signals(
        self,
        signals: list[MetricSignal],
        required_types: list[str],
    ) -> list[MetricSignal]:
        """Find signals matching the required types for a pattern."""
        matching = []
        for signal in signals:
            if signal.signal_type.value in required_types:
                matching.append(signal)
        return matching

    def _create_prediction(
        self,
        pattern_name: str,
        pattern: dict[str, Any],
        signals: list[MetricSignal],
    ) -> PredictedIncident | None:
        """Create a prediction from a pattern match."""
        if not signals:
            return None

        self._prediction_counter += 1
        prediction_id = f"pred_{self._prediction_counter}"

        affected_services = list(set(s.service for s in signals))
        root_signal = max(signals, key=lambda s: s.severity_score)
        causal_chain = self._build_causal_chain(pattern, signals, root_signal)
        confidence_score = self._calculate_confidence(signals, pattern)

        if confidence_score >= 0.95:
            confidence = PredictionConfidence.VERY_HIGH
        elif confidence_score >= 0.85:
            confidence = PredictionConfidence.HIGH
        elif confidence_score >= 0.7:
            confidence = PredictionConfidence.MEDIUM
        else:
            confidence = PredictionConfidence.LOW

        now = datetime.now(timezone.utc)

        if root_signal.time_to_threshold:
            expected_time = now + root_signal.time_to_threshold
        else:
            expected_time = now + causal_chain.estimated_time_to_incident

        return PredictedIncident(
            prediction_id=prediction_id,
            incident_type=pattern["type"],
            affected_services=affected_services,
            confidence=confidence,
            confidence_score=confidence_score,
            predicted_at=now,
            expected_incident_time=expected_time,
            causal_chain=causal_chain,
            signals=signals,
        )

    def _build_causal_chain(
        self,
        pattern: dict[str, Any],
        signals: list[MetricSignal],
        root_signal: MetricSignal,
    ) -> CausalChain:
        """Build a causal chain from pattern and signals."""
        self._chain_counter += 1
        chain_id = f"chain_{self._chain_counter}"

        steps = []
        template = pattern.get("chain_template", [])

        for i, (description, offset_minutes) in enumerate(template):
            signal = signals[i % len(signals)]
            probability = max(0.5, 1.0 - (i * 0.1))

            step = CausalStep(
                step_number=i + 1,
                signal=signal,
                description=description,
                time_offset=timedelta(minutes=offset_minutes),
                probability=probability,
                evidence=[
                    f"Current {signal.signal_type.value}: {signal.current_value:.1f}",
                    f"Threshold: {signal.threshold_critical:.1f}",
                    f"Trend: {signal.trend.value} ({signal.trend_rate:+.2f}/hr)",
                ],
            )
            steps.append(step)

        return CausalChain(
            chain_id=chain_id,
            steps=steps,
            root_cause_signal=root_signal,
            root_cause_description=self._generate_root_cause_description(root_signal),
            total_probability=self._calculate_chain_probability(steps),
        )

    def _generate_root_cause_description(self, signal: MetricSignal) -> str:
        """Generate human-readable root cause description."""
        descriptions = {
            "memory_usage": f"Memory leak in {signal.service}",
            "connection_pool": f"Connection pool exhaustion in {signal.service}",
            "cpu_usage": f"CPU saturation in {signal.service}",
            "disk_usage": f"Disk space critical on {signal.service}",
            "error_rate": f"Error rate spike in {signal.service}",
            "latency_p99": f"Latency degradation in {signal.service}",
            "gc_pause": f"GC pressure in {signal.service}",
        }
        return descriptions.get(
            signal.signal_type.value,
            f"Anomaly detected in {signal.service}",
        )

    def _calculate_chain_probability(self, steps: list[CausalStep]) -> float:
        """Calculate overall probability of the causal chain."""
        if not steps:
            return 0.0

        probability = 1.0
        for step in steps:
            probability *= step.probability

        return probability

    def _calculate_confidence(
        self,
        signals: list[MetricSignal],
        pattern: dict[str, Any],
    ) -> float:
        """Calculate confidence score for a prediction."""
        if not signals:
            return 0.0

        signal_confidence = sum(
            1.0 if s.severity_score > 70 else 0.7 if s.severity_score > 50 else 0.4
            for s in signals
        ) / len(signals)

        pattern_match = len(signals) / len(pattern.get("signals", [1]))
        pattern_confidence = min(1.0, pattern_match)

        trend_confidence = sum(
            0.9 if s.trend == SignalTrend.INCREASING else 0.5 for s in signals
        ) / len(signals)

        anomaly_confidence = sum(0.8 if s.is_anomalous else 0.5 for s in signals) / len(
            signals
        )

        return (
            signal_confidence * 0.3
            + pattern_confidence * 0.3
            + trend_confidence * 0.2
            + anomaly_confidence * 0.2
        )

    def get_prediction(self, prediction_id: str) -> PredictedIncident | None:
        """Get a prediction by ID."""
        return self._predictions.get(prediction_id)

    def get_active_predictions(self) -> list[PredictedIncident]:
        """Get all active (not prevented, not expired) predictions."""
        now = datetime.now(timezone.utc)
        return [
            p
            for p in self._predictions.values()
            if not p.prevented and p.expected_incident_time > now
        ]

    def acknowledge_prediction(self, prediction_id: str) -> bool:
        """Mark a prediction as acknowledged."""
        prediction = self._predictions.get(prediction_id)
        if prediction:
            prediction.acknowledged = True
            return True
        return False

    def mark_prevented(self, prediction_id: str) -> bool:
        """Mark a prediction as prevented."""
        prediction = self._predictions.get(prediction_id)
        if prediction:
            prediction.prevented = True

            log.info(
                "incident prevented",
                extra={
                    "prediction_id": prediction_id,
                    "incident_type": prediction.incident_type.value,
                    "affected_services": prediction.affected_services,
                },
            )
            return True
        return False

    def get_predictions_for_service(self, service: str) -> list[PredictedIncident]:
        """Get predictions affecting a specific service."""
        return [
            p
            for p in self._predictions.values()
            if service in p.affected_services and not p.prevented
        ]

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the prediction dashboard."""
        active = self.get_active_predictions()
        high_confidence = [p for p in active if p.confidence_score >= 0.85]

        by_type: dict[str, int] = {}
        for p in active:
            t = p.incident_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "active_predictions": len(active),
            "high_confidence_count": len(high_confidence),
            "by_incident_type": by_type,
            "predictions": [p.to_dict() for p in active[:10]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_predictor: IncidentPredictor | None = None


def get_incident_predictor() -> IncidentPredictor:
    """Get the default incident predictor."""
    global _predictor
    if _predictor is None:
        _predictor = IncidentPredictor()
    return _predictor
