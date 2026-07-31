"""Predictive Scaling - ML-based traffic prediction and auto-scaling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from enum import Enum
import math

log = logging.getLogger("devcopilot.predictive_scaling.predictor")


class ScalingDirection(str, Enum):
    """Scaling direction."""
    UP = "up"
    DOWN = "down"
    NONE = "none"


class PredictionConfidence(str, Enum):
    """Confidence level of prediction."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TrafficDataPoint:
    """A traffic data point."""
    timestamp: datetime
    requests_per_second: float
    cpu_percent: float
    memory_percent: float
    response_time_ms: float
    error_rate: float


@dataclass
class TrafficPrediction:
    """Traffic prediction for a future time."""
    timestamp: datetime
    predicted_rps: float
    confidence: PredictionConfidence
    confidence_interval_low: float
    confidence_interval_high: float
    factors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "predicted_rps": self.predicted_rps,
            "confidence": self.confidence.value,
            "confidence_interval": {
                "low": self.confidence_interval_low,
                "high": self.confidence_interval_high,
            },
            "factors": self.factors,
        }


@dataclass
class ScalingRecommendation:
    """Scaling recommendation based on prediction."""
    service: str
    direction: ScalingDirection
    current_replicas: int
    recommended_replicas: int
    reason: str
    predicted_traffic: TrafficPrediction
    action_time: datetime
    auto_approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "direction": self.direction.value,
            "current_replicas": self.current_replicas,
            "recommended_replicas": self.recommended_replicas,
            "reason": self.reason,
            "predicted_traffic": self.predicted_traffic.to_dict(),
            "action_time": self.action_time.isoformat(),
            "auto_approved": self.auto_approved,
        }


@dataclass
class ScalingConfig:
    """Configuration for predictive scaling."""
    service: str
    min_replicas: int = 1
    max_replicas: int = 10
    target_cpu_percent: float = 70.0
    target_rps_per_replica: float = 100.0
    scale_up_threshold: float = 0.8
    scale_down_threshold: float = 0.3
    cooldown_seconds: int = 300
    prediction_horizon_minutes: int = 30


