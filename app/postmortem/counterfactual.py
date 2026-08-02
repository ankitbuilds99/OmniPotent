"""Counterfactual analysis for postmortems.

Analyzes "what if" scenarios to identify prevention opportunities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.postmortem.counterfactual")


class CounterfactualType(Enum):
    """Type of counterfactual scenario."""

    TEST_COVERAGE = "test_coverage"
    CANARY_DEPLOY = "canary_deploy"
    TYPE_CHECKING = "type_checking"
    ALERT_THRESHOLD = "alert_threshold"
    CODE_REVIEW = "code_review"
    STAGING_PARITY = "staging_parity"
    CIRCUIT_BREAKER = "circuit_breaker"
    RATE_LIMITING = "rate_limiting"
    FEATURE_FLAG = "feature_flag"
    ROLLBACK_AUTOMATION = "rollback_automation"


class CounterfactualValue(Enum):
    """Value assessment of a counterfactual."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass
class CounterfactualOutcome:
    """Predicted outcome of a counterfactual scenario."""

    prevented: bool
    duration_reduction_minutes: int
    impact_reduction_percent: float
    customers_saved: int
    revenue_saved: float
    detection_improvement_seconds: int


@dataclass
class Counterfactual:
    """A "what if" scenario for an incident."""

    counterfactual_id: str
    counterfactual_type: CounterfactualType
    question: str
    description: str
    outcome: CounterfactualOutcome
    investment_hours: float
    value: CounterfactualValue
    recommendation: str
    confidence: float
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counterfactual_id": self.counterfactual_id,
            "type": self.counterfactual_type.value,
            "question": self.question,
            "description": self.description,
            "outcome": {
                "prevented": self.outcome.prevented,
                "duration_reduction_minutes": self.outcome.duration_reduction_minutes,
                "impact_reduction_percent": self.outcome.impact_reduction_percent,
                "customers_saved": self.outcome.customers_saved,
                "revenue_saved": self.outcome.revenue_saved,
                "detection_improvement_seconds": self.outcome.detection_improvement_seconds,
            },
            "investment_hours": self.investment_hours,
            "value": self.value.value,
            "recommendation": self.recommendation,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
        }


@dataclass
class ContributingFactor:
    """A factor that contributed to the incident."""

    factor_id: str
    factor_type: str
    title: str
    description: str
    category: str
    is_root_cause: bool = False
    leads_to: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "factor_type": self.factor_type,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "is_root_cause": self.is_root_cause,
            "leads_to": self.leads_to,
            "evidence": self.evidence,
        }


@dataclass
class CausalGraph:
    """Graph of contributing factors and their causal relationships."""

    factors: list[ContributingFactor]
    trigger: ContributingFactor | None
    root_cause: ContributingFactor | None
    outcome_description: str
    outcome_metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "factors": [f.to_dict() for f in self.factors],
            "trigger": self.trigger.to_dict() if self.trigger else None,
            "root_cause": self.root_cause.to_dict() if self.root_cause else None,
            "outcome_description": self.outcome_description,
            "outcome_metrics": self.outcome_metrics,
        }


