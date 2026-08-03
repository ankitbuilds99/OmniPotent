"""Cost-Aware Model Router.

Routes AI requests to optimal models based on:
- Task complexity and requirements
- Cost constraints
- Latency requirements
- Quality requirements
- Current model performance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.cost.model_router")


class RoutingStrategy(str, Enum):
    """Model routing strategies."""
    COST_OPTIMIZED = "cost_optimized"
    QUALITY_OPTIMIZED = "quality_optimized"
    BALANCED = "balanced"
    LATENCY_OPTIMIZED = "latency_optimized"
    ADAPTIVE = "adaptive"


class ModelTier(str, Enum):
    """Model capability tiers."""
    ECONOMY = "economy"  # Cheapest, basic tasks
    STANDARD = "standard"  # Good balance
    PREMIUM = "premium"  # Best quality
    SPECIALIZED = "specialized"  # Domain-specific


@dataclass
class ModelConfig:
    """Configuration for a model."""
    name: str
    provider: str  # "openai", "anthropic", "google"
    tier: ModelTier

    # Pricing (per 1M tokens)
    input_price: float
    output_price: float

    # Capabilities
    max_context: int
    supports_functions: bool = True
    supports_vision: bool = False
    supports_json_mode: bool = True

    # Performance characteristics
    avg_latency_ms: float = 500
    quality_score: float = 0.9  # 0-1
    reliability: float = 0.99

    # Specializations
    specializations: list[str] = field(default_factory=list)

    # Rate limits
    requests_per_minute: int = 500
    tokens_per_minute: int = 100_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "tier": self.tier.value,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "max_context": self.max_context,
            "avg_latency_ms": self.avg_latency_ms,
            "quality_score": self.quality_score,
        }

    @property
    def cost_per_1k_tokens(self) -> float:
        """Average cost per 1K tokens."""
        return (self.input_price + self.output_price) / 2000


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    selected_model: ModelConfig
    reason: str
    alternatives: list[ModelConfig]
    estimated_cost: float
    estimated_latency_ms: float
    confidence: float
    fallback_model: ModelConfig | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_model": self.selected_model.name,
            "reason": self.reason,
            "alternatives": [m.name for m in self.alternatives],
            "estimated_cost": self.estimated_cost,
            "estimated_latency_ms": self.estimated_latency_ms,
            "confidence": self.confidence,
            "fallback_model": self.fallback_model.name if self.fallback_model else None,
        }


@dataclass
class ModelPerformance:
    """Tracked performance for a model."""
    model_name: str
    requests: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0
    total_tokens: int = 0
    total_cost: float = 0
    quality_ratings: list[float] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def success_rate(self) -> float:
        if self.requests == 0:
            return 1.0
        return self.successes / self.requests

    @property
    def avg_latency(self) -> float:
        if self.requests == 0:
            return 0
        return self.total_latency_ms / self.requests

    @property
    def avg_quality(self) -> float:
        if not self.quality_ratings:
            return 0.9
        return sum(self.quality_ratings[-100:]) / len(self.quality_ratings[-100:])


@dataclass
class RoutingContext:
    """Context for routing decision."""
    task_type: str
    estimated_input_tokens: int
    estimated_output_tokens: int

    # Requirements
    min_quality: float = 0.8
    max_latency_ms: float | None = None
    max_cost: float | None = None

    # Features needed
    requires_functions: bool = False
    requires_vision: bool = False
    requires_json: bool = False

    # Context
    is_retry: bool = False
    previous_model: str | None = None
    urgency: str = "normal"  # "low", "normal", "high", "critical"


class CostAwareModelRouter:
    """Routes requests to optimal AI models.

    Features:
    - Multi-provider support
    - Cost/quality/latency optimization
    - Adaptive learning from performance
    - Fallback handling
    - Rate limit awareness
    """

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        default_model: str = "gpt-4o",
    ):
        self.strategy = strategy
        self.default_model = default_model

        self._models: dict[str, ModelConfig] = {}
        self._performance: dict[str, ModelPerformance] = {}
        self._rate_limit_state: dict[str, dict[str, Any]] = {}

        self._register_default_models()

    def _register_default_models(self) -> None:
        """Register default model configurations."""
        models = [
            ModelConfig(
                name="gpt-4o",
                provider="openai",
                tier=ModelTier.STANDARD,
                input_price=2.50,
                output_price=10.00,
                max_context=128_000,
                avg_latency_ms=400,
                quality_score=0.92,
                supports_vision=True,
                specializations=["general", "coding", "analysis"],
            ),
            ModelConfig(
                name="gpt-4o-mini",
                provider="openai",
                tier=ModelTier.ECONOMY,
                input_price=0.15,
                output_price=0.60,
                max_context=128_000,
                avg_latency_ms=200,
                quality_score=0.85,
                specializations=["classification", "extraction", "simple"],
            ),
            ModelConfig(
                name="gpt-4-turbo",
                provider="openai",
                tier=ModelTier.PREMIUM,
                input_price=10.00,
                output_price=30.00,
                max_context=128_000,
                avg_latency_ms=600,
                quality_score=0.95,
                supports_vision=True,
                specializations=["complex_reasoning", "creative"],
            ),
            ModelConfig(
                name="claude-3-opus",
                provider="anthropic",
                tier=ModelTier.PREMIUM,
                input_price=15.00,
                output_price=75.00,
                max_context=200_000,
                avg_latency_ms=800,
                quality_score=0.97,
                specializations=["reasoning", "safety", "analysis"],
            ),
            ModelConfig(
                name="claude-3.5-sonnet",
                provider="anthropic",
                tier=ModelTier.STANDARD,
                input_price=3.00,
                output_price=15.00,
                max_context=200_000,
                avg_latency_ms=350,
                quality_score=0.94,
                specializations=["coding", "analysis", "general"],
            ),
            ModelConfig(
                name="claude-3-haiku",
                provider="anthropic",
                tier=ModelTier.ECONOMY,
                input_price=0.25,
                output_price=1.25,
                max_context=200_000,
                avg_latency_ms=150,
                quality_score=0.82,
                specializations=["classification", "extraction"],
            ),
            ModelConfig(
                name="gemini-1.5-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                input_price=1.25,
                output_price=5.00,
                max_context=1_000_000,
                avg_latency_ms=500,
                quality_score=0.90,
                specializations=["long_context", "multimodal"],
            ),
        ]

        for model in models:
            self._models[model.name] = model
            self._performance[model.name] = ModelPerformance(model_name=model.name)

    def route(self, context: RoutingContext) -> RoutingDecision:
        """Route a request to the optimal model.

        Args:
            context: Routing context with requirements

        Returns:
            RoutingDecision with selected model
        """
        candidates = self._filter_candidates(context)

        if not candidates:
            # Fallback to default
            default = self._models.get(self.default_model)
            if default:
                return RoutingDecision(
                    selected_model=default,
                    reason="No candidates matched requirements, using default",
                    alternatives=[],
                    estimated_cost=self._estimate_cost(default, context),
                    estimated_latency_ms=default.avg_latency_ms,
                    confidence=0.5,
                    fallback_model=None,
                )
            raise ValueError("No available models")

        # Score candidates based on strategy
        scored = []
        for model in candidates:
            score = self._score_model(model, context)
            scored.append((model, score))

        # Sort by score (descending)
        scored.sort(key=lambda x: x[1], reverse=True)

        selected = scored[0][0]
        alternatives = [m for m, _ in scored[1:4]]
        fallback = alternatives[0] if alternatives else None

        # Generate reason
        reason = self._generate_reason(selected, context)

        return RoutingDecision(
            selected_model=selected,
            reason=reason,
            alternatives=alternatives,
            estimated_cost=self._estimate_cost(selected, context),
            estimated_latency_ms=self._get_expected_latency(selected),
            confidence=min(scored[0][1], 1.0),
            fallback_model=fallback,
        )

    def _filter_candidates(self, context: RoutingContext) -> list[ModelConfig]:
        """Filter models based on hard requirements."""
        candidates = []

        for model in self._models.values():
            # Check feature requirements
            if context.requires_functions and not model.supports_functions:
                continue
            if context.requires_vision and not model.supports_vision:
                continue
            if context.requires_json and not model.supports_json_mode:
                continue

            # Check context length
            total_tokens = context.estimated_input_tokens + context.estimated_output_tokens
            if total_tokens > model.max_context:
                continue

            # Check quality requirement
            perf = self._performance.get(model.name)
            effective_quality = model.quality_score
            if perf:
                effective_quality = (model.quality_score + perf.avg_quality) / 2
            if effective_quality < context.min_quality:
                continue

            # Check latency requirement
            if context.max_latency_ms:
                expected_latency = self._get_expected_latency(model)
                if expected_latency > context.max_latency_ms:
                    continue

            # Check cost requirement
            if context.max_cost:
                estimated_cost = self._estimate_cost(model, context)
                if estimated_cost > context.max_cost:
                    continue

            # Check rate limits
            if self._is_rate_limited(model.name):
                continue

            candidates.append(model)

        return candidates

    def _score_model(self, model: ModelConfig, context: RoutingContext) -> float:
        """Score a model for the given context."""
        perf = self._performance.get(model.name)

        # Base scores
        cost = self._estimate_cost(model, context)
        latency = self._get_expected_latency(model)
        quality = model.quality_score
        if perf:
            quality = (quality + perf.avg_quality) / 2

        # Normalize scores (0-1, higher is better)
        max_cost = 0.10  # $0.10 as reference
        cost_score = max(0, 1 - (cost / max_cost))

        max_latency = 2000  # 2 seconds as reference
        latency_score = max(0, 1 - (latency / max_latency))

        quality_score = quality

        # Reliability from performance
        reliability_score = perf.success_rate if perf else model.reliability

        # Task specialization bonus
        specialization_bonus: float = 0.0
        if context.task_type in model.specializations:
            specialization_bonus = 0.1

        # Apply strategy weights
        if self.strategy == RoutingStrategy.COST_OPTIMIZED:
            weights = {"cost": 0.5, "latency": 0.15, "quality": 0.25, "reliability": 0.1}
        elif self.strategy == RoutingStrategy.QUALITY_OPTIMIZED:
            weights = {"cost": 0.1, "latency": 0.15, "quality": 0.55, "reliability": 0.2}
        elif self.strategy == RoutingStrategy.LATENCY_OPTIMIZED:
            weights = {"cost": 0.15, "latency": 0.5, "quality": 0.25, "reliability": 0.1}
        elif self.strategy == RoutingStrategy.ADAPTIVE:
            # Adjust based on context urgency
            if context.urgency == "critical":
                weights = {"cost": 0.05, "latency": 0.3, "quality": 0.45, "reliability": 0.2}
            elif context.urgency == "high":
                weights = {"cost": 0.15, "latency": 0.25, "quality": 0.4, "reliability": 0.2}
            else:
                weights = {"cost": 0.3, "latency": 0.2, "quality": 0.35, "reliability": 0.15}
        else:  # BALANCED
            weights = {"cost": 0.25, "latency": 0.2, "quality": 0.35, "reliability": 0.2}

        score = (
            weights["cost"] * cost_score +
            weights["latency"] * latency_score +
            weights["quality"] * quality_score +
            weights["reliability"] * reliability_score +
            specialization_bonus
        )

        # Penalty for retry with same model
        if context.is_retry and context.previous_model == model.name:
            score *= 0.5

        return score

    def _estimate_cost(self, model: ModelConfig, context: RoutingContext) -> float:
        """Estimate cost for request."""
        input_cost = (context.estimated_input_tokens / 1_000_000) * model.input_price
        output_cost = (context.estimated_output_tokens / 1_000_000) * model.output_price
        return input_cost + output_cost

    def _get_expected_latency(self, model: ModelConfig) -> float:
        """Get expected latency for model."""
        perf = self._performance.get(model.name)
        if perf and perf.requests > 10:
            return (model.avg_latency_ms + perf.avg_latency) / 2
        return model.avg_latency_ms

    def _is_rate_limited(self, model_name: str) -> bool:
        """Check if model is currently rate limited."""
        state = self._rate_limit_state.get(model_name)
        if not state:
            return False

        if state.get("limited_until"):
            return datetime.now(timezone.utc) < state["limited_until"]

        return False

    def _generate_reason(self, model: ModelConfig, context: RoutingContext) -> str:
        """Generate human-readable reason for selection."""
        reasons = []

        if context.task_type in model.specializations:
            reasons.append(f"specialized for {context.task_type}")

        if self.strategy == RoutingStrategy.COST_OPTIMIZED:
            reasons.append("cost-optimized selection")
        elif self.strategy == RoutingStrategy.QUALITY_OPTIMIZED:
            reasons.append("quality-optimized selection")

        perf = self._performance.get(model.name)
        if perf and perf.success_rate > 0.98:
            reasons.append(f"high reliability ({perf.success_rate:.1%})")

        return f"Selected {model.name}: " + ", ".join(reasons) if reasons else f"Selected {model.name}"

    def record_result(
        self,
        model_name: str,
        success: bool,
        latency_ms: float,
        tokens: int,
        cost: float,
        quality_rating: float | None = None,
    ) -> None:
        """Record result for performance tracking.

        Args:
            model_name: Model used
            success: Whether request succeeded
            latency_ms: Actual latency
            tokens: Tokens used
            cost: Actual cost
            quality_rating: Optional quality rating (0-1)
        """
        if model_name not in self._performance:
            self._performance[model_name] = ModelPerformance(model_name=model_name)

        perf = self._performance[model_name]
        perf.requests += 1
        if success:
            perf.successes += 1
        else:
            perf.failures += 1
        perf.total_latency_ms += latency_ms
        perf.total_tokens += tokens
        perf.total_cost += cost
        if quality_rating is not None:
            perf.quality_ratings.append(quality_rating)
        perf.last_updated = datetime.now(timezone.utc)

    def record_rate_limit(
        self,
        model_name: str,
        retry_after_seconds: int = 60,
    ) -> None:
        """Record a rate limit hit."""
        self._rate_limit_state[model_name] = {
            "limited_until": datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds),
            "recorded_at": datetime.now(timezone.utc),
        }
        log.warning("Rate limited: %s for %ds", model_name, retry_after_seconds)

    def register_model(self, model: ModelConfig) -> None:
        """Register a new model."""
        self._models[model.name] = model
        self._performance[model.name] = ModelPerformance(model_name=model.name)
        log.info("Registered model: %s (%s)", model.name, model.tier.value)

    def get_model(self, name: str) -> ModelConfig | None:
        """Get a model by name."""
        return self._models.get(name)

    def list_models(
        self,
        tier: ModelTier | None = None,
        provider: str | None = None,
    ) -> list[ModelConfig]:
        """List available models."""
        models = list(self._models.values())

        if tier:
            models = [m for m in models if m.tier == tier]
        if provider:
            models = [m for m in models if m.provider == provider]

        return models

    def get_performance(self, model_name: str) -> ModelPerformance | None:
        """Get performance stats for a model."""
        return self._performance.get(model_name)

    def get_stats(self) -> dict[str, Any]:
        """Get router statistics."""
        total_requests = sum(p.requests for p in self._performance.values())
        total_cost = sum(p.total_cost for p in self._performance.values())

        by_tier: dict[str, dict[str, int | float]] = {}
        for model in self._models.values():
            tier = model.tier.value
            if tier not in by_tier:
                by_tier[tier] = {"models": 0, "requests": 0, "cost": 0.0}
            by_tier[tier]["models"] = int(by_tier[tier]["models"]) + 1
            perf = self._performance.get(model.name)
            if perf:
                by_tier[tier]["requests"] = int(by_tier[tier]["requests"]) + perf.requests
                by_tier[tier]["cost"] = float(by_tier[tier]["cost"]) + perf.total_cost

        return {
            "total_models": len(self._models),
            "total_requests": total_requests,
            "total_cost": total_cost,
            "strategy": self.strategy.value,
            "by_tier": by_tier,
            "rate_limited_models": len([
                m for m in self._rate_limit_state.values()
                if m.get("limited_until") and datetime.now(timezone.utc) < m["limited_until"]
            ]),
        }


def create_model_router(
    strategy: RoutingStrategy = RoutingStrategy.BALANCED,
    default_model: str = "gpt-4o",
) -> CostAwareModelRouter:
    """Factory function to create a model router."""
    return CostAwareModelRouter(
        strategy=strategy,
        default_model=default_model,
    )
