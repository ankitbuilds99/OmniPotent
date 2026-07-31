"""Adaptive Router - Intelligent model selection based on task characteristics."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from app.router.classifier import TaskClassifier, TaskComplexity, TaskType, ClassificationResult
from app.router.metrics import RouterMetrics, ModelPerformance

log = logging.getLogger("devcopilot.router.adaptive")


@dataclass
class ModelProfile:
    """Profile of a model's capabilities and costs."""
    name: str
    provider: str  # anthropic, openai, etc.

    # Capabilities
    max_context_tokens: int = 200000
    supports_tools: bool = True
    supports_vision: bool = False

    # Performance characteristics
    typical_latency_ms: float = 2000.0
    tokens_per_second: float = 50.0

    # Cost per 1M tokens
    input_cost_per_1m: float = 3.0
    output_cost_per_1m: float = 15.0

    # Quality by complexity
    quality_by_complexity: dict[str, float] = field(default_factory=lambda: {
        "trivial": 0.95,
        "simple": 0.95,
        "moderate": 0.90,
        "complex": 0.85,
        "expert": 0.80,
    })

    # Routing weights (higher = prefer)
    weight: float = 1.0
    enabled: bool = True

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a request."""
        input_cost = input_tokens * self.input_cost_per_1m / 1_000_000
        output_cost = output_tokens * self.output_cost_per_1m / 1_000_000
        return input_cost + output_cost


@dataclass
class RouteDecision:
    """Result of a routing decision."""
    model: str
    reason: str
    confidence: float
    alternatives: list[str]
    estimated_latency_ms: float
    estimated_cost_usd: float
    classification: ClassificationResult
    fallback_model: str | None = None


@dataclass
class RouterConfig:
    """Configuration for the adaptive router."""
    # Default models
    default_model: str = "claude-sonnet-4-20250514"
    fast_model: str = "claude-3-haiku-20240307"
    expert_model: str = "claude-3-opus-20240229"

    # Routing behavior
    use_fast_for_trivial: bool = True
    use_expert_for_complex: bool = False
    prefer_cost_over_quality: bool = False

    # Thresholds
    max_latency_ms: float = 60000.0
    max_cost_per_request_usd: float = 1.0

    # Fallback
    enable_fallback: bool = True
    fallback_on_timeout: bool = True
    fallback_on_error: bool = True

    # Learning
    learn_from_feedback: bool = True
    min_samples_for_learning: int = 10


# Default model profiles
DEFAULT_PROFILES = [
    ModelProfile(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        max_context_tokens=200000,
        typical_latency_ms=2500,
        tokens_per_second=80,
        input_cost_per_1m=3.0,
        output_cost_per_1m=15.0,
        quality_by_complexity={"trivial": 0.98, "simple": 0.97, "moderate": 0.95, "complex": 0.92, "expert": 0.88},
        weight=1.0,
    ),
    ModelProfile(
        name="claude-3-haiku-20240307",
        provider="anthropic",
        max_context_tokens=200000,
        typical_latency_ms=500,
        tokens_per_second=150,
        input_cost_per_1m=0.25,
        output_cost_per_1m=1.25,
        quality_by_complexity={"trivial": 0.95, "simple": 0.92, "moderate": 0.85, "complex": 0.70, "expert": 0.55},
        weight=0.8,
    ),
    ModelProfile(
        name="claude-3-opus-20240229",
        provider="anthropic",
        max_context_tokens=200000,
        typical_latency_ms=5000,
        tokens_per_second=40,
        input_cost_per_1m=15.0,
        output_cost_per_1m=75.0,
        quality_by_complexity={"trivial": 0.99, "simple": 0.99, "moderate": 0.98, "complex": 0.96, "expert": 0.94},
        weight=0.6,  # Lower weight due to cost
    ),
    ModelProfile(
        name="gpt-4o",
        provider="openai",
        max_context_tokens=128000,
        typical_latency_ms=2000,
        tokens_per_second=100,
        input_cost_per_1m=5.0,
        output_cost_per_1m=15.0,
        quality_by_complexity={"trivial": 0.97, "simple": 0.96, "moderate": 0.93, "complex": 0.90, "expert": 0.85},
        weight=0.9,
    ),
    ModelProfile(
        name="gpt-4-turbo",
        provider="openai",
        max_context_tokens=128000,
        typical_latency_ms=3000,
        tokens_per_second=60,
        input_cost_per_1m=10.0,
        output_cost_per_1m=30.0,
        quality_by_complexity={"trivial": 0.98, "simple": 0.97, "moderate": 0.94, "complex": 0.91, "expert": 0.87},
        weight=0.7,
    ),
]


class AdaptiveRouter:
    """Intelligently routes requests to the most appropriate model.

    Considers:
    - Task complexity and type
    - Model capabilities and costs
    - Historical performance
    - Latency requirements
    - Budget constraints
    """

    def __init__(
        self,
        config: RouterConfig | None = None,
        profiles: list[ModelProfile] | None = None,
    ):
        """Initialize the router.

        Args:
            config: Router configuration
            profiles: Model profiles (uses defaults if not provided)
        """
        self.config = config or RouterConfig()
        self._profiles = {p.name: p for p in (profiles or DEFAULT_PROFILES)}
        self._classifier = TaskClassifier()
        self._metrics = RouterMetrics()

        log.info(
            "AdaptiveRouter initialized with %d models: %s",
            len(self._profiles),
            list(self._profiles.keys()),
        )

    def route(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> RouteDecision:
        """Route a request to the best model.

        Args:
            query: The user's query
            context: Optional context (history, etc.)
            constraints: Optional constraints (max_cost, max_latency)

        Returns:
            RouteDecision with selected model and reasoning
        """
        # Classify the task
        classification = self._classifier.classify(query, context)

        # Apply constraints
        max_cost = constraints.get("max_cost_usd", self.config.max_cost_per_request_usd) if constraints else self.config.max_cost_per_request_usd
        max_latency = constraints.get("max_latency_ms", self.config.max_latency_ms) if constraints else self.config.max_latency_ms

        # Score each model
        scores = self._score_models(classification, max_cost, max_latency)

        # Select best model
        best_model = max(scores, key=scores.get)
        profile = self._profiles[best_model]

        # Get alternatives
        sorted_models = sorted(scores.keys(), key=lambda m: scores[m], reverse=True)
        alternatives = sorted_models[1:4]  # Top 3 alternatives

        # Determine fallback
        fallback = None
        if self.config.enable_fallback:
            fallback = self._select_fallback(best_model, alternatives)

        # Estimate latency and cost
        estimated_tokens = self._classifier.estimate_tokens(query, classification.complexity)
        estimated_latency = profile.typical_latency_ms + (estimated_tokens / profile.tokens_per_second) * 1000
        estimated_cost = profile.estimate_cost(int(estimated_tokens * 0.7), int(estimated_tokens * 0.3))

        # Generate reasoning
        reason = self._generate_reason(
            best_model, classification, profile, scores[best_model]
        )

        return RouteDecision(
            model=best_model,
            reason=reason,
            confidence=classification.confidence,
            alternatives=alternatives,
            estimated_latency_ms=estimated_latency,
            estimated_cost_usd=estimated_cost,
            classification=classification,
            fallback_model=fallback,
        )

    def _score_models(
        self,
        classification: ClassificationResult,
        max_cost: float,
        max_latency: float,
    ) -> dict[str, float]:
        """Score each model for the given task."""
        scores: dict[str, float] = {}
        complexity = classification.complexity.value

        for name, profile in self._profiles.items():
            if not profile.enabled:
                continue

            # Base quality score for this complexity
            quality = profile.quality_by_complexity.get(complexity, 0.8)

            # Latency penalty
            latency_ratio = profile.typical_latency_ms / max_latency
            latency_score = max(0, 1 - latency_ratio)

            # Cost penalty
            estimated_tokens = 5000  # Rough estimate
            estimated_cost = profile.estimate_cost(int(estimated_tokens * 0.7), int(estimated_tokens * 0.3))
            cost_ratio = estimated_cost / max_cost if max_cost > 0 else 0
            cost_score = max(0, 1 - cost_ratio)

            # Historical performance bonus
            perf = self._metrics.get_model_performance(name)
            history_bonus = 0
            if perf and perf.total_requests >= self.config.min_samples_for_learning:
                history_bonus = (perf.success_rate - 0.9) * 0.5  # Bonus for >90% success

            # Combine scores
            if self.config.prefer_cost_over_quality:
                score = (quality * 0.3 + latency_score * 0.2 + cost_score * 0.4 + history_bonus) * profile.weight
            else:
                score = (quality * 0.5 + latency_score * 0.2 + cost_score * 0.2 + history_bonus) * profile.weight

            scores[name] = score

        return scores

    def _select_fallback(self, primary: str, alternatives: list[str]) -> str | None:
        """Select a fallback model."""
        # Prefer Haiku as fallback (fast and cheap)
        if self.config.fast_model in self._profiles and self.config.fast_model != primary:
            return self.config.fast_model

        # Otherwise use first alternative
        return alternatives[0] if alternatives else None

    def _generate_reason(
        self,
        model: str,
        classification: ClassificationResult,
        profile: ModelProfile,
        score: float,
    ) -> str:
        """Generate human-readable reasoning for the routing decision."""
        parts = []

        # Complexity-based reasoning
        if classification.complexity == TaskComplexity.TRIVIAL and self.config.use_fast_for_trivial:
            parts.append("Simple task routed to fast model")
        elif classification.complexity == TaskComplexity.EXPERT and self.config.use_expert_for_complex:
            parts.append("Expert-level task routed to most capable model")
        else:
            parts.append(f"Selected based on {classification.complexity.value} complexity")

        # Quality reasoning
        quality = profile.quality_by_complexity.get(classification.complexity.value, 0.8)
        parts.append(f"expected quality {quality:.0%}")

        # Cost reasoning
        if profile.input_cost_per_1m < 1.0:
            parts.append("cost-effective")

        return "; ".join(parts)

    def record_result(
        self,
        decision: RouteDecision,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        confidence: float = 0.0,
        correct: bool | None = None,
    ) -> None:
        """Record the result of a routing decision for learning.

        Args:
            decision: The routing decision made
            latency_ms: Actual latency
            input_tokens: Actual input tokens
            output_tokens: Actual output tokens
            success: Whether the request succeeded
            confidence: Model's confidence in the result
            correct: Whether the result was correct (if verifiable)
        """
        profile = self._profiles.get(decision.model)
        cost = profile.estimate_cost(input_tokens, output_tokens) if profile else 0.0

        self._metrics.record_request(
            model=decision.model,
            complexity=decision.classification.complexity.value,
            task_type=decision.classification.task_type.value,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            success=success,
            confidence=confidence,
            correct=correct,
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get routing metrics summary."""
        return self._metrics.get_summary()

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Get routing recommendations."""
        return self._metrics.get_recommendations()

    def add_model(self, profile: ModelProfile) -> None:
        """Add or update a model profile."""
        self._profiles[profile.name] = profile
        log.info("Added model profile: %s", profile.name)

    def disable_model(self, name: str) -> bool:
        """Disable a model from routing."""
        if name in self._profiles:
            self._profiles[name].enabled = False
            log.info("Disabled model: %s", name)
            return True
        return False

    def enable_model(self, name: str) -> bool:
        """Enable a model for routing."""
        if name in self._profiles:
            self._profiles[name].enabled = True
            log.info("Enabled model: %s", name)
            return True
        return False


def create_router(
    default_model: str = "claude-sonnet-4-20250514",
    fast_model: str = "claude-3-haiku-20240307",
    **kwargs,
) -> AdaptiveRouter:
    """Factory function to create an adaptive router."""
    config = RouterConfig(
        default_model=default_model,
        fast_model=fast_model,
        **kwargs,
    )
    return AdaptiveRouter(config=config)
