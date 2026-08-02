"""Decision Engine - Core orchestrator.

Combines all decision-making components:
- Success prediction
- Option ranking
- Risk scoring
- Cost estimation
- Impact analysis
- Evidence evaluation

Provides a unified interface for making remediation decisions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import hashlib

from app.decision_engine.success_predictor import (
    SuccessPredictor,
    SuccessPrediction,
    PredictionFeatures,
    ActionType,
    RiskLevel,
)
from app.decision_engine.option_ranker import (
    OptionRanker,
    DecisionOption,
    RankedOption,
    RankingResult,
    RankingStrategy,
    RankingCriteria,
)

log = logging.getLogger("devcopilot.decision_engine")


class DecisionConfidence(str, Enum):
    """Confidence level in the decision."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class DecisionOutcome(str, Enum):
    """Recommended decision outcome."""
    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    REQUIRE_APPROVAL = "require_approval"
    INVESTIGATE_MORE = "investigate_more"
    ESCALATE = "escalate"
    ABORT = "abort"


@dataclass
class DecisionFactor:
    """A factor contributing to the decision."""
    name: str
    value: float  # Normalized 0-1
    weight: float
    impact: str  # positive, negative, neutral
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "weight": round(self.weight, 3),
            "impact": self.impact,
            "explanation": self.explanation,
        }


@dataclass
class EvidenceEvaluation:
    """Evaluation of evidence supporting/opposing the decision."""
    supporting_evidence: list[dict[str, Any]]
    opposing_evidence: list[dict[str, Any]]
    evidence_strength: float  # 0-1
    evidence_coverage: float  # 0-1, how much of the incident is explained
    confidence_from_evidence: float  # 0-1

    def to_dict(self) -> dict[str, Any]:
        return {
            "supporting_count": len(self.supporting_evidence),
            "opposing_count": len(self.opposing_evidence),
            "evidence_strength": round(self.evidence_strength, 3),
            "evidence_coverage": round(self.evidence_coverage, 3),
            "confidence_from_evidence": round(self.confidence_from_evidence, 3),
        }


@dataclass
class CostEstimate:
    """Estimated costs of the decision."""
    direct_cost_usd: float  # Cloud resources, API calls, etc.
    opportunity_cost_usd: float  # Lost revenue during execution
    labor_cost_usd: float  # Engineer time
    total_cost_usd: float
    cost_per_minute_delay: float  # Cost of not acting
    break_even_minutes: float  # When cost of action < cost of inaction

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct_cost_usd": round(self.direct_cost_usd, 2),
            "opportunity_cost_usd": round(self.opportunity_cost_usd, 2),
            "labor_cost_usd": round(self.labor_cost_usd, 2),
            "total_cost_usd": round(self.total_cost_usd, 2),
            "cost_per_minute_delay": round(self.cost_per_minute_delay, 2),
            "break_even_minutes": round(self.break_even_minutes, 1),
        }


@dataclass
class ImpactAnalysis:
    """Analysis of the impact of the decision."""
    blast_radius_services: int
    affected_users_estimate: int
    revenue_at_risk_usd: float
    sla_minutes_at_risk: float
    reputation_impact: str  # minimal, moderate, significant, severe
    regulatory_impact: bool
    impact_score: float  # 0-1, normalized impact

    def to_dict(self) -> dict[str, Any]:
        return {
            "blast_radius_services": self.blast_radius_services,
            "affected_users_estimate": self.affected_users_estimate,
            "revenue_at_risk_usd": round(self.revenue_at_risk_usd, 2),
            "sla_minutes_at_risk": round(self.sla_minutes_at_risk, 1),
            "reputation_impact": self.reputation_impact,
            "regulatory_impact": self.regulatory_impact,
            "impact_score": round(self.impact_score, 3),
        }