class CounterfactualAnalyzer:
    """Analyzes counterfactual scenarios for incidents."""

    def __init__(self) -> None:
        self._counter = 0
        self._factor_counter = 0
        self._scenario_templates = self._setup_scenario_templates()

    def _setup_scenario_templates(self) -> dict[str, dict[str, Any]]:
        """Set up counterfactual scenario templates."""
        return {
            "null_check": {
                "type": CounterfactualType.TEST_COVERAGE,
                "question": "What if we had a test for null/undefined handling?",
                "description": "A test case checking for null input would have caught this in CI",
                "prevented": True,
                "detection_point": "CI pipeline (before merge)",
                "investment_hours": 2.0,
            },
            "type_checking": {
                "type": CounterfactualType.TYPE_CHECKING,
                "question": "What if strict type checking was enabled?",
                "description": "Static type analysis would have flagged the type mismatch at build time",
                "prevented": True,
                "detection_point": "IDE/build time",
                "investment_hours": 8.0,
            },
            "canary_deploy": {
                "type": CounterfactualType.CANARY_DEPLOY,
                "question": "What if we had used canary deployment?",
                "description": "Canary would have limited blast radius to a small percentage of traffic",
                "prevented": False,
                "impact_reduction": 0.9,
                "investment_hours": 1.0,
            },
            "faster_alerts": {
                "type": CounterfactualType.ALERT_THRESHOLD,
                "question": "What if alert threshold was lower?",
                "description": "Earlier alerting would have reduced detection time",
                "prevented": False,
                "detection_improvement_seconds": 120,
                "investment_hours": 0.5,
            },
            "code_review_checklist": {
                "type": CounterfactualType.CODE_REVIEW,
                "question": "What if code review had a checklist for this type of change?",
                "description": "A review checklist would have prompted checking edge cases",
                "prevented": True,
                "detection_point": "Code review",
                "investment_hours": 4.0,
            },
            "staging_parity": {
                "type": CounterfactualType.STAGING_PARITY,
                "question": "What if staging had realistic test data?",
                "description": "Testing with realistic data would have exposed the edge case",
                "prevented": True,
                "detection_point": "Staging environment",
                "investment_hours": 4.0,
            },
            "circuit_breaker": {
                "type": CounterfactualType.CIRCUIT_BREAKER,
                "question": "What if circuit breakers were in place?",
                "description": "Circuit breakers would have prevented cascade failures",
                "prevented": False,
                "impact_reduction": 0.7,
                "investment_hours": 16.0,
            },
            "feature_flag": {
                "type": CounterfactualType.FEATURE_FLAG,
                "question": "What if the change was behind a feature flag?",
                "description": "Feature flag would have allowed instant rollback without deploy",
                "prevented": False,
                "duration_reduction_percent": 0.8,
                "investment_hours": 2.0,
            },
            "auto_rollback": {
                "type": CounterfactualType.ROLLBACK_AUTOMATION,
                "question": "What if automatic rollback was configured?",
                "description": "Auto-rollback on error spike would have limited incident duration",
                "prevented": False,
                "duration_reduction_percent": 0.6,
                "investment_hours": 4.0,
            },
        }

    def analyze(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        duration_minutes: int,
        customers_affected: int = 0,
        revenue_impact: float = 0.0,
    ) -> list[Counterfactual]:
        """Analyze counterfactual scenarios for an incident."""
        counterfactuals: list[Counterfactual] = []

        root_cause_lower = root_cause.lower()

        if any(word in root_cause_lower for word in ["null", "none", "undefined", "attributeerror"]):
            counterfactuals.append(
                self._create_counterfactual(
                    "null_check",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )
            counterfactuals.append(
                self._create_counterfactual(
                    "type_checking",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        if incident_data.get("triggered_by_deploy", False) or "deploy" in root_cause_lower:
            counterfactuals.append(
                self._create_counterfactual(
                    "canary_deploy",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )
            counterfactuals.append(
                self._create_counterfactual(
                    "feature_flag",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )
            counterfactuals.append(
                self._create_counterfactual(
                    "auto_rollback",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        ttd = incident_data.get("time_to_detect_minutes", 5)
        if ttd > 2:
            counterfactuals.append(
                self._create_counterfactual(
                    "faster_alerts",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        if any(word in root_cause_lower for word in ["code review", "missed", "overlooked"]):
            counterfactuals.append(
                self._create_counterfactual(
                    "code_review_checklist",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        if any(word in root_cause_lower for word in ["staging", "test environment", "edge case"]):
            counterfactuals.append(
                self._create_counterfactual(
                    "staging_parity",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        if any(word in root_cause_lower for word in ["cascade", "timeout", "dependency", "downstream"]):
            counterfactuals.append(
                self._create_counterfactual(
                    "circuit_breaker",
                    duration_minutes,
                    customers_affected,
                    revenue_impact,
                )
            )

        counterfactuals.sort(key=lambda c: (
            0 if c.value == CounterfactualValue.HIGH else
            1 if c.value == CounterfactualValue.MEDIUM else
            2
        ))

        return counterfactuals

    def _create_counterfactual(
        self,
        template_key: str,
        duration_minutes: int,
        customers_affected: int,
        revenue_impact: float,
    ) -> Counterfactual:
        """Create a counterfactual from a template."""
        self._counter += 1
        template = self._scenario_templates[template_key]

        prevented = template.get("prevented", False)
        impact_reduction = template.get("impact_reduction", 0.0)
        duration_reduction_percent = template.get("duration_reduction_percent", 0.0)
        detection_improvement = template.get("detection_improvement_seconds", 0)

        if prevented:
            duration_reduction = duration_minutes
            customers_saved = customers_affected
            revenue_saved = revenue_impact
            impact_reduction_pct = 100.0
        else:
            if impact_reduction > 0:
                duration_reduction = int(duration_minutes * impact_reduction)
                customers_saved = int(customers_affected * impact_reduction)
                revenue_saved = revenue_impact * impact_reduction
                impact_reduction_pct = impact_reduction * 100
            elif duration_reduction_percent > 0:
                duration_reduction = int(duration_minutes * duration_reduction_percent)
                customers_saved = int(customers_affected * duration_reduction_percent)
                revenue_saved = revenue_impact * duration_reduction_percent
                impact_reduction_pct = duration_reduction_percent * 100
            else:
                duration_reduction = 0
                customers_saved = 0
                revenue_saved = 0.0
                impact_reduction_pct = 0.0

        outcome = CounterfactualOutcome(
            prevented=prevented,
            duration_reduction_minutes=duration_reduction,
            impact_reduction_percent=impact_reduction_pct,
            customers_saved=customers_saved,
            revenue_saved=revenue_saved,
            detection_improvement_seconds=detection_improvement,
        )

        investment = template.get("investment_hours", 1.0)

        if prevented or impact_reduction_pct >= 80:
            value = CounterfactualValue.HIGH
            recommendation = f"Recommended action item - {template['description']}"
        elif impact_reduction_pct >= 50:
            value = CounterfactualValue.MEDIUM
            recommendation = f"Consider implementing - {template['description']}"
        elif detection_improvement > 60:
            value = CounterfactualValue.MEDIUM
            recommendation = f"Would improve detection by {detection_improvement}s"
        else:
            value = CounterfactualValue.LOW
            recommendation = "Lower priority improvement"

        evidence = []
        if prevented:
            evidence.append(f"Detection point: {template.get('detection_point', 'Earlier in pipeline')}")
            evidence.append("Customer impact: Zero (incident prevented)")
        if impact_reduction_pct > 0:
            evidence.append(f"Impact reduction: {impact_reduction_pct:.0f}%")
        if customers_saved > 0:
            evidence.append(f"Customers saved: {customers_saved}")
        if revenue_saved > 0:
            evidence.append(f"Revenue saved: ${revenue_saved:,.0f}")

        return Counterfactual(
            counterfactual_id=f"cf_{self._counter}",
            counterfactual_type=template["type"],
            question=template["question"],
            description=template["description"],
            outcome=outcome,
            investment_hours=investment,
            value=value,
            recommendation=recommendation,
            confidence=0.85 if prevented else 0.7,
            evidence=evidence,
        )

    def build_causal_graph(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        contributing_factors: list[str],
    ) -> CausalGraph:
        """Build a causal graph showing how factors led to the incident."""
        factors: list[ContributingFactor] = []

        trigger_title = incident_data.get("trigger", "Unknown trigger")
        self._factor_counter += 1
        trigger = ContributingFactor(
            factor_id=f"factor_{self._factor_counter}",
            factor_type="trigger",
            title=trigger_title,
            description="The event that initiated the incident",
            category="trigger",
            is_root_cause=False,
        )
        factors.append(trigger)

        self._factor_counter += 1
        root = ContributingFactor(
            factor_id=f"factor_{self._factor_counter}",
            factor_type="technical_cause",
            title=root_cause[:100] if root_cause else "Unknown root cause",
            description=root_cause,
            category="technical",
            is_root_cause=True,
        )
        factors.append(root)
        trigger.leads_to.append(root.factor_id)

        for cf in contributing_factors:
            self._factor_counter += 1

            category = "process"
            if any(word in cf.lower() for word in ["code", "bug", "error", "null"]):
                category = "technical"
            elif any(word in cf.lower() for word in ["review", "checklist", "process"]):
                category = "process"
            elif any(word in cf.lower() for word in ["test", "coverage", "staging"]):
                category = "testing"

            factor = ContributingFactor(
                factor_id=f"factor_{self._factor_counter}",
                factor_type="contributing",
                title=cf[:80],
                description=cf,
                category=category,
            )
            factors.append(factor)
            trigger.leads_to.append(factor.factor_id)
            factor.leads_to.append(root.factor_id)

        outcome_metrics = {
            "errors": incident_data.get("error_count", 0),
            "customers_affected": incident_data.get("customers_affected", 0),
            "revenue_impact": incident_data.get("revenue_impact", 0),
            "duration_minutes": incident_data.get("duration_minutes", 0),
        }

        return CausalGraph(
            factors=factors,
            trigger=trigger,
            root_cause=root,
            outcome_description=incident_data.get("impact", "Service degradation"),
            outcome_metrics=outcome_metrics,
        )


_analyzer: CounterfactualAnalyzer | None = None


def get_counterfactual_analyzer() -> CounterfactualAnalyzer:
    """Get the default counterfactual analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = CounterfactualAnalyzer()
    return _analyzer
