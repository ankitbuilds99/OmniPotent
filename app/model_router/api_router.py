"""Model Router API Router.

REST API for adaptive model routing and cost optimization.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.model_router.adaptive_router import (
    AdaptiveModelRouter,
    CustomerTier,
    ModelTier,
    SLARequirements,
)
from app.model_router.complexity_analyzer import ComplexityAnalyzer
from app.model_router.cost_optimizer import CostOptimizer
from app.tenancy.scoping import scoped

log = logging.getLogger("omnipotent.model_router.api")

router = APIRouter(prefix="/model-router", tags=["model-router"])

# One instance per org. The router and the cost optimizer accumulate routing
# decisions and spend per tenant, and the /model-router stats endpoints read
# them back: shared, one customer's query volume and LLM bill were visible to
# every other customer, and their budget caps applied to everyone.
_routers = scoped(AdaptiveModelRouter)
_complexity_analyzers = scoped(ComplexityAnalyzer)
_cost_optimizers = scoped(CostOptimizer)


def get_router() -> AdaptiveModelRouter:
    return _routers.get()


def get_complexity_analyzer() -> ComplexityAnalyzer:
    return _complexity_analyzers.get()


def get_cost_optimizer() -> CostOptimizer:
    return _cost_optimizers.get()


class RouteRequest(BaseModel):
    query: str
    context: dict[str, Any] | None = None
    customer_tier: str = "professional"
    max_latency_ms: int | None = None
    min_quality: str = "balanced"
    max_cost: float | None = None
    incident_severity: str | None = None
    preferred_provider: str | None = None


class AnalyzeComplexityRequest(BaseModel):
    query: str
    context: dict[str, Any] | None = None
    incident_severity: str | None = None


class EstimateCostRequest(BaseModel):
    model_id: str
    input_tokens: int
    output_tokens: int | None = None
    use_cache: bool = False
    use_batch: bool = False


class RecordUsageRequest(BaseModel):
    model_id: str
    input_tokens: int
    output_tokens: int
    customer_id: str | None = None
    request_type: str = "inference"


class SetBudgetRequest(BaseModel):
    customer_id: str
    monthly_budget: float


class RecommendModelRequest(BaseModel):
    use_case: str
    budget: str = "balanced"


class SetAvailabilityRequest(BaseModel):
    model_id: str
    available: bool


@router.post("/route")
async def route_request(req: RouteRequest) -> dict[str, Any]:
    """Route a query to the optimal model.

    Considers complexity, SLA requirements, customer tier,
    and cost optimization to select the best model.
    """
    model_router = get_router()

    try:
        customer_tier = CustomerTier(req.customer_tier)
    except ValueError:
        customer_tier = CustomerTier.PROFESSIONAL

    try:
        min_quality = ModelTier(req.min_quality)
    except ValueError:
        min_quality = ModelTier.BALANCED

    sla = SLARequirements(
        max_latency_ms=req.max_latency_ms,
        min_quality=min_quality,
        max_cost=req.max_cost,
    )

    decision = model_router.route(
        query=req.query,
        context=req.context,
        customer_tier=customer_tier,
        sla=sla,
        incident_severity=req.incident_severity,
        preferred_provider=req.preferred_provider,
    )

    return decision.to_dict()


@router.post("/analyze-complexity")
async def analyze_complexity(req: AnalyzeComplexityRequest) -> dict[str, Any]:
    """Analyze the complexity of a query.

    Returns detailed complexity scoring including semantic
    complexity, reasoning depth, and domain breadth.
    """
    analyzer = get_complexity_analyzer()

    score = analyzer.analyze(
        query=req.query,
        context=req.context,
        incident_severity=req.incident_severity,
    )

    return score.to_dict()


@router.post("/estimate-cost")
async def estimate_cost(req: EstimateCostRequest) -> dict[str, Any]:
    """Estimate the cost of a model request.

    Returns cost estimates for standard, cached, and batched
    request modes.
    """
    optimizer = get_cost_optimizer()

    estimate = optimizer.estimate_cost(
        model_id=req.model_id,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        use_cache=req.use_cache,
        use_batch=req.use_batch,
    )

    return estimate.to_dict()


@router.post("/record-usage")
async def record_usage(req: RecordUsageRequest) -> dict[str, Any]:
    """Record model usage for cost tracking."""
    optimizer = get_cost_optimizer()

    record = optimizer.record_usage(
        model_id=req.model_id,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        customer_id=req.customer_id,
        request_type=req.request_type,
    )

    return {
        "recorded": True,
        "cost": record.cost,
        "timestamp": record.timestamp.isoformat(),
    }


@router.get("/usage/daily")
async def get_daily_usage(customer_id: str | None = None) -> dict[str, Any]:
    """Get daily usage statistics."""
    optimizer = get_cost_optimizer()
    return optimizer.get_daily_usage(customer_id)


@router.get("/usage/monthly")
async def get_monthly_usage(customer_id: str | None = None) -> dict[str, Any]:
    """Get monthly usage statistics."""
    optimizer = get_cost_optimizer()
    return optimizer.get_monthly_usage(customer_id)


@router.get("/budget/check")
async def check_budget(
    customer_id: str | None = None,
    additional_cost: float = 0.0,
) -> dict[str, Any]:
    """Check if budget allows for additional spending."""
    optimizer = get_cost_optimizer()
    return optimizer.check_budget(customer_id, additional_cost)


@router.post("/budget/set")
async def set_customer_budget(req: SetBudgetRequest) -> dict[str, Any]:
    """Set monthly budget for a customer."""
    optimizer = get_cost_optimizer()
    optimizer.set_customer_budget(req.customer_id, req.monthly_budget)
    return {
        "status": "set",
        "customer_id": req.customer_id,
        "monthly_budget": req.monthly_budget,
    }


@router.post("/recommend")
async def recommend_model(req: RecommendModelRequest) -> dict[str, Any]:
    """Get model recommendation for a use case.

    Use cases: chat, analysis, coding, incident_response
    Budgets: economy, balanced, quality
    """
    model_router = get_router()
    return model_router.get_model_recommendation(req.use_case, req.budget)


@router.post("/availability")
async def set_model_availability(req: SetAvailabilityRequest) -> dict[str, Any]:
    """Set model availability status."""
    model_router = get_router()
    model_router.set_model_availability(req.model_id, req.available)
    return {
        "model_id": req.model_id,
        "available": req.available,
    }


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get routing and cost statistics."""
    model_router = get_router()
    optimizer = get_cost_optimizer()

    return {
        "routing": model_router.get_routing_stats(),
        "cost_analytics": optimizer.get_cost_analytics(days=7),
    }


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """List available models with their configurations."""
    model_router = get_router()
    optimizer = get_cost_optimizer()

    models = []
    for model_id, config in model_router.MODEL_CONFIGS.items():
        cost_info = optimizer.get_model_cost(model_id)
        tier = config["tier"]
        tier_value = tier.value if hasattr(tier, "value") else str(tier)
        models.append({
            "id": model_id,
            "tier": tier_value,
            "avg_latency_ms": config["avg_latency_ms"],
            "quality_score": config["quality_score"],
            "supports_streaming": config["supports_streaming"],
            "supports_function_calling": config["supports_function_calling"],
            "max_context": config["max_context"],
            "cost_per_1k_input": cost_info.input_cost_per_1k if cost_info else None,
            "cost_per_1k_output": cost_info.output_cost_per_1k if cost_info else None,
            "available": model_router._model_availability.get(model_id, True),
        })

    return {"models": models}


@router.get("/tiers")
async def list_tiers() -> dict[str, Any]:
    """List customer tiers and their allowed model tiers."""
    model_router = get_router()

    tiers = {}
    for customer_tier, model_tiers in model_router.TIER_ALLOWED_MODELS.items():
        tiers[customer_tier.value] = [t.value for t in model_tiers]

    return {"customer_tiers": tiers}
