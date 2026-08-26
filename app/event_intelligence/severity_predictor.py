"""Severity Predictor.

ML-based severity prediction for incidents based on:
- Historical incident patterns
- Service criticality
- Time of day/week patterns
- Blast radius estimation
- Current system state
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("omnipotent.event_intelligence.severity_predictor")


class PredictedSeverity(str, Enum):
    """Predicted severity levels."""
    SEV1 = "sev1"  # Critical - immediate response required
    SEV2 = "sev2"  # High - response within 15 minutes
    SEV3 = "sev3"  # Medium - response within 1 hour
    SEV4 = "sev4"  # Low - response within 4 hours
    SEV5 = "sev5"  # Informational - no response required


@dataclass
class SeverityFactors:
    """Factors contributing to severity prediction."""
    service_criticality: float  # 0-1
    blast_radius_score: float  # 0-1
    time_sensitivity: float  # 0-1 (higher during business hours)
    historical_pattern_score: float  # 0-1
    error_rate_impact: float  # 0-1
    user_impact_score: float  # 0-1
    revenue_impact: bool
    customer_facing: bool
    slo_breach: bool
    alert_type_score: float = 0.5  # 0-1, from ALERT_TYPE_WEIGHTS


@dataclass
class SeverityPrediction:
    """Output of severity prediction."""
    incident_id: str
    predicted_severity: PredictedSeverity
    confidence: float
    factors: SeverityFactors
    reasoning: str
    recommended_response_time_minutes: int
    escalation_recommended: bool
    predicted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "predicted_severity": self.predicted_severity.value,
            "confidence": self.confidence,
            "factors": {
                "service_criticality": self.factors.service_criticality,
                "blast_radius_score": self.factors.blast_radius_score,
                "time_sensitivity": self.factors.time_sensitivity,
                "historical_pattern_score": self.factors.historical_pattern_score,
                "error_rate_impact": self.factors.error_rate_impact,
                "user_impact_score": self.factors.user_impact_score,
                "alert_type_score": self.factors.alert_type_score,
                "revenue_impact": self.factors.revenue_impact,
                "customer_facing": self.factors.customer_facing,
                "slo_breach": self.factors.slo_breach,
            },
            "reasoning": self.reasoning,
            "recommended_response_time_minutes": self.recommended_response_time_minutes,
            "escalation_recommended": self.escalation_recommended,
            "predicted_at": self.predicted_at.isoformat(),
        }


@dataclass
class IncidentContext:
    """Context for severity prediction."""
    incident_id: str
    service: str
    alert_type: str
    error_rate: Optional[float] = None
    latency_p99: Optional[float] = None
    affected_endpoints: list[str] = field(default_factory=list)
    related_alerts_count: int = 0
    current_slo_status: Optional[dict[str, Any]] = None
    blast_radius: Optional[dict[str, Any]] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SeverityPredictor:
    """Predicts incident severity using multiple signals.

    Uses a weighted scoring system that considers:
    1. Service criticality (from service catalog)
    2. Blast radius (affected downstream services)
    3. Time-based patterns (business hours, on-call load)
    4. Historical incident patterns
    5. Real-time metrics (error rates, latency)
    6. SLO status and error budget
    """

    # Service criticality weights
    CRITICAL_SERVICES = {
        "api-gateway": 1.0,
        "auth": 0.95,
        "payment": 0.95,
        "checkout": 0.9,
        "billing": 0.9,
        "user": 0.8,
        "order": 0.85,
        "inventory": 0.75,
        "notification": 0.4,
        "analytics": 0.3,
        "recommendation": 0.5,
    }

    # Response time targets by severity (minutes)
    RESPONSE_TIMES = {
        PredictedSeverity.SEV1: 5,
        PredictedSeverity.SEV2: 15,
        PredictedSeverity.SEV3: 60,
        PredictedSeverity.SEV4: 240,
        PredictedSeverity.SEV5: 1440,
    }

    # Alert type severity weights
    ALERT_TYPE_WEIGHTS = {
        "down": 1.0,
        "error_rate": 0.85,
        "latency": 0.6,
        "saturation": 0.7,
        "memory": 0.5,
        "cpu": 0.4,
        "disk": 0.45,
        "certificate": 0.8,
        "security": 0.95,
    }

    def __init__(
        self,
        service_catalog: Optional[dict[str, Any]] = None,
        historical_data: Optional[list[dict[str, Any]]] = None,
    ):
        self.service_catalog = service_catalog or {}
        self.historical_data = historical_data or []
        self._prediction_history: list[dict[str, Any]] = []

    async def predict(self, context: IncidentContext) -> SeverityPrediction:
        """Predict severity for an incident."""

        # Calculate all factors
        factors = await self._calculate_factors(context)

        # Calculate weighted severity score
        score = self._calculate_severity_score(factors)

        # Map score to severity level
        severity = self._score_to_severity(score)

        # Generate reasoning
        reasoning = self._generate_reasoning(factors, severity)

        # Determine if escalation is needed
        escalation = self._should_escalate(factors, severity)

        prediction = SeverityPrediction(
            incident_id=context.incident_id,
            predicted_severity=severity,
            confidence=self._calculate_confidence(factors),
            factors=factors,
            reasoning=reasoning,
            recommended_response_time_minutes=self.RESPONSE_TIMES[severity],
            escalation_recommended=escalation,
        )

        # Record for learning
        self._prediction_history.append({
            "incident_id": context.incident_id,
            "predicted": severity.value,
            "score": score,
            "timestamp": datetime.now(timezone.utc),
        })

        return prediction

    async def _calculate_factors(self, context: IncidentContext) -> SeverityFactors:
        """Calculate all severity factors."""

        # Service criticality
        service_crit = self._get_service_criticality(context.service)

        # Blast radius score
        blast_score = self._calculate_blast_radius_score(context.blast_radius)

        # Time sensitivity
        time_sens = self._calculate_time_sensitivity(context.timestamp)

        # Historical pattern
        hist_score = self._calculate_historical_score(context)

        # Error rate impact
        error_impact = self._calculate_error_rate_impact(context.error_rate)

        # User impact
        user_impact = self._calculate_user_impact(context)

        # Revenue path check
        revenue_impact = self.is_revenue_path(context.service)

        # Customer facing check
        customer_facing = self.is_customer_facing(context.service)

        # SLO breach check
        slo_breach = self._check_slo_breach(context.current_slo_status)

        # What kind of alert this is
        alert_type_score = self._get_alert_type_weight(context.alert_type)

        return SeverityFactors(
            service_criticality=service_crit,
            blast_radius_score=blast_score,
            time_sensitivity=time_sens,
            historical_pattern_score=hist_score,
            error_rate_impact=error_impact,
            user_impact_score=user_impact,
            revenue_impact=revenue_impact,
            customer_facing=customer_facing,
            slo_breach=slo_breach,
            alert_type_score=alert_type_score,
        )

    def _get_alert_type_weight(self, alert_type: str) -> float:
        """How severe this kind of alert is on its own.

        ALERT_TYPE_WEIGHTS shipped with the module but nothing read it, so a
        service being *down* scored exactly the same as a CPU alert on it.
        Substring matching because callers send "error_rate", "high_error_rate"
        and "ErrorRate" for the same thing; an unrecognised type gets 0.5 rather
        than 0, so an unusual name cannot talk the score down.
        """
        key = (alert_type or "").strip().lower().replace("-", "_")
        if key in self.ALERT_TYPE_WEIGHTS:
            return self.ALERT_TYPE_WEIGHTS[key]
        matches = [w for name, w in self.ALERT_TYPE_WEIGHTS.items() if name in key]
        return max(matches) if matches else 0.5

    def _get_service_criticality(self, service: str) -> float:
        """Get service criticality score."""
        # Check service catalog first
        if service in self.service_catalog:
            return self.service_catalog[service].get("criticality", 0.5)

        # Fall back to hardcoded weights
        service_lower = service.lower()
        for key, weight in self.CRITICAL_SERVICES.items():
            if key in service_lower:
                return weight

        return 0.5  # Default medium criticality

    # What an unknown blast radius scores. Named so `_calculate_confidence` can
    # tell "we worked out the blast radius and it is small" from "nobody told us".
    UNKNOWN_BLAST_RADIUS = 0.3

    def _calculate_blast_radius_score(self, blast_radius: Optional[dict[str, Any]]) -> float:
        """Calculate blast radius impact score."""
        if not blast_radius:
            return self.UNKNOWN_BLAST_RADIUS  # Unknown, assume moderate

        affected_pct = blast_radius.get("affected_percentage", 0)
        revenue_affected = blast_radius.get("revenue_path_affected", False)
        customer_facing = blast_radius.get("customer_facing_impact", False)

        score = affected_pct

        if revenue_affected:
            score = max(score, 0.8)
        if customer_facing:
            score = max(score, 0.7)

        return min(score, 1.0)

    def _calculate_time_sensitivity(self, timestamp: datetime) -> float:
        """Calculate time-based sensitivity score."""
        hour = timestamp.hour
        weekday = timestamp.weekday()

        # Business hours (9am - 6pm local time, Mon-Fri)
        is_business_hours = 9 <= hour <= 18 and weekday < 5

        # Peak hours (10am - 2pm)
        is_peak = 10 <= hour <= 14 and weekday < 5

        if is_peak:
            return 1.0
        elif is_business_hours:
            return 0.8
        elif weekday < 5:
            return 0.5
        else:
            return 0.3  # Weekend/night

    def _calculate_historical_score(self, context: IncidentContext) -> float:
        """Calculate score based on historical incident patterns."""
        if not self.historical_data:
            return 0.5

        # Find similar historical incidents
        similar = [
            h for h in self.historical_data
            if h.get("service") == context.service
            or h.get("alert_type") == context.alert_type
        ]

        if not similar:
            return 0.5

        # Calculate average severity of similar incidents
        severity_scores = {
            "sev1": 1.0, "sev2": 0.8, "sev3": 0.6, "sev4": 0.4, "sev5": 0.2
        }

        avg_severity = sum(
            severity_scores.get(h.get("severity", "sev3"), 0.5)
            for h in similar
        ) / len(similar)

        return avg_severity

    def _calculate_error_rate_impact(self, error_rate: Optional[float]) -> float:
        """Calculate impact based on error rate."""
        if error_rate is None:
            return 0.3

        if error_rate >= 50:
            return 1.0
        elif error_rate >= 20:
            return 0.8
        elif error_rate >= 5:
            return 0.6
        elif error_rate >= 1:
            return 0.4
        else:
            return 0.2

    def _calculate_user_impact(self, context: IncidentContext) -> float:
        """Calculate user impact score."""
        score = 0.0

        # Related alerts indicate broader impact
        if context.related_alerts_count >= 10:
            score += 0.3
        elif context.related_alerts_count >= 5:
            score += 0.2
        elif context.related_alerts_count >= 2:
            score += 0.1

        # Affected endpoints
        if len(context.affected_endpoints) >= 5:
            score += 0.3
        elif len(context.affected_endpoints) >= 2:
            score += 0.2
        elif len(context.affected_endpoints) >= 1:
            score += 0.1

        # Error rate contribution
        if context.error_rate and context.error_rate > 10:
            score += 0.2

        # Latency contribution
        if context.latency_p99 and context.latency_p99 > 5000:
            score += 0.2
        elif context.latency_p99 and context.latency_p99 > 1000:
            score += 0.1

        return min(score, 1.0)

    def is_revenue_path(self, service: str) -> bool:
        """Check if service is on revenue-critical path."""
        revenue_services = {
            "checkout", "payment", "billing", "subscription",
            "cart", "order", "pricing", "discount"
        }
        return any(rs in service.lower() for rs in revenue_services)

    def is_customer_facing(self, service: str) -> bool:
        """Check if service is customer-facing."""
        customer_facing = {
            "api-gateway", "frontend", "web", "mobile",
            "app", "portal", "ui", "dashboard"
        }
        return any(cf in service.lower() for cf in customer_facing)

    def _check_slo_breach(self, slo_status: Optional[dict[str, Any]]) -> bool:
        """Check if SLO is breached or at risk."""
        if not slo_status:
            return False

        budget_remaining = slo_status.get("budget_remaining_percent", 100)
        status = slo_status.get("status", "healthy")

        return budget_remaining < 10 or status in ["critical", "exhausted"]

    def _calculate_severity_score(self, factors: SeverityFactors) -> float:
        """Calculate weighted severity score.

        The weights sum to 1.0, so a total outage of a tier-0 service with a
        known blast radius can actually reach the 0.85 SEV1 threshold. Alert
        type is one of the terms; it used to be scored nowhere at all.
        """
        weights = {
            "service_criticality": 0.18,
            "blast_radius": 0.18,
            "error_rate": 0.14,
            "user_impact": 0.14,
            "alert_type": 0.12,
            "time_sensitivity": 0.08,
            "historical": 0.08,
            "binary_factors": 0.08,
        }

        score = 0.0

        score += factors.service_criticality * weights["service_criticality"]
        score += factors.blast_radius_score * weights["blast_radius"]
        score += factors.error_rate_impact * weights["error_rate"]
        score += factors.user_impact_score * weights["user_impact"]
        score += factors.alert_type_score * weights["alert_type"]
        score += factors.time_sensitivity * weights["time_sensitivity"]
        score += factors.historical_pattern_score * weights["historical"]

        # Binary factors
        binary_score = 0.0
        if factors.revenue_impact:
            binary_score += 0.4
        if factors.customer_facing:
            binary_score += 0.3
        if factors.slo_breach:
            binary_score += 0.3
        score += min(binary_score, 1.0) * weights["binary_factors"]

        return score

    def _score_to_severity(self, score: float) -> PredictedSeverity:
        """Map score to severity level."""
        if score >= 0.85:
            return PredictedSeverity.SEV1
        elif score >= 0.65:
            return PredictedSeverity.SEV2
        elif score >= 0.45:
            return PredictedSeverity.SEV3
        elif score >= 0.25:
            return PredictedSeverity.SEV4
        else:
            return PredictedSeverity.SEV5

    def _calculate_confidence(self, factors: SeverityFactors) -> float:
        """Calculate prediction confidence."""
        # Higher confidence when more data is available
        confidence = 0.5

        # Service is known
        if factors.service_criticality != 0.5:
            confidence += 0.1

        # Historical data available
        if factors.historical_pattern_score != 0.5:
            confidence += 0.1

        # Blast radius calculated. `> 0` was always true, because the fallback
        # for "no blast radius supplied" is 0.3 -- so every prediction got this
        # bump for a number nobody had computed.
        if factors.blast_radius_score != self.UNKNOWN_BLAST_RADIUS:
            confidence += 0.1

        # Clear signals
        if factors.revenue_impact or factors.slo_breach:
            confidence += 0.1

        if factors.error_rate_impact > 0.5:
            confidence += 0.1

        return min(confidence, 0.95)

    def _generate_reasoning(self, factors: SeverityFactors, severity: PredictedSeverity) -> str:
        """Generate human-readable reasoning."""
        reasons = []

        if factors.service_criticality >= 0.8:
            reasons.append("Critical service affected")
        if factors.blast_radius_score >= 0.5:
            reasons.append(f"High blast radius ({factors.blast_radius_score*100:.0f}% of services)")
        if factors.revenue_impact:
            reasons.append("Revenue path impacted")
        if factors.slo_breach:
            reasons.append("SLO breach detected")
        if factors.customer_facing:
            reasons.append("Customer-facing service")
        if factors.error_rate_impact >= 0.6:
            reasons.append("High error rate")
        if factors.alert_type_score >= 0.8:
            reasons.append("Severe alert type")
        if factors.time_sensitivity >= 0.8:
            reasons.append("Peak business hours")

        if not reasons:
            reasons.append("Standard incident pattern")

        return f"Predicted {severity.value.upper()}: " + "; ".join(reasons)

    def _should_escalate(self, factors: SeverityFactors, severity: PredictedSeverity) -> bool:
        """Determine if incident should be escalated."""
        # Always escalate SEV1
        if severity == PredictedSeverity.SEV1:
            return True

        # Escalate SEV2 with revenue impact
        if severity == PredictedSeverity.SEV2 and factors.revenue_impact:
            return True

        # Escalate if SLO breach on critical service
        if factors.slo_breach and factors.service_criticality >= 0.8:
            return True

        return False

    def record_actual_severity(
        self,
        incident_id: str,
        actual_severity: PredictedSeverity,
    ) -> None:
        """Record actual severity for learning."""
        # Find prediction in history
        for pred in self._prediction_history:
            if pred["incident_id"] == incident_id:
                pred["actual"] = actual_severity.value
                pred["correct"] = pred["predicted"] == actual_severity.value
                break

    def get_accuracy_stats(self) -> dict[str, Any]:
        """Get prediction accuracy statistics."""
        with_actuals = [p for p in self._prediction_history if "actual" in p]

        if not with_actuals:
            return {"predictions": len(self._prediction_history), "accuracy": None}

        correct = sum(1 for p in with_actuals if p.get("correct", False))

        return {
            "predictions": len(self._prediction_history),
            "with_feedback": len(with_actuals),
            "correct": correct,
            "accuracy": correct / len(with_actuals) if with_actuals else 0,
        }