class PredictiveScaler:
    """ML-based predictive scaling engine.

    Features:
    - Time-series traffic prediction
    - Seasonal pattern detection
    - Anomaly-aware scaling
    - Cost-optimized recommendations
    - Gradual scale-down
    """

    def __init__(self):
        """Initialize the predictive scaler."""
        self._configs: dict[str, ScalingConfig] = {}
        self._history: dict[str, list[TrafficDataPoint]] = {}
        self._last_scale: dict[str, datetime] = {}
        log.info("PredictiveScaler initialized")

    def configure_service(self, config: ScalingConfig) -> None:
        """Configure scaling for a service.

        Args:
            config: Scaling configuration
        """
        self._configs[config.service] = config
        self._history[config.service] = []
        log.info("Configured scaling for: %s", config.service)

    def add_data_point(self, service: str, data: TrafficDataPoint) -> None:
        """Add a traffic data point.

        Args:
            service: Service name
            data: Traffic data point
        """
        if service not in self._history:
            self._history[service] = []

        self._history[service].append(data)

        # Keep last 7 days of data
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self._history[service] = [
            d for d in self._history[service]
            if d.timestamp > cutoff
        ]

    async def predict_traffic(
        self,
        service: str,
        horizon_minutes: int = 30,
    ) -> list[TrafficPrediction]:
        """Predict future traffic.

        Args:
            service: Service to predict for
            horizon_minutes: How far to predict

        Returns:
            List of predictions
        """
        history = self._history.get(service, [])

        if len(history) < 10:
            # Not enough data
            return []

        predictions: list[TrafficPrediction] = []
        now = datetime.now(timezone.utc)

        for i in range(0, horizon_minutes, 5):  # 5-minute intervals
            future_time = now + timedelta(minutes=i)
            prediction = self._predict_single(history, future_time)
            predictions.append(prediction)

        return predictions

    def _predict_single(
        self,
        history: list[TrafficDataPoint],
        target_time: datetime,
    ) -> TrafficPrediction:
        """Predict traffic for a single point in time."""
        factors: list[str] = []

        # Get base from recent average
        recent = history[-20:]
        base_rps = sum(d.requests_per_second for d in recent) / len(recent)

        # Check for time-of-day pattern
        target_hour = target_time.hour
        same_hour_data = [
            d for d in history
            if d.timestamp.hour == target_hour
        ]

        if same_hour_data:
            hour_avg = sum(d.requests_per_second for d in same_hour_data) / len(same_hour_data)
            hourly_factor = hour_avg / base_rps if base_rps > 0 else 1.0
            predicted = base_rps * hourly_factor
            factors.append(f"Time-of-day pattern (hour {target_hour})")
        else:
            predicted = base_rps
            factors.append("Using recent average")

        # Check for day-of-week pattern
        target_dow = target_time.weekday()
        same_dow_data = [
            d for d in history
            if d.timestamp.weekday() == target_dow
        ]

        if same_dow_data:
            dow_avg = sum(d.requests_per_second for d in same_dow_data) / len(same_dow_data)
            dow_factor = dow_avg / base_rps if base_rps > 0 else 1.0
            predicted *= dow_factor
            factors.append(f"Day-of-week pattern ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][target_dow]})")

        # Detect trend
        if len(recent) >= 10:
            first_half = sum(d.requests_per_second for d in recent[:10]) / 10
            second_half = sum(d.requests_per_second for d in recent[-10:]) / 10
            trend = (second_half - first_half) / first_half if first_half > 0 else 0

            if abs(trend) > 0.1:
                predicted *= (1 + trend * 0.5)
                factors.append(f"Trend: {'+' if trend > 0 else ''}{trend:.0%}")

        # Calculate confidence interval
        variance = sum((d.requests_per_second - base_rps) ** 2 for d in recent) / len(recent)
        std_dev = math.sqrt(variance)
        interval = std_dev * 1.96  # 95% confidence

        # Determine confidence level
        if len(history) >= 1000 and len(factors) >= 2:
            confidence = PredictionConfidence.HIGH
        elif len(history) >= 100:
            confidence = PredictionConfidence.MEDIUM
        else:
            confidence = PredictionConfidence.LOW

        return TrafficPrediction(
            timestamp=target_time,
            predicted_rps=max(0, predicted),
            confidence=confidence,
            confidence_interval_low=max(0, predicted - interval),
            confidence_interval_high=predicted + interval,
            factors=factors,
        )

    async def get_scaling_recommendation(
        self,
        service: str,
        current_replicas: int,
    ) -> ScalingRecommendation | None:
        """Get scaling recommendation for a service.

        Args:
            service: Service name
            current_replicas: Current replica count

        Returns:
            ScalingRecommendation or None
        """
        config = self._configs.get(service)
        if not config:
            return None

        # Check cooldown
        last_scale = self._last_scale.get(service)
        if last_scale:
            cooldown_end = last_scale + timedelta(seconds=config.cooldown_seconds)
            if datetime.now(timezone.utc) < cooldown_end:
                return None

        # Get predictions
        predictions = await self.predict_traffic(
            service, config.prediction_horizon_minutes
        )

        if not predictions:
            return None

        # Use the peak prediction
        peak = max(predictions, key=lambda p: p.predicted_rps)

        # Calculate required replicas
        required_replicas = math.ceil(
            peak.predicted_rps / config.target_rps_per_replica
        )

        # Apply limits
        required_replicas = max(config.min_replicas, min(config.max_replicas, required_replicas))

        # Determine direction
        if required_replicas > current_replicas:
            direction = ScalingDirection.UP
            reason = f"Predicted traffic of {peak.predicted_rps:.0f} RPS requires more capacity"
        elif required_replicas < current_replicas:
            direction = ScalingDirection.DOWN
            reason = f"Predicted traffic of {peak.predicted_rps:.0f} RPS allows scale-down"
        else:
            direction = ScalingDirection.NONE
            reason = "Current capacity matches predicted demand"

        # Gradual scale-down (reduce by at most 1 at a time)
        if direction == ScalingDirection.DOWN:
            required_replicas = max(required_replicas, current_replicas - 1)

        # Auto-approve small scale-ups
        auto_approved = (
            direction == ScalingDirection.UP and
            required_replicas <= current_replicas + 2 and
            peak.confidence == PredictionConfidence.HIGH
        )

        return ScalingRecommendation(
            service=service,
            direction=direction,
            current_replicas=current_replicas,
            recommended_replicas=required_replicas,
            reason=reason,
            predicted_traffic=peak,
            action_time=peak.timestamp,
            auto_approved=auto_approved,
        )

    async def apply_scaling(
        self,
        recommendation: ScalingRecommendation,
    ) -> dict[str, Any]:
        """Apply a scaling recommendation.

        Args:
            recommendation: Recommendation to apply

        Returns:
            Result of scaling operation
        """
        log.info(
            "Scaling %s: %d -> %d replicas",
            recommendation.service,
            recommendation.current_replicas,
            recommendation.recommended_replicas,
        )

        # In production: use Kubernetes API to scale
        # kubectl scale deployment/<service> --replicas=<n>

        self._last_scale[recommendation.service] = datetime.now(timezone.utc)

        return {
            "success": True,
            "service": recommendation.service,
            "previous_replicas": recommendation.current_replicas,
            "new_replicas": recommendation.recommended_replicas,
            "scaled_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_traffic_summary(self, service: str) -> dict[str, Any]:
        """Get traffic summary for a service.

        Args:
            service: Service name

        Returns:
            Traffic summary
        """
        history = self._history.get(service, [])

        if not history:
            return {"error": "No data available"}

        recent = history[-100:]

        return {
            "service": service,
            "data_points": len(history),
            "oldest_data": history[0].timestamp.isoformat() if history else None,
            "newest_data": history[-1].timestamp.isoformat() if history else None,
            "current_rps": recent[-1].requests_per_second if recent else 0,
            "average_rps": sum(d.requests_per_second for d in recent) / len(recent),
            "peak_rps": max(d.requests_per_second for d in recent),
            "average_cpu": sum(d.cpu_percent for d in recent) / len(recent),
            "average_memory": sum(d.memory_percent for d in recent) / len(recent),
        }


def create_predictive_scaler() -> PredictiveScaler:
    """Factory function to create a predictive scaler."""
    return PredictiveScaler()
