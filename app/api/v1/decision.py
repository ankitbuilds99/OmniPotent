"""Decision Engine API.

Provides endpoints for:
- Evaluating remediation options
- Predicting success probability
- Ranking options by multiple criteria
- Making automated decisions
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field

from app.decision_engine import (
    DecisionEngine,
    DecisionOption,
    SuccessPredictor,
    PredictionFeatures,
    OptionRanker,
    RankingCriteria,
    create_decision_engine,
)
from app.decision_engine.success_predictor import ActionType, RiskLevel
from app.decision_engine.option_ranker import RankingStrategy
from app.decision_engine.engine import (
    IncidentContext,
    DecisionConfidence,
    DecisionOutcome,
)

log = logging.getLogger("devcopilot.api.decision")

router = APIRouter(prefix="/decision", tags=["decision-engine"])

# Singleton instances
_decision_engine: DecisionEngine | None = None
_success_predictor: SuccessPredictor | None = None
_option_ranker: OptionRanker | None = None


def get_decision_engine() -> DecisionEngine:
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = create_decision_engine()
    return _decision_engine


def get_success_predictor() -> SuccessPredictor:
    global _success_predictor
    if _success_predictor is None:
        _success_predictor = SuccessPredictor()
    return _success_predictor


def get_option_ranker() -> OptionRanker:
    global _option_ranker
    if _option_ranker is None:
        _option_ranker = OptionRanker()
    return _option_ranker


# Request/Response Models
class PredictionFeaturesRequest(BaseModel):
    action_type: str = Field(..., description="Type of action (restart_service, scale_up, rollback, etc.)")
    action_complexity: float = Field(0.5, ge=0, le=1, description="Complexity of the action (0-1)")
    is_reversible: bool = Field(True, description="Whether the action can be easily reversed")
    requires_downtime: bool = Field(False, description="Whether the action requires service downtime")
    estimated_duration_seconds: int = Field(60, ge=0, description="Estimated duration in seconds")
    incident_severity: str = Field("P2", description="Incident severity (P0-P4)")
    incident_duration_minutes: int = Field(30, ge=0, description="How long the incident has been ongoing")
    affected_services_count: int = Field(1, ge=1, description="Number of affected services")
    is_customer_facing: bool = Field(True, description="Whether the service is customer-facing")
    is_revenue_impacting: bool = Field(False, description="Whether the incident impacts revenue")
    similar_incidents_count: int = Field(0, ge=0, description="Number of similar historical incidents")
    similar_action_success_rate: float = Field(0.8, ge=0, le=1, description="Success rate of similar actions")
    service_change_frequency: float = Field(1.0, ge=0, description="Changes per day for this service")
    time_since_last_deploy_hours: float = Field(24.0, ge=0, description="Hours since last deployment")
    is_peak_traffic: bool = Field(False, description="Whether it's currently peak traffic")
    is_change_freeze: bool = Field(False, description="Whether a change freeze is active")
    is_maintenance_window: bool = Field(False, description="Whether it's a maintenance window")
    on_call_experience_level: float = Field(0.7, ge=0, le=1, description="Experience level of on-call engineer")
    root_cause_confidence: float = Field(0.7, ge=0, le=1, description="Confidence in root cause diagnosis")
    evidence_count: int = Field(5, ge=0, description="Number of evidence pieces collected")
    diagnostic_coverage: float = Field(0.6, ge=0, le=1, description="Coverage of diagnostic investigation")


class SuccessPredictionResponse(BaseModel):
    probability: float
    confidence: float
    risk_level: str
    positive_factors: list[str]
    negative_factors: list[str]
    recommended_safeguards: list[str]
    alternative_actions: list[str]
    model_version: str
    prediction_id: str
    predicted_at: str


class DecisionOptionRequest(BaseModel):
    option_id: str = Field(..., description="Unique identifier for this option")
    name: str = Field(..., description="Name of the remediation option")
    description: str = Field(..., description="Description of what this option does")
    action_type: str = Field(..., description="Type of action")
    success_probability: float = Field(0.8, ge=0, le=1, description="Estimated success probability")
    risk_score: float = Field(0.3, ge=0, le=1, description="Risk score (0=low, 1=high)")
    estimated_time_minutes: int = Field(10, ge=0, description="Estimated execution time")
    estimated_cost_usd: float = Field(0, ge=0, description="Estimated cost in USD")
    impact_reduction_percent: float = Field(80, ge=0, le=100, description="Expected impact reduction")
    is_reversible: bool = Field(True, description="Whether the action is reversible")
    is_automated: bool = Field(True, description="Whether the action can be automated")
    requires_approval: bool = Field(False, description="Whether human approval is required")
    requires_downtime: bool = Field(False, description="Whether downtime is required")
    prerequisites: list[str] = Field(default_factory=list, description="Prerequisites for this action")
    side_effects: list[str] = Field(default_factory=list, description="Known side effects")
    evidence_supporting: list[str] = Field(default_factory=list, description="Evidence supporting this option")
    evidence_against: list[str] = Field(default_factory=list, description="Evidence against this option")


class RankOptionsRequest(BaseModel):
    options: list[DecisionOptionRequest]
    strategy: str = Field("balanced", description="Ranking strategy: balanced, speed_focused, safety_focused, cost_focused")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context for ranking")


class RankedOptionResponse(BaseModel):
    option_id: str
    name: str
    rank: int
    total_score: float
    scores: dict[str, float]
    score_delta_from_top: float
    percentage_of_top: float
    strengths: list[str]
    weaknesses: list[str]
    recommendation: str


class RankingResultResponse(BaseModel):
    ranked_options: list[RankedOptionResponse]
    strategy: str
    top_recommendation: RankedOptionResponse | None
    close_alternatives: list[RankedOptionResponse]
    analysis_summary: str
    ranking_id: str
    ranked_at: str


class IncidentContextRequest(BaseModel):
    incident_id: str = Field(..., description="Unique incident identifier")
    title: str = Field(..., description="Incident title")
    severity: str = Field("P2", description="Severity level (P0-P4)")
    status: str = Field("investigating", description="Current incident status")
    started_at: str = Field(..., description="ISO timestamp when incident started")
    duration_minutes: int = Field(30, ge=0, description="Incident duration in minutes")
    affected_services: list[str] = Field(default_factory=list, description="List of affected services")
    affected_users_estimate: int = Field(0, ge=0, description="Estimated affected users")
    is_customer_facing: bool = Field(True, description="Whether customer-facing")
    is_revenue_impacting: bool = Field(False, description="Whether revenue-impacting")
    root_cause_hypothesis: str = Field("", description="Current root cause hypothesis")
    root_cause_confidence: float = Field(0.5, ge=0, le=1, description="Confidence in hypothesis")
    evidence_collected: list[dict[str, Any]] = Field(default_factory=list, description="Collected evidence")
    is_peak_traffic: bool = Field(False)
    is_change_freeze: bool = Field(False)
    is_maintenance_window: bool = Field(False)
    recent_deployments: list[dict[str, Any]] = Field(default_factory=list)
    responders: list[str] = Field(default_factory=list)
    on_call_experience_level: float = Field(0.7, ge=0, le=1)


class DecisionRequest(BaseModel):
    context: IncidentContextRequest
    options: list[DecisionOptionRequest]
    ranking_strategy: str = Field("balanced", description="Ranking strategy to use")


class DecisionResultResponse(BaseModel):
    decision_id: str
    incident_id: str
    outcome: str
    confidence: str
    composite_score: float
    recommended_option: RankedOptionResponse | None
    success_probability: float | None
    risk_level: str | None
    safeguards: list[str]
    prerequisites: list[str]
    post_actions: list[str]
    reasoning: str
    warnings: list[str]
    decided_at: str


# Endpoints
@router.post("/predict-success", response_model=SuccessPredictionResponse)
async def predict_success(
    request: PredictionFeaturesRequest,
    predictor: SuccessPredictor = Depends(get_success_predictor),
) -> SuccessPredictionResponse:
    """Predict the success probability of a remediation action.

    Uses ML-based prediction with historical data calibration to estimate
    the likelihood that a proposed action will successfully resolve an incident.
    """
    try:
        action_type_map = {
            "restart_service": ActionType.RESTART_SERVICE,
            "scale_up": ActionType.SCALE_UP,
            "scale_down": ActionType.SCALE_DOWN,
            "rollback": ActionType.ROLLBACK,
            "config_change": ActionType.CONFIG_CHANGE,
            "cache_clear": ActionType.CACHE_CLEAR,
            "feature_flag_toggle": ActionType.FEATURE_FLAG_TOGGLE,
            "pod_restart": ActionType.POD_RESTART,
            "database_migration": ActionType.DATABASE_MIGRATION,
        }

        action_type = action_type_map.get(request.action_type, ActionType.CUSTOM)

        features = PredictionFeatures(
            action_type=action_type,
            action_complexity=request.action_complexity,
            is_reversible=request.is_reversible,
            requires_downtime=request.requires_downtime,
            estimated_duration_seconds=request.estimated_duration_seconds,
            incident_severity=request.incident_severity,
            incident_duration_minutes=request.incident_duration_minutes,
            affected_services_count=request.affected_services_count,
            is_customer_facing=request.is_customer_facing,
            is_revenue_impacting=request.is_revenue_impacting,
            similar_incidents_count=request.similar_incidents_count,
            similar_action_success_rate=request.similar_action_success_rate,
            service_change_frequency=request.service_change_frequency,
            time_since_last_deploy_hours=request.time_since_last_deploy_hours,
            is_peak_traffic=request.is_peak_traffic,
            is_change_freeze=request.is_change_freeze,
            is_maintenance_window=request.is_maintenance_window,
            on_call_experience_level=request.on_call_experience_level,
            root_cause_confidence=request.root_cause_confidence,
            evidence_count=request.evidence_count,
            diagnostic_coverage=request.diagnostic_coverage,
        )

        prediction = await predictor.predict(features)

        return SuccessPredictionResponse(
            probability=prediction.probability,
            confidence=prediction.confidence,
            risk_level=prediction.risk_level.value,
            positive_factors=prediction.positive_factors,
            negative_factors=prediction.negative_factors,
            recommended_safeguards=prediction.recommended_safeguards,
            alternative_actions=prediction.alternative_actions,
            model_version=prediction.model_version,
            prediction_id=prediction.prediction_id,
            predicted_at=prediction.predicted_at.isoformat(),
        )

    except Exception as e:
        log.error("Prediction failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


@router.post("/rank-options", response_model=RankingResultResponse)
async def rank_options(
    request: RankOptionsRequest,
    ranker: OptionRanker = Depends(get_option_ranker),
) -> RankingResultResponse:
    """Rank remediation options using multi-criteria decision analysis.

    Evaluates options across success probability, risk, cost, time,
    impact reduction, reversibility, and automation level.
    """
    try:
        strategy_map = {
            "balanced": RankingStrategy.BALANCED,
            "speed_focused": RankingStrategy.SPEED_FOCUSED,
            "safety_focused": RankingStrategy.SAFETY_FOCUSED,
            "cost_focused": RankingStrategy.COST_FOCUSED,
        }
        strategy = strategy_map.get(request.strategy, RankingStrategy.BALANCED)

        options = [
            DecisionOption(
                option_id=opt.option_id,
                name=opt.name,
                description=opt.description,
                action_type=opt.action_type,
                success_probability=opt.success_probability,
                risk_score=opt.risk_score,
                estimated_time_minutes=opt.estimated_time_minutes,
                estimated_cost_usd=opt.estimated_cost_usd,
                impact_reduction_percent=opt.impact_reduction_percent,
                is_reversible=opt.is_reversible,
                is_automated=opt.is_automated,
                requires_approval=opt.requires_approval,
                requires_downtime=opt.requires_downtime,
                prerequisites=opt.prerequisites,
                side_effects=opt.side_effects,
                evidence_supporting=opt.evidence_supporting,
                evidence_against=opt.evidence_against,
            )
            for opt in request.options
        ]

        result = ranker.rank(options, strategy, context=request.context)

        ranked_options = [
            RankedOptionResponse(
                option_id=ro.option.option_id,
                name=ro.option.name,
                rank=ro.rank,
                total_score=ro.total_score,
                scores=ro.scores,
                score_delta_from_top=ro.score_delta_from_top,
                percentage_of_top=ro.percentage_of_top,
                strengths=ro.strengths,
                weaknesses=ro.weaknesses,
                recommendation=ro.recommendation,
            )
            for ro in result.ranked_options
        ]

        top_rec = None
        if result.top_recommendation:
            tr = result.top_recommendation
            top_rec = RankedOptionResponse(
                option_id=tr.option.option_id,
                name=tr.option.name,
                rank=tr.rank,
                total_score=tr.total_score,
                scores=tr.scores,
                score_delta_from_top=tr.score_delta_from_top,
                percentage_of_top=tr.percentage_of_top,
                strengths=tr.strengths,
                weaknesses=tr.weaknesses,
                recommendation=tr.recommendation,
            )

        close_alts = [
            RankedOptionResponse(
                option_id=ca.option.option_id,
                name=ca.option.name,
                rank=ca.rank,
                total_score=ca.total_score,
                scores=ca.scores,
                score_delta_from_top=ca.score_delta_from_top,
                percentage_of_top=ca.percentage_of_top,
                strengths=ca.strengths,
                weaknesses=ca.weaknesses,
                recommendation=ca.recommendation,
            )
            for ca in result.close_alternatives
        ]

        return RankingResultResponse(
            ranked_options=ranked_options,
            strategy=result.strategy.value,
            top_recommendation=top_rec,
            close_alternatives=close_alts,
            analysis_summary=result.analysis_summary,
            ranking_id=result.ranking_id,
            ranked_at=result.ranked_at.isoformat(),
        )

    except Exception as e:
        log.error("Ranking failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Ranking failed: {str(e)}")


@router.post("/decide", response_model=DecisionResultResponse)
async def make_decision(
    request: DecisionRequest,
    engine: DecisionEngine = Depends(get_decision_engine),
) -> DecisionResultResponse:
    """Make a complete decision about remediation options.

    Combines success prediction, option ranking, evidence evaluation,
    cost estimation, and impact analysis to produce a final recommendation.
    """
    try:
        context = IncidentContext(
            incident_id=request.context.incident_id,
            title=request.context.title,
            severity=request.context.severity,
            status=request.context.status,
            started_at=datetime.fromisoformat(request.context.started_at.replace('Z', '+00:00')),
            duration_minutes=request.context.duration_minutes,
            affected_services=request.context.affected_services,
            affected_users_estimate=request.context.affected_users_estimate,
            is_customer_facing=request.context.is_customer_facing,
            is_revenue_impacting=request.context.is_revenue_impacting,
            root_cause_hypothesis=request.context.root_cause_hypothesis,
            root_cause_confidence=request.context.root_cause_confidence,
            evidence_collected=request.context.evidence_collected,
            is_peak_traffic=request.context.is_peak_traffic,
            is_change_freeze=request.context.is_change_freeze,
            is_maintenance_window=request.context.is_maintenance_window,
            recent_deployments=request.context.recent_deployments,
            responders=request.context.responders,
            on_call_experience_level=request.context.on_call_experience_level,
        )

        options = [
            DecisionOption(
                option_id=opt.option_id,
                name=opt.name,
                description=opt.description,
                action_type=opt.action_type,
                success_probability=opt.success_probability,
                risk_score=opt.risk_score,
                estimated_time_minutes=opt.estimated_time_minutes,
                estimated_cost_usd=opt.estimated_cost_usd,
                impact_reduction_percent=opt.impact_reduction_percent,
                is_reversible=opt.is_reversible,
                is_automated=opt.is_automated,
                requires_approval=opt.requires_approval,
                requires_downtime=opt.requires_downtime,
                prerequisites=opt.prerequisites,
                side_effects=opt.side_effects,
                evidence_supporting=opt.evidence_supporting,
                evidence_against=opt.evidence_against,
            )
            for opt in request.options
        ]

        strategy_map = {
            "balanced": RankingStrategy.BALANCED,
            "speed_focused": RankingStrategy.SPEED_FOCUSED,
            "safety_focused": RankingStrategy.SAFETY_FOCUSED,
            "cost_focused": RankingStrategy.COST_FOCUSED,
        }
        strategy = strategy_map.get(request.ranking_strategy, RankingStrategy.BALANCED)

        result = await engine.decide(context, options, strategy)

        rec_option = None
        if result.recommended_option:
            ro = result.recommended_option
            rec_option = RankedOptionResponse(
                option_id=ro.option.option_id,
                name=ro.option.name,
                rank=ro.rank,
                total_score=ro.total_score,
                scores=ro.scores,
                score_delta_from_top=ro.score_delta_from_top,
                percentage_of_top=ro.percentage_of_top,
                strengths=ro.strengths,
                weaknesses=ro.weaknesses,
                recommendation=ro.recommendation,
            )

        return DecisionResultResponse(
            decision_id=result.decision_id,
            incident_id=result.incident_id,
            outcome=result.outcome.value,
            confidence=result.confidence.value,
            composite_score=result.composite_score,
            recommended_option=rec_option,
            success_probability=result.success_prediction.probability if result.success_prediction else None,
            risk_level=result.success_prediction.risk_level.value if result.success_prediction else None,
            safeguards=result.safeguards,
            prerequisites=result.prerequisites,
            post_actions=result.post_actions,
            reasoning=result.reasoning,
            warnings=result.warnings,
            decided_at=result.decided_at.isoformat(),
        )

    except Exception as e:
        log.error("Decision failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Decision failed: {str(e)}")


@router.post("/record-outcome/{prediction_id}")
async def record_outcome(
    prediction_id: str,
    was_successful: bool,
    actual_duration_seconds: int,
    required_rollback: bool = False,
    side_effects: list[str] | None = None,
    predictor: SuccessPredictor = Depends(get_success_predictor),
) -> dict[str, str]:
    """Record the actual outcome of a predicted action for model improvement.

    This feedback loop helps calibrate the success prediction model over time.
    """
    try:
        await predictor.record_outcome(
            prediction_id=prediction_id,
            was_successful=was_successful,
            actual_duration_seconds=actual_duration_seconds,
            required_rollback=required_rollback,
            side_effects=side_effects,
        )

        return {"status": "recorded", "prediction_id": prediction_id}

    except Exception as e:
        log.error("Failed to record outcome: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to record outcome: {str(e)}")


@router.get("/strategies")
async def list_strategies() -> dict[str, Any]:
    """List available ranking strategies with descriptions."""
    return {
        "strategies": [
            {
                "id": "balanced",
                "name": "Balanced",
                "description": "Equal weight to all factors (default)",
            },
            {
                "id": "speed_focused",
                "name": "Speed Focused",
                "description": "Prioritize fast resolution time",
            },
            {
                "id": "safety_focused",
                "name": "Safety Focused",
                "description": "Prioritize low risk and reversibility",
            },
            {
                "id": "cost_focused",
                "name": "Cost Focused",
                "description": "Prioritize low cost options",
            },
        ]
    }


@router.get("/action-types")
async def list_action_types() -> dict[str, Any]:
    """List supported action types for success prediction."""
    return {
        "action_types": [
            {"id": "restart_service", "base_success_rate": 0.90},
            {"id": "scale_up", "base_success_rate": 0.93},
            {"id": "scale_down", "base_success_rate": 0.88},
            {"id": "rollback", "base_success_rate": 0.85},
            {"id": "config_change", "base_success_rate": 0.82},
            {"id": "cache_clear", "base_success_rate": 0.95},
            {"id": "feature_flag_toggle", "base_success_rate": 0.98},
            {"id": "pod_restart", "base_success_rate": 0.92},
            {"id": "database_migration", "base_success_rate": 0.70},
            {"id": "dns_update", "base_success_rate": 0.80},
            {"id": "certificate_renewal", "base_success_rate": 0.88},
            {"id": "security_patch", "base_success_rate": 0.75},
        ]
    }
