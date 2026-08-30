"""Adaptive Model Router.

Intelligent routing of LLM requests based on complexity,
SLA requirements, cost constraints, and customer tier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypedDict

from app import model_catalog
from app.model_router.complexity_analyzer import (
    ComplexityAnalyzer,
    ComplexityScore,
    ComplexityLevel,
)
from app.model_router.cost_optimizer import CostOptimizer, CostEstimate

log = logging.getLogger("omnipotent.model_router.router")


class ModelConfig(TypedDict):
    """Configuration for a model."""
    tier: "ModelTier"
    avg_latency_ms: int
    quality_score: float
    supports_streaming: bool
    supports_function_calling: bool
    max_context: int


class ModelTier(str, Enum):
    """Model capability tiers."""
    FAST = "fast"            # Fastest response, lower quality
    BALANCED = "balanced"    # Good balance of speed/quality
    QUALITY = "quality"      # High quality, slower
    PREMIUM = "premium"      # Best quality, highest latency/cost


# Tiers from weakest to strongest. ModelTier is a str Enum, so comparing two
# tiers directly compares their *names* alphabetically ("quality" > "premium",
# "balanced" < "fast"), which silently inverted the SLA quality floor. Every
# ordering question about tiers goes through TIER_ORDER / tier_rank.
TIER_ORDER: list["ModelTier"] = [
    ModelTier.FAST,
    ModelTier.BALANCED,
    ModelTier.QUALITY,
    ModelTier.PREMIUM,
]


def tier_rank(tier: "ModelTier") -> int:
    """Position of `tier` in the weakest-to-strongest order."""
    return TIER_ORDER.index(tier)


# Router-owned heuristics per tier: rough latency expectation and the relative
# quality weight _select_optimal_model scores with. Model IDs, context windows
# and prices never live here — those belong to app/model_catalog.py.
_TIER_PROFILES: dict["ModelTier", tuple[int, float]] = {
    ModelTier.FAST: (500, 0.7),
    ModelTier.BALANCED: (1500, 0.85),
    ModelTier.QUALITY: (2000, 0.9),
    ModelTier.PREMIUM: (5000, 0.95),
}


def _resolve_or_default(model_id: str, provider: str, fast: bool) -> str:
    """An operator override if the catalog knows it, else the provider default.

    An override the catalog cannot resolve gets the default rather than a
    guessed tier/context: the router has to be able to describe every model it
    may answer with.
    """
    if model_id and model_catalog.is_known(model_id):
        return model_id
    if model_id:
        log.warning(
            "COPILOT_%sMODEL=%r is not in the model catalog; the model router "
            "falls back to the %s provider default",
            "FAST_" if fast else "", model_id, provider,
        )
    return model_catalog.default_model(provider, fast=fast)


def _build_model_configs() -> dict[str, "ModelConfig"]:
    """MODEL_CONFIGS for the active provider, from app.model_catalog.

    The table used to hardcode five model IDs across two providers — including
    the retired ``claude-3-opus-latest`` — so the router happily answered with
    models this deployment has no key for. It now names exactly the models the
    deployment actually runs on: the configured (or catalog-default) fast model
    on the FAST tier and the main reasoning model on the PREMIUM tier. When a
    provider uses one model for both roles it sits on the BALANCED tier and
    serves every request.
    """
    settings = _get_settings()
    provider = settings.copilot_provider
    fast_id = _resolve_or_default(settings.copilot_fast_model.strip(), provider, fast=True)
    main_id = _resolve_or_default(settings.copilot_model.strip(), provider, fast=False)

    tiers: dict[str, ModelTier] = (
        {main_id: ModelTier.BALANCED}
        if fast_id == main_id
        else {fast_id: ModelTier.FAST, main_id: ModelTier.PREMIUM}
    )

    configs: dict[str, ModelConfig] = {}
    for model_id, tier in tiers.items():
        info = model_catalog.get(model_id)
        avg_latency_ms, quality_score = _TIER_PROFILES[tier]
        configs[model_id] = {
            "tier": tier,
            "avg_latency_ms": avg_latency_ms,
            "quality_score": quality_score,
            "supports_streaming": True,
            "supports_function_calling": model_catalog.CAP_TOOLS in info.capabilities,
            "max_context": info.max_context,
        }
    return configs


def _get_settings():
    # Local import so importing this module never freezes settings (tests patch
    # the environment and clear get_settings' cache).
    from app.config import get_settings

    return get_settings()


class CustomerTier(str, Enum):
    """Customer subscription tiers."""
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass
class SLARequirements:
    """SLA requirements for a request."""
    max_latency_ms: int | None = None
    min_quality: ModelTier = ModelTier.BALANCED
    max_cost: float | None = None
    requires_streaming: bool = False
    requires_function_calling: bool = False


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    model_id: str
    model_tier: ModelTier
    reason: str
    complexity: ComplexityScore
    cost_estimate: CostEstimate | None = None

    # Routing factors
    factors: dict[str, Any] = field(default_factory=dict)

    # Fallback options
    fallback_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_tier": self.model_tier.value,
            "reason": self.reason,
            "complexity": self.complexity.to_dict(),
            "cost_estimate": self.cost_estimate.to_dict() if self.cost_estimate else None,
            "factors": self.factors,
            "fallback_models": self.fallback_models,
        }


class AdaptiveModelRouter:
    """Routes LLM requests to optimal model based on multiple factors.

    Routing factors:
    - Query complexity (semantic, reasoning depth, domain breadth)
    - SLA requirements (latency, quality, cost constraints)
    - Customer tier (feature access, priority)
    - Current load and availability
    - Cost optimization
    """

    # Customer tier restrictions
    TIER_ALLOWED_MODELS = {
        CustomerTier.FREE: [ModelTier.FAST],
        CustomerTier.STARTER: [ModelTier.FAST, ModelTier.BALANCED],
        CustomerTier.PROFESSIONAL: [ModelTier.FAST, ModelTier.BALANCED, ModelTier.QUALITY],
        CustomerTier.ENTERPRISE: [ModelTier.FAST, ModelTier.BALANCED, ModelTier.QUALITY, ModelTier.PREMIUM],
    }

    # Complexity to model tier mapping
    COMPLEXITY_TIER_MAP = {
        ComplexityLevel.TRIVIAL: ModelTier.FAST,
        ComplexityLevel.LOW: ModelTier.FAST,
        ComplexityLevel.MEDIUM: ModelTier.BALANCED,
        ComplexityLevel.HIGH: ModelTier.QUALITY,
        ComplexityLevel.CRITICAL: ModelTier.PREMIUM,
    }

    def __init__(
        self,
        complexity_analyzer: ComplexityAnalyzer | None = None,
        cost_optimizer: CostOptimizer | None = None,
        default_model: str = "",
    ):
        self.complexity_analyzer = complexity_analyzer or ComplexityAnalyzer()
        self.cost_optimizer = cost_optimizer or CostOptimizer()
        # Built per instance from the catalog + the active provider, not a class
        # constant: the served path (app/model_router/api_router.py) constructs
        # this with defaults, so a class-level table hardcoding another
        # provider's models routed every deployment at models it has no key for.
        self.MODEL_CONFIGS: dict[str, ModelConfig] = _build_model_configs()
        settings = _get_settings()
        self.default_model = (
            default_model
            or settings.copilot_model.strip()
            or model_catalog.default_model(settings.copilot_provider)
        )

        # Runtime state
        self._model_availability: dict[str, bool] = {
            m: True for m in self.MODEL_CONFIGS
        }
        self._routing_history: list[dict[str, Any]] = []

    def route(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        customer_tier: CustomerTier = CustomerTier.PROFESSIONAL,
        sla: SLARequirements | None = None,
        incident_severity: str | None = None,
        preferred_provider: str | None = None,
    ) -> RoutingDecision:
        """Route a query to the optimal model.

        Args:
            query: User query
            context: Additional context (alert data, service info, etc.)
            customer_tier: Customer's subscription tier
            sla: SLA requirements
            incident_severity: If part of an incident (P0-P4)
            preferred_provider: "anthropic" or "openai" preference

        Returns:
            RoutingDecision with selected model and reasoning
        """
        sla = sla or SLARequirements()

        # Step 1: Analyze complexity
        complexity = self.complexity_analyzer.analyze(
            query=query,
            context=context,
            incident_severity=incident_severity,
        )

        # Step 2: Determine target tier based on complexity
        target_tier = self.COMPLEXITY_TIER_MAP.get(
            complexity.level, ModelTier.BALANCED
        )

        # Step 3: Adjust for SLA requirements — min_quality is a floor, so it may
        # only ever raise the tier chosen from complexity, never lower it.
        floor_applied = tier_rank(sla.min_quality) > tier_rank(target_tier)
        if floor_applied:
            target_tier = sla.min_quality

        # Step 4: Filter by customer tier
        allowed_tiers = self.TIER_ALLOWED_MODELS.get(
            customer_tier, [ModelTier.FAST]
        )

        # Cap at highest allowed tier
        highest_allowed_idx = max(tier_rank(t) for t in allowed_tiers)
        max_tier = TIER_ORDER[highest_allowed_idx]
        if tier_rank(target_tier) > highest_allowed_idx:
            target_tier = max_tier

        # Step 5: Find candidate models
        candidates = self._get_candidate_models(
            target_tier=target_tier,
            max_tier=max_tier,
            sla=sla,
            preferred_provider=preferred_provider,
        )

        if not candidates:
            # Fallback to default
            return RoutingDecision(
                model_id=self.default_model,
                model_tier=ModelTier.BALANCED,
                reason="No candidates matched requirements, using default",
                complexity=complexity,
                factors={
                    "target_tier": target_tier.value,
                    "fallback": True,
                },
            )

        # Step 6: Apply cost optimization
        best_model = self._select_optimal_model(
            candidates=candidates,
            complexity=complexity,
            sla=sla,
        )

        # Step 7: Calculate cost estimate
        cost_estimate = None
        if self.cost_optimizer and complexity.estimated_tokens > 0:
            cost_estimate = self.cost_optimizer.estimate_cost(
                model_id=best_model,
                input_tokens=complexity.estimated_tokens // 4,
                output_tokens=complexity.estimated_tokens * 3 // 4,
            )

        # Build routing factors
        factors = {
            "target_tier": target_tier.value,
            "customer_tier": customer_tier.value,
            "complexity_level": complexity.level.value,
            "complexity_score": complexity.overall_score,
            "latency_requirement": sla.max_latency_ms,
            "candidates_considered": len(candidates),
            "min_quality": sla.min_quality.value,
            "min_quality_floor_applied": floor_applied,
        }

        # Get fallback models
        fallbacks = [m for m in candidates if m != best_model][:3]

        # Build reason
        reason = self._build_reason(
            model=best_model,
            complexity=complexity,
            customer_tier=customer_tier,
            sla=sla,
            floor_applied=floor_applied,
        )

        # Record routing decision
        self._record_routing(
            query_hash=hash(query[:100]),
            model=best_model,
            complexity=complexity,
            customer_tier=customer_tier,
        )

        model_config = self.MODEL_CONFIGS[best_model]
        model_tier = model_config["tier"]
        assert isinstance(model_tier, ModelTier)

        return RoutingDecision(
            model_id=best_model,
            model_tier=model_tier,
            reason=reason,
            complexity=complexity,
            cost_estimate=cost_estimate,
            factors=factors,
            fallback_models=fallbacks,
        )

    def _get_candidate_models(
        self,
        target_tier: ModelTier,
        sla: SLARequirements,
        preferred_provider: str | None,
        max_tier: ModelTier = ModelTier.PREMIUM,
    ) -> list[str]:
        """Get candidate models matching requirements.

        Models above `max_tier` (the customer plan's ceiling) are never
        candidates: with model rates now coming from the catalog, the scoring in
        _select_optimal_model can no longer be relied on to keep a premium model
        away from a capped plan by price alone. If nothing sits inside the
        [target, max] window — the catalog-derived table has models on two tiers,
        not four — the strongest allowed models below the target are used, so a
        capped plan degrades downward rather than escaping upward.
        """
        candidates = []
        below_window: dict[int, list[str]] = {}

        target_idx = tier_rank(target_tier)
        max_idx = tier_rank(max_tier)

        for model_id, config in self.MODEL_CONFIGS.items():
            # Check availability
            if not self._model_availability.get(model_id, True):
                continue

            # Check tier ceiling (customer plan)
            model_tier_idx = tier_rank(config["tier"])
            if model_tier_idx > max_idx:
                continue

            # Check latency requirement
            if sla.max_latency_ms and config["avg_latency_ms"] > sla.max_latency_ms:
                continue

            # Check capability requirements
            if sla.requires_streaming and not config["supports_streaming"]:
                continue
            if sla.requires_function_calling and not config["supports_function_calling"]:
                continue

            # Check provider preference
            if preferred_provider:
                if preferred_provider == "anthropic" and not model_id.startswith("claude"):
                    continue
                if preferred_provider == "openai" and not model_id.startswith("gpt"):
                    continue

            # Check tier floor (allow target or higher quality); keep what falls
            # below as the degrade-downward fallback described above.
            if model_tier_idx < target_idx:
                below_window.setdefault(model_tier_idx, []).append(model_id)
                continue

            candidates.append(model_id)

        if not candidates and below_window:
            candidates = below_window[max(below_window)]

        return candidates

    def _select_optimal_model(
        self,
        candidates: list[str],
        complexity: ComplexityScore,
        sla: SLARequirements,
    ) -> str:
        """Select optimal model from candidates."""
        if not candidates:
            return self.default_model

        if len(candidates) == 1:
            return candidates[0]

        # Score each candidate
        scores: dict[str, float] = {}

        for model_id in candidates:
            config = self.MODEL_CONFIGS[model_id]
            score = 0.0

            # Quality score (higher is better for complex queries)
            quality_weight = complexity.overall_score
            score += config["quality_score"] * quality_weight * 0.4

            # Latency score (lower is better for simple queries)
            latency_weight = 1 - complexity.overall_score
            max_latency = 5000
            latency_score = 1 - (config["avg_latency_ms"] / max_latency)
            score += latency_score * latency_weight * 0.3

            # Cost score (lower cost is better when quality isn't critical)
            if self.cost_optimizer:
                cost_info = self.cost_optimizer.get_model_cost(model_id)
                if cost_info:
                    max_cost = 0.1  # Normalize against max expected cost
                    cost_score = 1 - (cost_info.avg_cost_per_1k / max_cost)
                    cost_weight = 0.3 if complexity.overall_score < 0.5 else 0.15
                    score += cost_score * cost_weight

            # SLA compliance bonus
            if sla.max_latency_ms:
                if config["avg_latency_ms"] < sla.max_latency_ms * 0.8:
                    score += 0.1  # Bonus for comfortable margin

            scores[model_id] = score

        # Select highest scoring
        return max(scores, key=lambda m: scores[m])

    def _build_reason(
        self,
        model: str,
        complexity: ComplexityScore,
        customer_tier: CustomerTier,
        sla: SLARequirements,
        floor_applied: bool = False,
    ) -> str:
        """Build human-readable routing reason."""
        config = self.MODEL_CONFIGS[model]
        parts = []

        # Complexity-based reason
        if complexity.level == ComplexityLevel.CRITICAL:
            parts.append("Critical query requiring highest quality")
        elif complexity.level == ComplexityLevel.HIGH:
            parts.append("Complex multi-domain reasoning detected")
        elif complexity.level == ComplexityLevel.MEDIUM:
            parts.append("Moderate complexity with balanced requirements")
        elif complexity.level == ComplexityLevel.LOW:
            parts.append("Simple query optimized for speed")
        else:
            parts.append("Trivial query using fastest model")

        # Quality floor: say so, otherwise the reason claims "fastest model"
        # while the SLA has just forced a more expensive one.
        if floor_applied:
            parts.append(f"raised to the {sla.min_quality.value} tier by the min_quality SLA")

        # Customer tier constraint
        if customer_tier == CustomerTier.FREE:
            parts.append("free tier limited to fast models")
        elif customer_tier == CustomerTier.ENTERPRISE:
            parts.append("enterprise tier with full model access")

        # SLA constraint
        if sla.max_latency_ms and config["avg_latency_ms"] < sla.max_latency_ms:
            parts.append(f"meets {sla.max_latency_ms}ms latency SLA")

        return "; ".join(parts)

    def _record_routing(
        self,
        query_hash: int,
        model: str,
        complexity: ComplexityScore,
        customer_tier: CustomerTier,
    ) -> None:
        """Record routing decision for analytics."""
        self._routing_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query_hash": query_hash,
            "model": model,
            "complexity_level": complexity.level.value,
            "complexity_score": complexity.overall_score,
            "customer_tier": customer_tier.value,
        })

        # Keep last 1000 entries
        if len(self._routing_history) > 1000:
            self._routing_history = self._routing_history[-1000:]

    def set_model_availability(self, model_id: str, available: bool) -> None:
        """Set model availability status."""
        self._model_availability[model_id] = available

    def get_routing_stats(self) -> dict[str, Any]:
        """Get routing statistics."""
        if not self._routing_history:
            return {
                "total_routed": 0,
                "by_model": {},
                "by_complexity": {},
                "by_customer_tier": {},
            }

        by_model: dict[str, int] = {}
        by_complexity: dict[str, int] = {}
        by_tier: dict[str, int] = {}

        for entry in self._routing_history:
            model = entry["model"]
            by_model[model] = by_model.get(model, 0) + 1

            complexity = entry["complexity_level"]
            by_complexity[complexity] = by_complexity.get(complexity, 0) + 1

            tier = entry["customer_tier"]
            by_tier[tier] = by_tier.get(tier, 0) + 1

        return {
            "total_routed": len(self._routing_history),
            "by_model": by_model,
            "by_complexity": by_complexity,
            "by_customer_tier": by_tier,
        }

    def get_model_recommendation(
        self,
        use_case: str,
        budget: str = "balanced",
    ) -> dict[str, Any]:
        """Get model recommendation for a use case.

        Args:
            use_case: "chat", "analysis", "coding", "incident_response"
            budget: "economy", "balanced", "quality"

        Returns:
            Recommendation with reasoning
        """
        # Weakest and strongest of the models this deployment actually runs on
        # (see _build_model_configs) — recommending anything else would name a
        # model the deployment has no key for.
        by_tier = sorted(self.MODEL_CONFIGS, key=lambda m: tier_rank(self.MODEL_CONFIGS[m]["tier"]))
        economy_model, top_model = by_tier[0], by_tier[-1]

        recommendations = {
            "chat": {
                "economy": economy_model,
                "balanced": top_model,
                "quality": top_model,
            },
            "analysis": {
                "economy": economy_model,
                "balanced": top_model,
                "quality": top_model,
            },
            "coding": {
                "economy": economy_model,
                "balanced": top_model,
                "quality": top_model,
            },
            "incident_response": {
                "economy": top_model,
                "balanced": top_model,
                "quality": top_model,
            },
        }

        use_case_recs = recommendations.get(use_case, recommendations["analysis"])
        model_id = use_case_recs.get(budget, use_case_recs["balanced"])
        config: ModelConfig | dict[str, Any] = self.MODEL_CONFIGS.get(model_id, {})

        cost_info = None
        if self.cost_optimizer:
            cost_info = self.cost_optimizer.get_model_cost(model_id)

        return {
            "use_case": use_case,
            "budget": budget,
            "recommended_model": model_id,
            "model_tier": config.get("tier", ModelTier.BALANCED).value,
            "avg_latency_ms": config.get("avg_latency_ms"),
            "quality_score": config.get("quality_score"),
            "cost_per_1k_tokens": cost_info.avg_cost_per_1k if cost_info else None,
        }