@dataclass
class DecisionResult:
    """Complete decision result."""
    decision_id: str
    incident_id: str

    # Primary recommendation
    recommended_option: RankedOption | None
    outcome: DecisionOutcome
    confidence: DecisionConfidence

    # Detailed analysis
    success_prediction: SuccessPrediction | None
    ranking_result: RankingResult | None
    evidence_evaluation: EvidenceEvaluation
    cost_estimate: CostEstimate
    impact_analysis: ImpactAnalysis

    # Decision factors
    factors: list[DecisionFactor]
    composite_score: float  # 0-1, overall decision score

    # Recommendations
    safeguards: list[str]
    prerequisites: list[str]
    post_actions: list[str]

    # Metadata
    reasoning: str
    warnings: list[str]
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "incident_id": self.incident_id,
            "recommended_option": self.recommended_option.to_dict() if self.recommended_option else None,
            "outcome": self.outcome.value,
            "confidence": self.confidence.value,
            "success_prediction": self.success_prediction.to_dict() if self.success_prediction else None,
            "ranking_result": self.ranking_result.to_dict() if self.ranking_result else None,
            "evidence_evaluation": self.evidence_evaluation.to_dict(),
            "cost_estimate": self.cost_estimate.to_dict(),
            "impact_analysis": self.impact_analysis.to_dict(),
            "factors": [f.to_dict() for f in self.factors],
            "composite_score": round(self.composite_score, 3),
            "safeguards": self.safeguards,
            "prerequisites": self.prerequisites,
            "post_actions": self.post_actions,
            "reasoning": self.reasoning,
            "warnings": self.warnings,
            "decided_at": self.decided_at.isoformat(),
        }


@dataclass
class IncidentContext:
    """Context about the incident for decision making."""
    incident_id: str
    title: str
    severity: str  # P0-P4
    status: str

    # Timing
    started_at: datetime
    duration_minutes: int

    # Scope
    affected_services: list[str]
    affected_users_estimate: int
    is_customer_facing: bool
    is_revenue_impacting: bool

    # Investigation
    root_cause_hypothesis: str
    root_cause_confidence: float
    evidence_collected: list[dict[str, Any]]

    # Environment
    is_peak_traffic: bool
    is_change_freeze: bool
    is_maintenance_window: bool
    recent_deployments: list[dict[str, Any]]

    # People
    responders: list[str]
    on_call_experience_level: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "duration_minutes": self.duration_minutes,
            "affected_services": self.affected_services,
            "affected_users_estimate": self.affected_users_estimate,
            "is_customer_facing": self.is_customer_facing,
            "is_revenue_impacting": self.is_revenue_impacting,
            "root_cause_hypothesis": self.root_cause_hypothesis,
            "root_cause_confidence": self.root_cause_confidence,
            "evidence_count": len(self.evidence_collected),
            "is_peak_traffic": self.is_peak_traffic,
            "is_change_freeze": self.is_change_freeze,
            "is_maintenance_window": self.is_maintenance_window,
            "recent_deployments_count": len(self.recent_deployments),
            "responder_count": len(self.responders),
            "on_call_experience_level": self.on_call_experience_level,
        }


