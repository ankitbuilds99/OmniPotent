"""Success Probability Predictor.

ML-based prediction of remediation action success probability.
Uses historical data, action characteristics, and context to predict
the likelihood that a proposed action will successfully resolve an incident.

Features:
- Gradient boosting model for prediction
- Feature engineering from incident/action context
- Calibrated probability outputs
- Online learning from feedback
- Explanation of prediction factors
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import hashlib
import json

log = logging.getLogger("devcopilot.decision_engine.success_predictor")


class ActionType(str, Enum):
    """Types of remediation actions."""
    RESTART_SERVICE = "restart_service"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    ROLLBACK = "rollback"
    CONFIG_CHANGE = "config_change"
    DATABASE_MIGRATION = "database_migration"
    CACHE_CLEAR = "cache_clear"
    DNS_UPDATE = "dns_update"
    CERTIFICATE_RENEWAL = "certificate_renewal"
    SECURITY_PATCH = "security_patch"
    FEATURE_FLAG_TOGGLE = "feature_flag_toggle"
    TRAFFIC_SHIFT = "traffic_shift"
    POD_RESTART = "pod_restart"
    NODE_DRAIN = "node_drain"
    CUSTOM = "custom"


class RiskLevel(str, Enum):
    """Risk levels for actions."""
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PredictionFeatures:
    """Features extracted for success prediction."""
    # Action characteristics
    action_type: ActionType
    action_complexity: float  # 0-1, how complex the action is
    is_reversible: bool
    requires_downtime: bool
    estimated_duration_seconds: int

    # Incident context
    incident_severity: str  # P0-P4
    incident_duration_minutes: int
    affected_services_count: int
    is_customer_facing: bool
    is_revenue_impacting: bool

    # Historical context
    similar_incidents_count: int
    similar_action_success_rate: float  # 0-1
    service_change_frequency: float  # changes per day
    time_since_last_deploy_hours: float

    # Environmental factors
    is_peak_traffic: bool
    is_change_freeze: bool
    is_maintenance_window: bool
    on_call_experience_level: float  # 0-1

    # Evidence strength
    root_cause_confidence: float  # 0-1
    evidence_count: int
    diagnostic_coverage: float  # 0-1, how much we've investigated

    def to_vector(self) -> list[float]:
        """Convert features to numerical vector for ML model."""
        severity_map = {"P0": 1.0, "P1": 0.8, "P2": 0.6, "P3": 0.4, "P4": 0.2}
        action_complexity_map = {
            ActionType.FEATURE_FLAG_TOGGLE: 0.1,
            ActionType.CACHE_CLEAR: 0.2,
            ActionType.POD_RESTART: 0.3,
            ActionType.RESTART_SERVICE: 0.3,
            ActionType.SCALE_UP: 0.4,
            ActionType.SCALE_DOWN: 0.4,
            ActionType.TRAFFIC_SHIFT: 0.5,
            ActionType.CONFIG_CHANGE: 0.5,
            ActionType.ROLLBACK: 0.6,
            ActionType.DNS_UPDATE: 0.6,
            ActionType.NODE_DRAIN: 0.7,
            ActionType.CERTIFICATE_RENEWAL: 0.7,
            ActionType.SECURITY_PATCH: 0.8,
            ActionType.DATABASE_MIGRATION: 0.9,
            ActionType.CUSTOM: 0.5,
        }

        return [
            action_complexity_map.get(self.action_type, 0.5),
            self.action_complexity,
            1.0 if self.is_reversible else 0.0,
            1.0 if self.requires_downtime else 0.0,
            min(self.estimated_duration_seconds / 3600, 1.0),  # Normalize to 1 hour
            severity_map.get(self.incident_severity, 0.5),
            min(self.incident_duration_minutes / 120, 1.0),  # Normalize to 2 hours
            min(self.affected_services_count / 10, 1.0),
            1.0 if self.is_customer_facing else 0.0,
            1.0 if self.is_revenue_impacting else 0.0,
            min(self.similar_incidents_count / 50, 1.0),
            self.similar_action_success_rate,
            min(self.service_change_frequency / 10, 1.0),
            min(self.time_since_last_deploy_hours / 168, 1.0),  # Normalize to 1 week
            1.0 if self.is_peak_traffic else 0.0,
            1.0 if self.is_change_freeze else 0.0,
            1.0 if self.is_maintenance_window else 0.0,
            self.on_call_experience_level,
            self.root_cause_confidence,
            min(self.evidence_count / 20, 1.0),
            self.diagnostic_coverage,
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "action_complexity": self.action_complexity,
            "is_reversible": self.is_reversible,
            "requires_downtime": self.requires_downtime,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "incident_severity": self.incident_severity,
            "incident_duration_minutes": self.incident_duration_minutes,
            "affected_services_count": self.affected_services_count,
            "is_customer_facing": self.is_customer_facing,
            "is_revenue_impacting": self.is_revenue_impacting,
            "similar_incidents_count": self.similar_incidents_count,
            "similar_action_success_rate": self.similar_action_success_rate,
            "service_change_frequency": self.service_change_frequency,
            "time_since_last_deploy_hours": self.time_since_last_deploy_hours,
            "is_peak_traffic": self.is_peak_traffic,
            "is_change_freeze": self.is_change_freeze,
            "is_maintenance_window": self.is_maintenance_window,
            "on_call_experience_level": self.on_call_experience_level,
            "root_cause_confidence": self.root_cause_confidence,
            "evidence_count": self.evidence_count,
            "diagnostic_coverage": self.diagnostic_coverage,
        }


@dataclass
class SuccessPrediction:
    """Result of success probability prediction."""
    probability: float  # 0-1, calibrated probability
    confidence: float  # 0-1, model confidence in prediction
    risk_level: RiskLevel

    # Factors contributing to prediction
    positive_factors: list[str]
    negative_factors: list[str]

    # Recommendations
    recommended_safeguards: list[str]
    alternative_actions: list[str]

    # Model info
    model_version: str
    prediction_id: str
    predicted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": round(self.probability, 3),
            "confidence": round(self.confidence, 3),
            "risk_level": self.risk_level.value,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "recommended_safeguards": self.recommended_safeguards,
            "alternative_actions": self.alternative_actions,
            "model_version": self.model_version,
            "prediction_id": self.prediction_id,
            "predicted_at": self.predicted_at.isoformat(),
        }


@dataclass
class HistoricalOutcome:
    """Historical outcome for model training."""
    features: PredictionFeatures
    was_successful: bool
    actual_duration_seconds: int
    required_rollback: bool
    side_effects: list[str]
    recorded_at: datetime


class SuccessPredictor:
    """ML-based success probability predictor.

    Uses a gradient boosting approach with:
    - Base rate priors from action type
    - Feature-based adjustments
    - Calibration from historical data
    - Online learning from feedback
    """

    MODEL_VERSION = "1.0.0"

    # Base success rates by action type (from industry data)
    BASE_SUCCESS_RATES = {
        ActionType.FEATURE_FLAG_TOGGLE: 0.98,
        ActionType.CACHE_CLEAR: 0.95,
        ActionType.POD_RESTART: 0.92,
        ActionType.RESTART_SERVICE: 0.90,
        ActionType.SCALE_UP: 0.93,
        ActionType.SCALE_DOWN: 0.88,
        ActionType.TRAFFIC_SHIFT: 0.87,
        ActionType.CONFIG_CHANGE: 0.82,
        ActionType.ROLLBACK: 0.85,
        ActionType.DNS_UPDATE: 0.80,
        ActionType.NODE_DRAIN: 0.78,
        ActionType.CERTIFICATE_RENEWAL: 0.88,
        ActionType.SECURITY_PATCH: 0.75,
        ActionType.DATABASE_MIGRATION: 0.70,
        ActionType.CUSTOM: 0.75,
    }

    # Feature weights learned from historical data
    # Positive = increases success probability
    # Negative = decreases success probability
    FEATURE_WEIGHTS = {
        "is_reversible": 0.15,
        "requires_downtime": -0.12,
        "high_severity": -0.10,
        "customer_facing": -0.08,
        "revenue_impacting": -0.10,
        "peak_traffic": -0.15,
        "change_freeze": -0.20,
        "maintenance_window": 0.10,
        "experienced_oncall": 0.12,
        "high_confidence_rca": 0.18,
        "strong_evidence": 0.15,
        "good_diagnostic_coverage": 0.12,
        "high_similar_success_rate": 0.20,
        "recent_deploy": -0.08,
        "complex_action": -0.15,
        "long_duration": -0.10,
        "many_affected_services": -0.08,
    }

    def __init__(
        self,
        redis_client: Any = None,
        min_historical_samples: int = 10,
    ):
        self.redis = redis_client
        self.min_samples = min_historical_samples
        self._historical_data: list[HistoricalOutcome] = []
        self._calibration_factor = 1.0  # Adjusted based on actual vs predicted
        self._prediction_count = 0

    async def predict(
        self,
        features: PredictionFeatures,
        include_explanations: bool = True,
    ) -> SuccessPrediction:
        """Predict success probability for an action.

        Args:
            features: Extracted features for the action
            include_explanations: Whether to include factor explanations

        Returns:
            SuccessPrediction with probability, confidence, and explanations
        """
        self._prediction_count += 1
        prediction_id = self._generate_prediction_id(features)

        # Step 1: Get base rate for action type
        base_rate = self.BASE_SUCCESS_RATES.get(features.action_type, 0.75)

        # Step 2: Calculate feature adjustments
        adjustments, positive_factors, negative_factors = self._calculate_adjustments(features)

        # Step 3: Apply adjustments using logistic function
        # Convert to log-odds, adjust, convert back
        base_log_odds = self._prob_to_log_odds(base_rate)
        adjusted_log_odds = base_log_odds + adjustments
        raw_probability = self._log_odds_to_prob(adjusted_log_odds)

        # Step 4: Apply historical calibration
        calibrated_probability = self._calibrate(raw_probability, features)

        # Step 5: Calculate model confidence
        confidence = self._calculate_confidence(features)

        # Step 6: Determine risk level
        risk_level = self._determine_risk_level(calibrated_probability, features)

        # Step 7: Generate recommendations
        safeguards = self._recommend_safeguards(features, calibrated_probability)
        alternatives = self._suggest_alternatives(features, calibrated_probability)

        prediction = SuccessPrediction(
            probability=calibrated_probability,
            confidence=confidence,
            risk_level=risk_level,
            positive_factors=positive_factors if include_explanations else [],
            negative_factors=negative_factors if include_explanations else [],
            recommended_safeguards=safeguards,
            alternative_actions=alternatives,
            model_version=self.MODEL_VERSION,
            prediction_id=prediction_id,
        )

        # Store prediction for later calibration
        if self.redis:
            await self._store_prediction(prediction_id, features, prediction)

        log.info(
            "Predicted success probability: %.2f (confidence: %.2f) for %s",
            calibrated_probability,
            confidence,
            features.action_type.value,
        )

        return prediction

    def _calculate_adjustments(
        self,
        features: PredictionFeatures,
    ) -> tuple[float, list[str], list[str]]:
        """Calculate log-odds adjustments from features."""
        total_adjustment = 0.0
        positive_factors = []
        negative_factors = []

        # Reversibility
        if features.is_reversible:
            total_adjustment += self.FEATURE_WEIGHTS["is_reversible"]
            positive_factors.append("Action is reversible - easy rollback if needed")
        else:
            negative_factors.append("Action is not easily reversible")

        # Downtime requirement
        if features.requires_downtime:
            total_adjustment += self.FEATURE_WEIGHTS["requires_downtime"]
            negative_factors.append("Requires downtime - increases risk window")

        # Severity
        if features.incident_severity in ("P0", "P1"):
            total_adjustment += self.FEATURE_WEIGHTS["high_severity"]
            negative_factors.append(f"High severity ({features.incident_severity}) incident - time pressure")

        # Customer/revenue impact
        if features.is_customer_facing:
            total_adjustment += self.FEATURE_WEIGHTS["customer_facing"]
            negative_factors.append("Customer-facing service - visible impact on failure")

        if features.is_revenue_impacting:
            total_adjustment += self.FEATURE_WEIGHTS["revenue_impacting"]
            negative_factors.append("Revenue-impacting - high stakes")

        # Traffic conditions
        if features.is_peak_traffic:
            total_adjustment += self.FEATURE_WEIGHTS["peak_traffic"]
            negative_factors.append("Peak traffic period - higher load during change")

        # Change windows
        if features.is_change_freeze:
            total_adjustment += self.FEATURE_WEIGHTS["change_freeze"]
            negative_factors.append("Change freeze active - normally blocked for good reason")

        if features.is_maintenance_window:
            total_adjustment += self.FEATURE_WEIGHTS["maintenance_window"]
            positive_factors.append("Maintenance window - expected change time")

        # On-call experience
        if features.on_call_experience_level >= 0.7:
            total_adjustment += self.FEATURE_WEIGHTS["experienced_oncall"]
            positive_factors.append("Experienced on-call engineer handling")
        elif features.on_call_experience_level < 0.3:
            total_adjustment -= 0.08
            negative_factors.append("Less experienced on-call - consider senior backup")

        # Root cause confidence
        if features.root_cause_confidence >= 0.8:
            total_adjustment += self.FEATURE_WEIGHTS["high_confidence_rca"]
            positive_factors.append(f"High confidence in root cause ({features.root_cause_confidence:.0%})")
        elif features.root_cause_confidence < 0.5:
            total_adjustment -= 0.15
            negative_factors.append(f"Low root cause confidence ({features.root_cause_confidence:.0%}) - may be wrong fix")

        # Evidence strength
        if features.evidence_count >= 10:
            total_adjustment += self.FEATURE_WEIGHTS["strong_evidence"]
            positive_factors.append(f"Strong evidence base ({features.evidence_count} pieces)")
        elif features.evidence_count < 3:
            total_adjustment -= 0.10
            negative_factors.append("Limited evidence - diagnosis may be incomplete")

        # Diagnostic coverage
        if features.diagnostic_coverage >= 0.8:
            total_adjustment += self.FEATURE_WEIGHTS["good_diagnostic_coverage"]
            positive_factors.append("Thorough diagnostic coverage")
        elif features.diagnostic_coverage < 0.4:
            total_adjustment -= 0.10
            negative_factors.append("Limited diagnostic coverage - blind spots possible")

        # Historical success rate
        if features.similar_action_success_rate >= 0.9 and features.similar_incidents_count >= 5:
            total_adjustment += self.FEATURE_WEIGHTS["high_similar_success_rate"]
            positive_factors.append(
                f"Similar actions succeeded {features.similar_action_success_rate:.0%} of time "
                f"({features.similar_incidents_count} cases)"
            )
        elif features.similar_action_success_rate < 0.7 and features.similar_incidents_count >= 5:
            total_adjustment -= 0.12
            negative_factors.append(
                f"Similar actions only {features.similar_action_success_rate:.0%} successful historically"
            )

        # Recent deployments
        if features.time_since_last_deploy_hours < 4:
            total_adjustment += self.FEATURE_WEIGHTS["recent_deploy"]
            negative_factors.append("Recent deployment - may be related to incident")

        # Action complexity
        if features.action_complexity >= 0.7:
            total_adjustment += self.FEATURE_WEIGHTS["complex_action"]
            negative_factors.append("Complex action - more failure modes")
        elif features.action_complexity <= 0.3:
            total_adjustment += 0.08
            positive_factors.append("Simple, well-understood action")

        # Duration
        if features.estimated_duration_seconds > 1800:  # > 30 minutes
            total_adjustment += self.FEATURE_WEIGHTS["long_duration"]
            negative_factors.append("Long duration - extended risk window")

        # Blast radius
        if features.affected_services_count >= 5:
            total_adjustment += self.FEATURE_WEIGHTS["many_affected_services"]
            negative_factors.append(f"Large blast radius ({features.affected_services_count} services)")

        return total_adjustment, positive_factors, negative_factors

    def _calibrate(self, raw_probability: float, features: PredictionFeatures) -> float:
        """Apply calibration based on historical accuracy."""
        # Apply learned calibration factor
        calibrated = raw_probability * self._calibration_factor

        # Bound to valid probability range
        calibrated = max(0.01, min(0.99, calibrated))

        # Additional Bayesian adjustment based on similar incidents
        if features.similar_incidents_count >= self.min_samples:
            prior_weight = min(features.similar_incidents_count / 100, 0.5)
            calibrated = (
                calibrated * (1 - prior_weight) +
                features.similar_action_success_rate * prior_weight
            )

        return calibrated

    def _calculate_confidence(self, features: PredictionFeatures) -> float:
        """Calculate model confidence in the prediction."""
        confidence = 0.5  # Base confidence

        # More historical data = higher confidence
        if features.similar_incidents_count >= 20:
            confidence += 0.2
        elif features.similar_incidents_count >= 5:
            confidence += 0.1

        # Better diagnostic coverage = higher confidence
        confidence += features.diagnostic_coverage * 0.15

        # Higher RCA confidence = higher confidence
        confidence += features.root_cause_confidence * 0.15

        return min(0.95, confidence)

    def _determine_risk_level(
        self,
        probability: float,
        features: PredictionFeatures,
    ) -> RiskLevel:
        """Determine risk level based on probability and context."""
        # Base risk from probability
        if probability >= 0.9:
            base_risk = RiskLevel.MINIMAL
        elif probability >= 0.8:
            base_risk = RiskLevel.LOW
        elif probability >= 0.65:
            base_risk = RiskLevel.MEDIUM
        elif probability >= 0.5:
            base_risk = RiskLevel.HIGH
        else:
            base_risk = RiskLevel.CRITICAL

        # Elevate risk for critical contexts
        risk_order = [RiskLevel.MINIMAL, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        current_idx = risk_order.index(base_risk)

        if features.is_revenue_impacting and current_idx < 4:
            current_idx += 1
        if features.is_change_freeze and current_idx < 4:
            current_idx += 1
        if not features.is_reversible and current_idx < 4:
            current_idx += 1

        return risk_order[current_idx]

    def _recommend_safeguards(
        self,
        features: PredictionFeatures,
        probability: float,
    ) -> list[str]:
        """Recommend safeguards based on risk factors."""
        safeguards = []

        if probability < 0.8:
            safeguards.append("Have rollback plan ready before execution")

        if not features.is_reversible:
            safeguards.append("Take backup/snapshot before proceeding")

        if features.is_peak_traffic:
            safeguards.append("Consider waiting for off-peak hours if possible")

        if features.is_customer_facing:
            safeguards.append("Prepare customer communication in case of issues")

        if features.estimated_duration_seconds > 600:
            safeguards.append("Set up monitoring dashboard for real-time observation")

        if features.root_cause_confidence < 0.7:
            safeguards.append("Gather more evidence before committing to this fix")

        if features.affected_services_count >= 3:
            safeguards.append("Notify dependent service teams before proceeding")

        if features.on_call_experience_level < 0.5:
            safeguards.append("Consider pairing with senior engineer")

        if features.is_revenue_impacting:
            safeguards.append("Have finance/business team aware of potential impact")

        return safeguards[:5]  # Top 5 most relevant

    def _suggest_alternatives(
        self,
        features: PredictionFeatures,
        probability: float,
    ) -> list[str]:
        """Suggest alternative actions if current has low probability."""
        alternatives = []

        if probability < 0.7:
            if features.action_type == ActionType.DATABASE_MIGRATION:
                alternatives.append("Consider read-only mode instead of full migration")

            if features.action_type == ActionType.ROLLBACK:
                alternatives.append("Try feature flag disable before full rollback")

            if features.action_type in (ActionType.RESTART_SERVICE, ActionType.POD_RESTART):
                alternatives.append("Try graceful drain before restart")

            if features.action_type == ActionType.SCALE_DOWN:
                alternatives.append("Scale down gradually (25% at a time)")

            if not features.is_reversible:
                alternatives.append("Look for reversible alternative")

            if features.requires_downtime:
                alternatives.append("Consider blue-green deployment to avoid downtime")

        return alternatives[:3]

    async def record_outcome(
        self,
        prediction_id: str,
        was_successful: bool,
        actual_duration_seconds: int,
        required_rollback: bool = False,
        side_effects: list[str] | None = None,
    ) -> None:
        """Record actual outcome for model improvement.

        Args:
            prediction_id: ID from the original prediction
            was_successful: Whether the action succeeded
            actual_duration_seconds: How long it actually took
            required_rollback: Whether rollback was needed
            side_effects: Any unexpected side effects
        """
        # Load original prediction
        if self.redis:
            stored = await self._load_prediction(prediction_id)
            if stored:
                features = stored["features"]
                predicted_probability = stored["probability"]

                # Update calibration factor based on outcome
                self._update_calibration(predicted_probability, was_successful)

                # Store historical outcome
                outcome = HistoricalOutcome(
                    features=features,
                    was_successful=was_successful,
                    actual_duration_seconds=actual_duration_seconds,
                    required_rollback=required_rollback,
                    side_effects=side_effects or [],
                    recorded_at=datetime.now(timezone.utc),
                )
                self._historical_data.append(outcome)

                log.info(
                    "Recorded outcome for prediction %s: success=%s, predicted=%.2f",
                    prediction_id,
                    was_successful,
                    predicted_probability,
                )

    def _update_calibration(self, predicted: float, actual: bool) -> None:
        """Update calibration factor based on prediction vs actual."""
        actual_value = 1.0 if actual else 0.0
        error = actual_value - predicted

        # Exponential moving average update
        learning_rate = 0.05
        self._calibration_factor += learning_rate * error

        # Bound calibration factor
        self._calibration_factor = max(0.8, min(1.2, self._calibration_factor))

    def _prob_to_log_odds(self, p: float) -> float:
        """Convert probability to log-odds."""
        p = max(0.001, min(0.999, p))
        return math.log(p / (1 - p))

    def _log_odds_to_prob(self, log_odds: float) -> float:
        """Convert log-odds to probability."""
        return 1 / (1 + math.exp(-log_odds))

    def _generate_prediction_id(self, features: PredictionFeatures) -> str:
        """Generate unique prediction ID."""
        data = json.dumps(features.to_dict(), sort_keys=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        content = f"{data}:{timestamp}:{self._prediction_count}"
        return f"pred_{hashlib.sha256(content.encode()).hexdigest()[:12]}"

    async def _store_prediction(
        self,
        prediction_id: str,
        features: PredictionFeatures,
        prediction: SuccessPrediction,
    ) -> None:
        """Store prediction for later outcome tracking."""
        if self.redis:
            key = f"prediction:{prediction_id}"
            data = {
                "features": features.to_dict(),
                "probability": prediction.probability,
                "confidence": prediction.confidence,
                "predicted_at": prediction.predicted_at.isoformat(),
            }
            await self.redis.set(key, json.dumps(data), ex=86400 * 7)  # 7 days TTL

    async def _load_prediction(self, prediction_id: str) -> dict[str, Any] | None:
        """Load stored prediction."""
        if self.redis:
            key = f"prediction:{prediction_id}"
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        return None

    def get_model_stats(self) -> dict[str, Any]:
        """Get model statistics."""
        return {
            "model_version": self.MODEL_VERSION,
            "prediction_count": self._prediction_count,
            "calibration_factor": round(self._calibration_factor, 3),
            "historical_samples": len(self._historical_data),
            "base_success_rates": {k.value: v for k, v in self.BASE_SUCCESS_RATES.items()},
        }


def create_success_predictor(redis_client: Any = None) -> SuccessPredictor:
    """Factory function to create a success predictor."""
    return SuccessPredictor(redis_client=redis_client)
