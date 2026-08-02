"""Cascade Prediction for Service Dependencies.

Predicts the cascade of failures through service dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.predictive_advanced.cascade_predictor")


class CascadePhase(str, Enum):
    TRIGGER = "trigger"
    PROPAGATING = "propagating"
    AMPLIFYING = "amplifying"
    PEAK = "peak"
    RECOVERING = "recovering"


class ImpactSeverity(str, Enum):
    NONE = "none"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    FULL_OUTAGE = "full_outage"


@dataclass
class ServiceImpact:
    """Impact on a specific service."""

    service: str
    severity: ImpactSeverity
    probability: float
    time_to_impact_minutes: float
    affected_endpoints: list[str]
    users_affected: int
    revenue_at_risk: float
    dependencies: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "severity": self.severity.value,
            "probability": round(self.probability, 2),
            "time_to_impact_minutes": round(self.time_to_impact_minutes, 1),
            "affected_endpoints": self.affected_endpoints,
            "users_affected": self.users_affected,
            "revenue_at_risk": round(self.revenue_at_risk, 2),
            "dependencies": self.dependencies,
        }


@dataclass
class CascadeStep:
    """A step in the cascade prediction."""

    step_number: int
    timestamp_offset_minutes: float
    event: str
    service: str
    trigger: str
    details: str
    impact: ServiceImpact
    phase: CascadePhase
    confidence: float
    is_current: bool = False
    is_predicted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step_number,
            "offset_minutes": round(self.timestamp_offset_minutes, 1),
            "event": self.event,
            "service": self.service,
            "trigger": self.trigger,
            "details": self.details,
            "impact": self.impact.to_dict(),
            "phase": self.phase.value,
            "confidence": round(self.confidence, 2),
            "is_current": self.is_current,
            "is_predicted": self.is_predicted,
        }


@dataclass
class CascadePrediction:
    """Full cascade prediction with timeline."""

    cascade_id: str
    trigger_service: str
    trigger_event: str
    total_services_affected: int
    total_users_affected: int
    total_revenue_at_risk: float
    steps: list[CascadeStep]
    peak_time_minutes: float
    recovery_time_minutes: float
    overall_confidence: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ai_summary: str = ""

    @property
    def current_phase(self) -> CascadePhase:
        for step in self.steps:
            if step.is_current:
                return step.phase
        return CascadePhase.TRIGGER

    def to_dict(self) -> dict[str, Any]:
        return {
            "cascade_id": self.cascade_id,
            "trigger_service": self.trigger_service,
            "trigger_event": self.trigger_event,
            "total_services_affected": self.total_services_affected,
            "total_users_affected": self.total_users_affected,
            "total_revenue_at_risk": round(self.total_revenue_at_risk, 2),
            "steps": [s.to_dict() for s in self.steps],
            "peak_time_minutes": round(self.peak_time_minutes, 1),
            "recovery_time_minutes": round(self.recovery_time_minutes, 1),
            "overall_confidence": round(self.overall_confidence, 2),
            "current_phase": self.current_phase.value,
            "created_at": self.created_at.isoformat(),
            "ai_summary": self.ai_summary,
        }


class CascadePredictor:
    """Predicts cascade failures through service dependencies."""

    def __init__(self) -> None:
        self._service_dependencies: dict[str, list[str]] = {}
        self._service_metrics: dict[str, dict[str, Any]] = {}
        self._cascade_history: dict[str, list[CascadePrediction]] = {}
        self._current_predictions: dict[str, CascadePrediction] = {}

    def register_dependency(
        self,
        org_id: str,
        service: str,
        depends_on: list[str],
    ) -> None:
        """Register service dependencies."""
        key = f"{org_id}:{service}"
        self._service_dependencies[key] = depends_on

        log.info(
            "Registered dependencies: service=%s depends_on=%s",
            service,
            depends_on,
        )

    def set_service_metrics(
        self,
        org_id: str,
        service: str,
        users_per_minute: int,
        revenue_per_minute: float,
        critical_endpoints: list[str],
    ) -> None:
        """Set service business metrics."""
        key = f"{org_id}:{service}"
        self._service_metrics[key] = {
            "users_per_minute": users_per_minute,
            "revenue_per_minute": revenue_per_minute,
            "critical_endpoints": critical_endpoints,
        }

    def predict_cascade(
        self,
        org_id: str,
        trigger_service: str,
        trigger_event: str,
        trigger_details: str,
        time_to_trigger_minutes: float = 0,
    ) -> CascadePrediction:
        """Predict the cascade of failures from a trigger event."""
        cascade_id = f"cascade_{org_id}_{trigger_service}_{datetime.now(timezone.utc).timestamp()}"

        steps: list[CascadeStep] = []
        affected_services: set[str] = set()
        total_users = 0
        total_revenue = 0.0

        trigger_metrics = self._get_service_metrics(org_id, trigger_service)
        trigger_impact = ServiceImpact(
            service=trigger_service,
            severity=ImpactSeverity.FULL_OUTAGE,
            probability=0.95,
            time_to_impact_minutes=time_to_trigger_minutes,
            affected_endpoints=trigger_metrics.get("critical_endpoints", []),
            users_affected=trigger_metrics.get("users_per_minute", 100) * 15,
            revenue_at_risk=trigger_metrics.get("revenue_per_minute", 100) * 15,
            dependencies=[],
        )

        steps.append(CascadeStep(
            step_number=1,
            timestamp_offset_minutes=time_to_trigger_minutes,
            event=trigger_event,
            service=trigger_service,
            trigger="Initial trigger",
            details=trigger_details,
            impact=trigger_impact,
            phase=CascadePhase.TRIGGER,
            confidence=0.95,
            is_current=time_to_trigger_minutes <= 0,
            is_predicted=time_to_trigger_minutes > 0,
        ))

        affected_services.add(trigger_service)
        total_users += trigger_impact.users_affected
        total_revenue += trigger_impact.revenue_at_risk

        downstream = self._find_downstream_services(org_id, trigger_service)
        step_num = 2
        current_offset = time_to_trigger_minutes

        for svc in downstream:
            if svc in affected_services:
                continue

            delay = self._estimate_propagation_delay(org_id, trigger_service, svc)
            offset = current_offset + delay

            metrics = self._get_service_metrics(org_id, svc)
            duration = 15

            impact = ServiceImpact(
                service=svc,
                severity=ImpactSeverity.PARTIAL_OUTAGE,
                probability=0.8,
                time_to_impact_minutes=offset,
                affected_endpoints=metrics.get("critical_endpoints", []),
                users_affected=metrics.get("users_per_minute", 50) * duration,
                revenue_at_risk=metrics.get("revenue_per_minute", 50) * duration,
                dependencies=[trigger_service],
            )

            steps.append(CascadeStep(
                step_number=step_num,
                timestamp_offset_minutes=offset,
                event=f"{svc} degraded",
                service=svc,
                trigger=f"Dependency on {trigger_service} failing",
                details=f"Timeout/errors from {trigger_service} causing cascade",
                impact=impact,
                phase=CascadePhase.PROPAGATING,
                confidence=0.8,
                is_predicted=True,
            ))

            affected_services.add(svc)
            total_users += impact.users_affected
            total_revenue += impact.revenue_at_risk
            step_num += 1

        secondary_downstream = set()
        for svc in downstream:
            secondary = self._find_downstream_services(org_id, svc)
            secondary_downstream.update(secondary)

        for svc in secondary_downstream:
            if svc in affected_services:
                continue

            delay = self._estimate_propagation_delay(org_id, trigger_service, svc)
            offset = current_offset + delay * 1.5

            metrics = self._get_service_metrics(org_id, svc)
            duration = 10

            impact = ServiceImpact(
                service=svc,
                severity=ImpactSeverity.DEGRADED,
                probability=0.6,
                time_to_impact_minutes=offset,
                affected_endpoints=metrics.get("critical_endpoints", [])[:2],
                users_affected=metrics.get("users_per_minute", 30) * duration,
                revenue_at_risk=metrics.get("revenue_per_minute", 30) * duration,
                dependencies=list(downstream & affected_services),
            )

            steps.append(CascadeStep(
                step_number=step_num,
                timestamp_offset_minutes=offset,
                event=f"{svc} impacted",
                service=svc,
                trigger="Secondary cascade",
                details="Indirect impact from upstream failures",
                impact=impact,
                phase=CascadePhase.AMPLIFYING,
                confidence=0.6,
                is_predicted=True,
            ))

            affected_services.add(svc)
            total_users += impact.users_affected
            total_revenue += impact.revenue_at_risk
            step_num += 1

            if step_num > 8:
                break

        peak_offset = max(s.timestamp_offset_minutes for s in steps) if steps else 0

        alerting_step = CascadeStep(
            step_number=step_num,
            timestamp_offset_minutes=peak_offset + 1,
            event="PagerDuty alert triggered",
            service="pagerduty",
            trigger="Error threshold exceeded",
            details="On-call engineer paged",
            impact=ServiceImpact(
                service="alerting",
                severity=ImpactSeverity.NONE,
                probability=0.99,
                time_to_impact_minutes=peak_offset + 1,
                affected_endpoints=[],
                users_affected=0,
                revenue_at_risk=0,
                dependencies=[],
            ),
            phase=CascadePhase.PEAK,
            confidence=0.99,
            is_predicted=True,
        )
        steps.append(alerting_step)

        steps.sort(key=lambda s: s.timestamp_offset_minutes)
        for i, step in enumerate(steps):
            step.step_number = i + 1

        overall_confidence = sum(s.confidence for s in steps) / len(steps) if steps else 0

        cascade = CascadePrediction(
            cascade_id=cascade_id,
            trigger_service=trigger_service,
            trigger_event=trigger_event,
            total_services_affected=len(affected_services),
            total_users_affected=total_users,
            total_revenue_at_risk=total_revenue,
            steps=steps,
            peak_time_minutes=peak_offset,
            recovery_time_minutes=peak_offset + 20,
            overall_confidence=overall_confidence,
            ai_summary=self._generate_summary(
                trigger_service,
                trigger_event,
                affected_services,
                total_users,
                total_revenue,
            ),
        )

        self._current_predictions[cascade_id] = cascade

        log.info(
            "Predicted cascade: trigger=%s services=%d users=%d revenue=$%.2f",
            trigger_service,
            len(affected_services),
            total_users,
            total_revenue,
        )

        return cascade

    def _find_downstream_services(
        self,
        org_id: str,
        service: str,
    ) -> set[str]:
        """Find services that depend on the given service."""
        downstream: set[str] = set()

        for key, deps in self._service_dependencies.items():
            if not key.startswith(f"{org_id}:"):
                continue

            svc = key.split(":")[1]
            if service in deps:
                downstream.add(svc)

        return downstream

    def _estimate_propagation_delay(
        self,
        org_id: str,
        source: str,
        target: str,
    ) -> float:
        """Estimate delay for failure to propagate between services."""
        return 2.0

    def _get_service_metrics(
        self,
        org_id: str,
        service: str,
    ) -> dict[str, Any]:
        """Get business metrics for a service."""
        key = f"{org_id}:{service}"
        return self._service_metrics.get(key, {
            "users_per_minute": 100,
            "revenue_per_minute": 100,
            "critical_endpoints": ["/api/v1"],
        })

    def _generate_summary(
        self,
        trigger_service: str,
        trigger_event: str,
        affected_services: set[str],
        total_users: int,
        total_revenue: float,
    ) -> str:
        """Generate AI summary of cascade prediction."""
        return (
            f"Predicted cascade starting from {trigger_service} ({trigger_event}). "
            f"Will affect {len(affected_services)} services, "
            f"impacting approximately {total_users:,} users and "
            f"putting ${total_revenue:,.0f} at risk. "
            f"Recommend immediate action on {trigger_service} to prevent propagation."
        )

    def get_cascade(self, cascade_id: str) -> CascadePrediction | None:
        """Get a cascade prediction by ID."""
        return self._current_predictions.get(cascade_id)

    def get_services_at_risk(
        self,
        org_id: str,
        service: str,
    ) -> list[dict[str, Any]]:
        """Get services that would be affected if a service fails."""
        downstream = self._find_downstream_services(org_id, service)

        results = []
        for svc in downstream:
            metrics = self._get_service_metrics(org_id, svc)
            results.append({
                "service": svc,
                "dependency_type": "direct",
                "users_at_risk": metrics.get("users_per_minute", 0) * 15,
                "revenue_at_risk": metrics.get("revenue_per_minute", 0) * 15,
            })

        return results

    def update_cascade_progress(
        self,
        cascade_id: str,
        actual_step: int,
    ) -> CascadePrediction | None:
        """Update cascade with actual progress."""
        cascade = self._current_predictions.get(cascade_id)
        if not cascade:
            return None

        for step in cascade.steps:
            step.is_current = step.step_number == actual_step
            step.is_predicted = step.step_number > actual_step

        return cascade

    def mark_cascade_averted(
        self,
        cascade_id: str,
        at_step: int,
        reason: str = "",
    ) -> CascadePrediction | None:
        """Mark a cascade as averted."""
        cascade = self._current_predictions.get(cascade_id)
        if not cascade:
            return None

        org_id = cascade_id.split("_")[1]

        if org_id not in self._cascade_history:
            self._cascade_history[org_id] = []

        self._cascade_history[org_id].append(cascade)

        del self._current_predictions[cascade_id]

        log.info(
            "Cascade averted: cascade=%s at_step=%d reason=%s",
            cascade_id,
            at_step,
            reason,
        )

        return cascade

    def get_cascade_display(
        self,
        cascade_id: str,
    ) -> dict[str, Any] | None:
        """Get cascade formatted for UI display."""
        cascade = self._current_predictions.get(cascade_id)
        if not cascade:
            return None

        timeline = []
        for step in cascade.steps:
            status = "current" if step.is_current else (
                "predicted" if step.is_predicted else "completed"
            )
            timeline.append({
                "step": step.step_number,
                "time": f"+{step.timestamp_offset_minutes:.0f}m",
                "event": step.event,
                "service": step.service,
                "status": status,
                "confidence": f"{step.confidence:.0%}",
            })

        return {
            "cascade_id": cascade.cascade_id,
            "trigger": {
                "service": cascade.trigger_service,
                "event": cascade.trigger_event,
            },
            "summary": {
                "services": cascade.total_services_affected,
                "users": f"{cascade.total_users_affected:,}",
                "revenue": f"${cascade.total_revenue_at_risk:,.0f}",
                "peak_time": f"+{cascade.peak_time_minutes:.0f}m",
            },
            "timeline": timeline,
            "phase": cascade.current_phase.value,
            "ai_summary": cascade.ai_summary,
        }


_predictor: CascadePredictor | None = None


def get_cascade_predictor() -> CascadePredictor:
    """Get the default cascade predictor."""
    global _predictor
    if _predictor is None:
        _predictor = CascadePredictor()
    return _predictor