class DecisionEngine:
    """Main decision engine orchestrating all decision components.

    Provides a unified interface for:
    1. Evaluating remediation options
    2. Predicting success probability
    3. Ranking options by multiple criteria
    4. Estimating costs and impacts
    5. Making final recommendations
    """

    # Thresholds for decision outcomes
    THRESHOLDS = {
        "proceed_score": 0.75,
        "caution_score": 0.60,
        "approval_score": 0.50,
        "investigate_score": 0.35,
        "abort_score": 0.20,
        "high_confidence": 0.80,
        "medium_confidence": 0.60,
        "evidence_strength_min": 0.50,
    }

    # Default hourly rates for cost estimation
    COST_RATES = {
        "engineer_hourly_usd": 150,
        "revenue_per_user_hourly_usd": 0.10,
        "sla_violation_per_minute_usd": 100,
    }

    def __init__(
        self,
        success_predictor: SuccessPredictor | None = None,
        option_ranker: OptionRanker | None = None,
        redis_client: Any = None,
        cost_rates: dict[str, float] | None = None,
    ):
        self.predictor = success_predictor or SuccessPredictor(redis_client)
        self.ranker = option_ranker or OptionRanker()
        self.redis = redis_client
        self.cost_rates = cost_rates or self.COST_RATES
        self._decision_count = 0

    async def decide(
        self,
        context: IncidentContext,
        options: list[DecisionOption],
        ranking_strategy: RankingStrategy = RankingStrategy.BALANCED,
    ) -> DecisionResult:
        """Make a decision about remediation options.

        Args:
            context: Incident context
            options: Available remediation options
            ranking_strategy: Strategy for ranking options

        Returns:
            DecisionResult with recommendation and analysis
        """
        self._decision_count += 1
        decision_id = self._generate_decision_id(context.incident_id)

        log.info(
            "Making decision for incident %s with %d options",
            context.incident_id,
            len(options),
        )

        # Step 1: Evaluate evidence
        evidence_eval = self._evaluate_evidence(context)

        # Step 2: Calculate impact analysis
        impact_analysis = self._analyze_impact(context)

        # Step 3: Estimate costs
        cost_estimate = self._estimate_costs(context, options)

        # Step 4: Predict success for each option
        predictions = await self._predict_success_for_options(context, options)

        # Update options with predictions
        for option in options:
            if option.option_id in predictions:
                option.success_probability = predictions[option.option_id].probability

        # Step 5: Rank options
        ranking_context = {
            "incident_severity": context.severity,
            "is_revenue_impacting": context.is_revenue_impacting,
            "is_change_freeze": context.is_change_freeze,
            "evidence_strength": evidence_eval.evidence_strength,
        }
        ranking_result = self.ranker.rank(options, ranking_strategy, context=ranking_context)

        # Step 6: Get top option's prediction
        top_prediction = None
        recommended_option = ranking_result.top_recommendation
        if recommended_option and recommended_option.option.option_id in predictions:
            top_prediction = predictions[recommended_option.option.option_id]

        # Step 7: Calculate decision factors
        factors = self._calculate_factors(
            context, evidence_eval, impact_analysis, cost_estimate, top_prediction, recommended_option
        )

        # Step 8: Calculate composite score
        composite_score = self._calculate_composite_score(factors)

        # Step 9: Determine outcome
        outcome = self._determine_outcome(
            composite_score, context, evidence_eval, top_prediction
        )

        # Step 10: Determine confidence
        confidence = self._determine_confidence(
            evidence_eval, top_prediction, ranking_result
        )

        # Step 11: Generate recommendations
        safeguards = self._generate_safeguards(context, top_prediction, outcome)
        prerequisites = self._generate_prerequisites(context, recommended_option)
        post_actions = self._generate_post_actions(context, recommended_option)

        # Step 12: Generate reasoning and warnings
        reasoning = self._generate_reasoning(
            context, outcome, recommended_option, factors
        )
        warnings = self._generate_warnings(context, outcome, impact_analysis, top_prediction)

        result = DecisionResult(
            decision_id=decision_id,
            incident_id=context.incident_id,
            recommended_option=recommended_option,
            outcome=outcome,
            confidence=confidence,
            success_prediction=top_prediction,
            ranking_result=ranking_result,
            evidence_evaluation=evidence_eval,
            cost_estimate=cost_estimate,
            impact_analysis=impact_analysis,
            factors=factors,
            composite_score=composite_score,
            safeguards=safeguards,
            prerequisites=prerequisites,
            post_actions=post_actions,
            reasoning=reasoning,
            warnings=warnings,
        )

        log.info(
            "Decision made for incident %s: outcome=%s, confidence=%s, score=%.2f",
            context.incident_id,
            outcome.value,
            confidence.value,
            composite_score,
        )

        return result

    def _evaluate_evidence(self, context: IncidentContext) -> EvidenceEvaluation:
        """Evaluate the evidence collected."""
        evidence = context.evidence_collected

        supporting = [e for e in evidence if e.get("supports_hypothesis", True)]
        opposing = [e for e in evidence if not e.get("supports_hypothesis", True)]

        # Evidence strength based on quantity and quality
        quantity_factor = min(len(evidence) / 10, 1.0)
        quality_factor = context.root_cause_confidence

        evidence_strength = (quantity_factor * 0.4 + quality_factor * 0.6)

        # Coverage: estimate based on evidence types
        evidence_types = set(e.get("type", "unknown") for e in evidence)
        expected_types = {"metrics", "logs", "traces", "deployment", "config"}
        coverage = len(evidence_types.intersection(expected_types)) / len(expected_types)

        # Confidence from evidence
        confidence = evidence_strength * 0.6 + coverage * 0.4
        if len(opposing) > len(supporting) * 0.5:
            confidence *= 0.7  # Reduce confidence if significant opposing evidence

        return EvidenceEvaluation(
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            evidence_strength=evidence_strength,
            evidence_coverage=coverage,
            confidence_from_evidence=confidence,
        )

    def _analyze_impact(self, context: IncidentContext) -> ImpactAnalysis:
        """Analyze the impact of the incident."""
        # Calculate revenue at risk
        hourly_user_revenue = self.cost_rates["revenue_per_user_hourly_usd"]
        revenue_at_risk = (
            context.affected_users_estimate *
            hourly_user_revenue *
            (context.duration_minutes / 60)
        )

        # SLA risk
        sla_minutes = max(0, context.duration_minutes - 30)  # Assuming 30 min SLA

        # Reputation impact
        if context.severity == "P0" and context.is_customer_facing:
            reputation = "severe"
        elif context.severity in ("P0", "P1") and context.is_customer_facing:
            reputation = "significant"
        elif context.is_customer_facing:
            reputation = "moderate"
        else:
            reputation = "minimal"

        # Regulatory
        regulatory = context.severity in ("P0", "P1") and context.affected_users_estimate > 10000

        # Normalized impact score
        severity_scores = {"P0": 1.0, "P1": 0.8, "P2": 0.6, "P3": 0.4, "P4": 0.2}
        impact_score = (
            severity_scores.get(context.severity, 0.5) * 0.3 +
            min(len(context.affected_services) / 10, 1.0) * 0.2 +
            min(context.affected_users_estimate / 10000, 1.0) * 0.3 +
            (1.0 if context.is_revenue_impacting else 0.0) * 0.2
        )

        return ImpactAnalysis(
            blast_radius_services=len(context.affected_services),
            affected_users_estimate=context.affected_users_estimate,
            revenue_at_risk_usd=revenue_at_risk,
            sla_minutes_at_risk=sla_minutes,
            reputation_impact=reputation,
            regulatory_impact=regulatory,
            impact_score=impact_score,
        )

    def _estimate_costs(
        self,
        context: IncidentContext,
        options: list[DecisionOption],
    ) -> CostEstimate:
        """Estimate costs of taking action vs not acting."""
        # Average direct cost from options
        if options:
            direct_cost = sum(o.estimated_cost_usd for o in options) / len(options)
        else:
            direct_cost = 0

        # Opportunity cost during execution
        avg_time = sum(o.estimated_time_minutes for o in options) / len(options) if options else 30
        opportunity_cost = (
            context.affected_users_estimate *
            self.cost_rates["revenue_per_user_hourly_usd"] *
            (avg_time / 60)
        )

        # Labor cost
        labor_cost = (
            len(context.responders) *
            self.cost_rates["engineer_hourly_usd"] *
            (avg_time / 60)
        )

        total_cost = direct_cost + opportunity_cost + labor_cost

        # Cost of delay (not acting)
        cost_per_minute = (
            context.affected_users_estimate *
            self.cost_rates["revenue_per_user_hourly_usd"] / 60 +
            self.cost_rates["sla_violation_per_minute_usd"]
        )

        # Break-even point
        break_even = total_cost / cost_per_minute if cost_per_minute > 0 else 999

        return CostEstimate(
            direct_cost_usd=direct_cost,
            opportunity_cost_usd=opportunity_cost,
            labor_cost_usd=labor_cost,
            total_cost_usd=total_cost,
            cost_per_minute_delay=cost_per_minute,
            break_even_minutes=break_even,
        )

    async def _predict_success_for_options(
        self,
        context: IncidentContext,
        options: list[DecisionOption],
    ) -> dict[str, SuccessPrediction]:
        """Predict success for all options in parallel."""
        predictions = {}

        async def predict_one(option: DecisionOption) -> tuple[str, SuccessPrediction]:
            features = self._build_prediction_features(context, option)
            prediction = await self.predictor.predict(features)
            return option.option_id, prediction

        tasks = [predict_one(option) for option in options]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple):
                option_id, prediction = result
                predictions[option_id] = prediction
            else:
                log.warning("Prediction failed: %s", result)

        return predictions

    def _build_prediction_features(
        self,
        context: IncidentContext,
        option: DecisionOption,
    ) -> PredictionFeatures:
        """Build prediction features from context and option."""
        # Map action type
        action_type_map = {
            "restart": ActionType.RESTART_SERVICE,
            "scale_up": ActionType.SCALE_UP,
            "scale_down": ActionType.SCALE_DOWN,
            "rollback": ActionType.ROLLBACK,
            "config": ActionType.CONFIG_CHANGE,
            "cache": ActionType.CACHE_CLEAR,
            "feature_flag": ActionType.FEATURE_FLAG_TOGGLE,
            "pod_restart": ActionType.POD_RESTART,
        }
        action_type = action_type_map.get(option.action_type, ActionType.CUSTOM)

        return PredictionFeatures(
            action_type=action_type,
            action_complexity=option.risk_score,  # Use risk as complexity proxy
            is_reversible=option.is_reversible,
            requires_downtime=option.requires_downtime,
            estimated_duration_seconds=option.estimated_time_minutes * 60,
            incident_severity=context.severity,
            incident_duration_minutes=context.duration_minutes,
            affected_services_count=len(context.affected_services),
            is_customer_facing=context.is_customer_facing,
            is_revenue_impacting=context.is_revenue_impacting,
            similar_incidents_count=10,  # Would come from historical lookup
            similar_action_success_rate=0.85,  # Would come from historical lookup
            service_change_frequency=2.0,  # Would come from metrics
            time_since_last_deploy_hours=24.0,  # Would come from deploy timeline
            is_peak_traffic=context.is_peak_traffic,
            is_change_freeze=context.is_change_freeze,
            is_maintenance_window=context.is_maintenance_window,
            on_call_experience_level=context.on_call_experience_level,
            root_cause_confidence=context.root_cause_confidence,
            evidence_count=len(context.evidence_collected),
            diagnostic_coverage=0.7,  # Would come from investigation state
        )

    def _calculate_factors(
        self,
        context: IncidentContext,
        evidence: EvidenceEvaluation,
        impact: ImpactAnalysis,
        cost: CostEstimate,
        prediction: SuccessPrediction | None,
        option: RankedOption | None,
    ) -> list[DecisionFactor]:
        """Calculate individual decision factors."""
        factors = []

        # Success probability
        if prediction:
            factors.append(DecisionFactor(
                name="Success Probability",
                value=prediction.probability,
                weight=0.25,
                impact="positive" if prediction.probability >= 0.7 else "negative",
                explanation=f"{prediction.probability:.0%} predicted success rate",
            ))

        # Evidence strength
        factors.append(DecisionFactor(
            name="Evidence Strength",
            value=evidence.evidence_strength,
            weight=0.20,
            impact="positive" if evidence.evidence_strength >= 0.6 else "negative",
            explanation=f"{len(evidence.supporting_evidence)} supporting, {len(evidence.opposing_evidence)} opposing",
        ))

        # Risk level
        if prediction:
            risk_value = {"minimal": 1.0, "low": 0.8, "medium": 0.5, "high": 0.3, "critical": 0.1}
            risk_score = risk_value.get(prediction.risk_level.value, 0.5)
            factors.append(DecisionFactor(
                name="Risk Level",
                value=risk_score,
                weight=0.20,
                impact="positive" if risk_score >= 0.6 else "negative",
                explanation=f"{prediction.risk_level.value} risk",
            ))

        # Impact severity
        factors.append(DecisionFactor(
            name="Impact Severity",
            value=1.0 - impact.impact_score,  # Invert - lower impact = better
            weight=0.15,
            impact="negative" if impact.impact_score >= 0.6 else "neutral",
            explanation=f"{impact.blast_radius_services} services, {impact.affected_users_estimate} users affected",
        ))

        # Cost efficiency
        cost_efficiency = 1.0 - min(cost.total_cost_usd / 10000, 1.0)
        factors.append(DecisionFactor(
            name="Cost Efficiency",
            value=cost_efficiency,
            weight=0.10,
            impact="positive" if cost_efficiency >= 0.7 else "neutral",
            explanation=f"${cost.total_cost_usd:.0f} total cost, break-even in {cost.break_even_minutes:.0f} min",
        ))

        # Root cause confidence
        factors.append(DecisionFactor(
            name="Root Cause Confidence",
            value=context.root_cause_confidence,
            weight=0.10,
            impact="positive" if context.root_cause_confidence >= 0.7 else "negative",
            explanation=f"{context.root_cause_confidence:.0%} confident in root cause",
        ))

        return factors

    def _calculate_composite_score(self, factors: list[DecisionFactor]) -> float:
        """Calculate weighted composite score from factors."""
        if not factors:
            return 0.5

        total_weight = sum(f.weight for f in factors)
        if total_weight == 0:
            return 0.5

        weighted_sum = sum(f.value * f.weight for f in factors)
        return weighted_sum / total_weight

    def _determine_outcome(
        self,
        score: float,
        context: IncidentContext,
        evidence: EvidenceEvaluation,
        prediction: SuccessPrediction | None,
    ) -> DecisionOutcome:
        """Determine the recommended decision outcome."""
        # Check for blockers
        if context.is_change_freeze and context.severity not in ("P0", "P1"):
            return DecisionOutcome.REQUIRE_APPROVAL

        if evidence.evidence_strength < self.THRESHOLDS["evidence_strength_min"]:
            return DecisionOutcome.INVESTIGATE_MORE

        if prediction and prediction.risk_level == RiskLevel.CRITICAL:
            return DecisionOutcome.ESCALATE

        # Score-based determination
        if score >= self.THRESHOLDS["proceed_score"]:
            return DecisionOutcome.PROCEED

        if score >= self.THRESHOLDS["caution_score"]:
            return DecisionOutcome.PROCEED_WITH_CAUTION

        if score >= self.THRESHOLDS["approval_score"]:
            return DecisionOutcome.REQUIRE_APPROVAL

        if score >= self.THRESHOLDS["investigate_score"]:
            return DecisionOutcome.INVESTIGATE_MORE

        if score >= self.THRESHOLDS["abort_score"]:
            return DecisionOutcome.ESCALATE

        return DecisionOutcome.ABORT

    def _determine_confidence(
        self,
        evidence: EvidenceEvaluation,
        prediction: SuccessPrediction | None,
        ranking: RankingResult | None,
    ) -> DecisionConfidence:
        """Determine confidence level in the decision."""
        confidence_signals = []

        # Evidence confidence
        confidence_signals.append(evidence.confidence_from_evidence)

        # Prediction confidence
        if prediction:
            confidence_signals.append(prediction.confidence)

        # Ranking clarity (how much better is top vs second)
        if ranking and len(ranking.ranked_options) >= 2:
            top_score = ranking.ranked_options[0].total_score
            second_score = ranking.ranked_options[1].total_score
            clarity = (top_score - second_score) / top_score if top_score > 0 else 0
            confidence_signals.append(min(clarity * 2, 1.0))  # Scale up

        avg_confidence = sum(confidence_signals) / len(confidence_signals) if confidence_signals else 0.5

        if avg_confidence >= self.THRESHOLDS["high_confidence"]:
            return DecisionConfidence.HIGH
        if avg_confidence >= self.THRESHOLDS["medium_confidence"]:
            return DecisionConfidence.MEDIUM
        if avg_confidence >= 0.4:
            return DecisionConfidence.LOW
        return DecisionConfidence.UNCERTAIN

    def _generate_safeguards(
        self,
        context: IncidentContext,
        prediction: SuccessPrediction | None,
        outcome: DecisionOutcome,
    ) -> list[str]:
        """Generate recommended safeguards."""
        safeguards = []

        if prediction:
            safeguards.extend(prediction.recommended_safeguards)

        if outcome in (DecisionOutcome.PROCEED_WITH_CAUTION, DecisionOutcome.REQUIRE_APPROVAL):
            safeguards.append("Have rollback plan ready before execution")

        if context.is_customer_facing:
            safeguards.append("Prepare customer communication template")

        if context.is_revenue_impacting:
            safeguards.append("Alert finance team of potential impact")

        if context.severity in ("P0", "P1"):
            safeguards.append("Keep incident commander informed of progress")

        return list(set(safeguards))[:5]

    def _generate_prerequisites(
        self,
        context: IncidentContext,
        option: RankedOption | None,
    ) -> list[str]:
        """Generate prerequisites before executing."""
        prerequisites = []

        if option:
            prerequisites.extend(option.option.prerequisites)

        prerequisites.append("Verify current system state matches expectations")
        prerequisites.append("Ensure monitoring dashboards are ready")

        if context.is_change_freeze:
            prerequisites.append("Obtain change freeze exception approval")

        return list(set(prerequisites))[:5]

    def _generate_post_actions(
        self,
        context: IncidentContext,
        option: RankedOption | None,
    ) -> list[str]:
        """Generate post-execution actions."""
        return [
            "Monitor key metrics for 15 minutes after execution",
            "Update incident timeline with action taken",
            "Notify stakeholders of resolution status",
            "Schedule postmortem if severity was P0/P1",
            "Document any unexpected side effects",
        ]

    def _generate_reasoning(
        self,
        context: IncidentContext,
        outcome: DecisionOutcome,
        option: RankedOption | None,
        factors: list[DecisionFactor],
    ) -> str:
        """Generate human-readable reasoning."""
        parts = []

        if option:
            parts.append(f"Recommending '{option.option.name}' for {context.severity} incident.")

        # Key positive factors
        positive = [f for f in factors if f.impact == "positive"]
        if positive:
            top_positive = max(positive, key=lambda f: f.value * f.weight)
            parts.append(f"Key strength: {top_positive.explanation}.")

        # Key concerns
        negative = [f for f in factors if f.impact == "negative"]
        if negative:
            top_negative = max(negative, key=lambda f: (1 - f.value) * f.weight)
            parts.append(f"Key concern: {top_negative.explanation}.")

        # Outcome explanation
        outcome_explanations = {
            DecisionOutcome.PROCEED: "Conditions are favorable to proceed.",
            DecisionOutcome.PROCEED_WITH_CAUTION: "Proceed with extra monitoring and safeguards.",
            DecisionOutcome.REQUIRE_APPROVAL: "Human approval needed due to risk factors.",
            DecisionOutcome.INVESTIGATE_MORE: "More investigation needed before acting.",
            DecisionOutcome.ESCALATE: "Escalate to senior engineers due to complexity.",
            DecisionOutcome.ABORT: "Do not proceed - risk is too high.",
        }
        parts.append(outcome_explanations.get(outcome, ""))

        return " ".join(parts)

    def _generate_warnings(
        self,
        context: IncidentContext,
        outcome: DecisionOutcome,
        impact: ImpactAnalysis,
        prediction: SuccessPrediction | None,
    ) -> list[str]:
        """Generate warning messages."""
        warnings = []

        if context.is_change_freeze:
            warnings.append("CHANGE FREEZE ACTIVE - Exception approval required")

        if context.is_peak_traffic:
            warnings.append("Peak traffic period - higher risk of user impact")

        if impact.regulatory_impact:
            warnings.append("Potential regulatory implications - document all actions")

        if prediction and prediction.probability < 0.5:
            warnings.append(f"Low success probability ({prediction.probability:.0%}) - consider alternatives")

        if context.root_cause_confidence < 0.5:
            warnings.append("Root cause uncertain - fix may not resolve the issue")

        if len(context.affected_services) >= 5:
            warnings.append("Large blast radius - coordinate with multiple teams")

        return warnings

    def _generate_decision_id(self, incident_id: str) -> str:
        """Generate unique decision ID."""
        timestamp = datetime.now(timezone.utc).isoformat()
        content = f"{incident_id}:{timestamp}:{self._decision_count}"
        return f"dec_{hashlib.sha256(content.encode()).hexdigest()[:12]}"


def create_decision_engine(redis_client: Any = None) -> DecisionEngine:
    """Factory function to create a decision engine."""
    return DecisionEngine(redis_client=redis_client)
