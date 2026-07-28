"""Model router for intelligent LLM model selection.

Routes queries to different models based on complexity, cost, and capability
requirements. Supports fallback chains and load balancing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.llm.router")


class ModelTier(Enum):
    """Model capability tiers."""

    FAST = "fast"  # Simple queries, low latency
    STANDARD = "standard"  # General purpose
    ADVANCED = "advanced"  # Complex reasoning
    PREMIUM = "premium"  # Most capable, highest cost


class TaskComplexity(Enum):
    """Task complexity levels."""

    TRIVIAL = 1  # Simple lookups, formatting
    SIMPLE = 2  # Basic Q&A, summaries
    MODERATE = 3  # Multi-step reasoning
    COMPLEX = 4  # Analysis, planning
    EXPERT = 5  # Novel problem solving


@dataclass
class ModelConfig:
    """Configuration for an LLM model."""

    name: str
    provider: str
    tier: ModelTier
    cost_per_1k_input: float
    cost_per_1k_output: float
    max_context: int
    max_output: int
    latency_p50_ms: float
    latency_p99_ms: float
    capabilities: set[str] = field(default_factory=set)
    rate_limit_rpm: int = 1000
    is_available: bool = True
    weight: float = 1.0

    @property
    def cost_score(self) -> float:
        """Relative cost score (lower is cheaper)."""
        return self.cost_per_1k_input + self.cost_per_1k_output

    def supports(self, capability: str) -> bool:
        """Check if model supports a capability."""
        return capability in self.capabilities


MODELS: dict[str, ModelConfig] = {
    "gpt-4o-mini": ModelConfig(
        name="gpt-4o-mini",
        provider="openai",
        tier=ModelTier.FAST,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_context=128000,
        max_output=16384,
        latency_p50_ms=200,
        latency_p99_ms=1000,
        capabilities={"chat", "function_calling", "json_mode"},
    ),
    "gpt-4o": ModelConfig(
        name="gpt-4o",
        provider="openai",
        tier=ModelTier.STANDARD,
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
        max_context=128000,
        max_output=16384,
        latency_p50_ms=500,
        latency_p99_ms=3000,
        capabilities={"chat", "function_calling", "json_mode", "vision"},
    ),
    "claude-3-5-haiku": ModelConfig(
        name="claude-3-5-haiku-20241022",
        provider="anthropic",
        tier=ModelTier.FAST,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        max_context=200000,
        max_output=8192,
        latency_p50_ms=300,
        latency_p99_ms=1500,
        capabilities={"chat", "function_calling"},
    ),
    "claude-3-5-sonnet": ModelConfig(
        name="claude-3-5-sonnet-20241022",
        provider="anthropic",
        tier=ModelTier.STANDARD,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        max_output=8192,
        latency_p50_ms=800,
        latency_p99_ms=4000,
        capabilities={"chat", "function_calling", "vision", "computer_use"},
    ),
    "claude-3-opus": ModelConfig(
        name="claude-3-opus-20240229",
        provider="anthropic",
        tier=ModelTier.PREMIUM,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_context=200000,
        max_output=4096,
        latency_p50_ms=2000,
        latency_p99_ms=10000,
        capabilities={"chat", "function_calling", "vision"},
    ),
    "o1": ModelConfig(
        name="o1",
        provider="openai",
        tier=ModelTier.PREMIUM,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.06,
        max_context=200000,
        max_output=100000,
        latency_p50_ms=5000,
        latency_p99_ms=30000,
        capabilities={"chat", "reasoning"},
    ),
    "o1-mini": ModelConfig(
        name="o1-mini",
        provider="openai",
        tier=ModelTier.ADVANCED,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.012,
        max_context=128000,
        max_output=65536,
        latency_p50_ms=3000,
        latency_p99_ms=15000,
        capabilities={"chat", "reasoning"},
    ),
}


@dataclass
class RoutingContext:
    """Context for routing decisions."""

    query: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    required_capabilities: set[str] = field(default_factory=set)
    max_latency_ms: float | None = None
    max_cost: float | None = None
    preferred_providers: list[str] | None = None
    excluded_models: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """Result of model routing."""

    model: ModelConfig
    reason: str
    alternatives: list[ModelConfig]
    estimated_cost: float
    estimated_latency_ms: float
    complexity: TaskComplexity


class ComplexityAnalyzer:
    """Analyzes query complexity."""

    COMPLEXITY_PATTERNS: list[tuple[re.Pattern[str], TaskComplexity]] = [
        (re.compile(r"\b(analyze|investigate|debug|troubleshoot)\b", re.I), TaskComplexity.COMPLEX),
        (re.compile(r"\b(plan|design|architect|strategy)\b", re.I), TaskComplexity.COMPLEX),
        (re.compile(r"\b(compare|evaluate|assess)\b", re.I), TaskComplexity.MODERATE),
        (re.compile(r"\b(explain|describe|summarize)\b", re.I), TaskComplexity.SIMPLE),
        (re.compile(r"\b(list|show|get|what is)\b", re.I), TaskComplexity.TRIVIAL),
        (re.compile(r"\b(novel|creative|innovative)\b", re.I), TaskComplexity.EXPERT),
        (re.compile(r"\b(step.by.step|reasoning|proof)\b", re.I), TaskComplexity.COMPLEX),
    ]

    def analyze(self, query: str, context: dict[str, Any] | None = None) -> TaskComplexity:
        """Analyze query complexity."""
        context = context or {}

        if context.get("requires_reasoning"):
            return TaskComplexity.EXPERT

        for pattern, complexity in self.COMPLEXITY_PATTERNS:
            if pattern.search(query):
                return complexity

        word_count = len(query.split())
        if word_count < 10:
            return TaskComplexity.SIMPLE
        elif word_count < 50:
            return TaskComplexity.MODERATE
        else:
            return TaskComplexity.COMPLEX


class ModelRouter:
    """Routes queries to appropriate models."""

    def __init__(
        self,
        models: dict[str, ModelConfig] | None = None,
        default_model: str = "gpt-4o",
        complexity_analyzer: ComplexityAnalyzer | None = None,
    ):
        self._models = models or MODELS.copy()
        self._default_model = default_model
        self._analyzer = complexity_analyzer or ComplexityAnalyzer()
        self._usage_stats: dict[str, dict[str, Any]] = {}
        self._fallback_chains: dict[str, list[str]] = {
            "openai": ["gpt-4o", "gpt-4o-mini"],
            "anthropic": ["claude-3-5-sonnet", "claude-3-5-haiku"],
            "reasoning": ["o1", "o1-mini", "claude-3-opus"],
        }

    def route(self, context: RoutingContext) -> RoutingDecision:
        """Route a query to the best model."""
        complexity = self._analyzer.analyze(context.query, context.metadata)

        candidates = self._filter_candidates(context, complexity)

        if not candidates:
            log.warning("no suitable models found, using default")
            model = self._models[self._default_model]
            return RoutingDecision(
                model=model,
                reason="fallback_to_default",
                alternatives=[],
                estimated_cost=self._estimate_cost(model, context),
                estimated_latency_ms=model.latency_p50_ms,
                complexity=complexity,
            )

        scored = self._score_candidates(candidates, context, complexity)
        best = scored[0][0]
        alternatives = [m for m, _ in scored[1:4]]

        return RoutingDecision(
            model=best,
            reason=self._explain_choice(best, context, complexity),
            alternatives=alternatives,
            estimated_cost=self._estimate_cost(best, context),
            estimated_latency_ms=best.latency_p50_ms,
            complexity=complexity,
        )

    def _filter_candidates(
        self,
        context: RoutingContext,
        complexity: TaskComplexity,
    ) -> list[ModelConfig]:
        """Filter models based on constraints."""
        candidates = []

        for name, model in self._models.items():
            if not model.is_available:
                continue
            if name in context.excluded_models:
                continue

            if context.required_capabilities:
                if not context.required_capabilities.issubset(model.capabilities):
                    continue

            if context.preferred_providers:
                if model.provider not in context.preferred_providers:
                    continue

            if context.max_latency_ms:
                if model.latency_p50_ms > context.max_latency_ms:
                    continue

            if context.max_cost:
                estimated_cost = self._estimate_cost(model, context)
                if estimated_cost > context.max_cost:
                    continue

            total_tokens = context.estimated_input_tokens + context.estimated_output_tokens
            if total_tokens > model.max_context:
                continue

            candidates.append(model)

        return candidates

    def _score_candidates(
        self,
        candidates: list[ModelConfig],
        context: RoutingContext,
        complexity: TaskComplexity,
    ) -> list[tuple[ModelConfig, float]]:
        """Score and rank candidates."""
        scored = []

        for model in candidates:
            score = 0.0

            tier_match = {
                TaskComplexity.TRIVIAL: ModelTier.FAST,
                TaskComplexity.SIMPLE: ModelTier.FAST,
                TaskComplexity.MODERATE: ModelTier.STANDARD,
                TaskComplexity.COMPLEX: ModelTier.ADVANCED,
                TaskComplexity.EXPERT: ModelTier.PREMIUM,
            }

            target_tier = tier_match.get(complexity, ModelTier.STANDARD)

            if model.tier == target_tier:
                score += 100
            elif model.tier.value == target_tier.value:
                score += 50

            max_cost = max(m.cost_score for m in candidates)
            if max_cost > 0:
                cost_score = (1 - model.cost_score / max_cost) * 30
                score += cost_score

            max_latency = max(m.latency_p50_ms for m in candidates)
            if max_latency > 0:
                latency_score = (1 - model.latency_p50_ms / max_latency) * 20
                score += latency_score

            score += len(model.capabilities) * 2

            score += model.weight * 10

            scored.append((model, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _estimate_cost(self, model: ModelConfig, context: RoutingContext) -> float:
        """Estimate cost for a request."""
        input_cost = (context.estimated_input_tokens / 1000) * model.cost_per_1k_input
        output_cost = (context.estimated_output_tokens / 1000) * model.cost_per_1k_output
        return input_cost + output_cost

    def _explain_choice(
        self,
        model: ModelConfig,
        context: RoutingContext,
        complexity: TaskComplexity,
    ) -> str:
        """Explain why a model was chosen."""
        reasons = []

        reasons.append(f"complexity={complexity.name.lower()}")
        reasons.append(f"tier={model.tier.value}")

        if context.max_latency_ms:
            reasons.append(f"latency_ok={model.latency_p50_ms}ms")

        if context.required_capabilities:
            reasons.append("capabilities_match")

        return "; ".join(reasons)

    def get_fallback_chain(self, model_name: str) -> list[ModelConfig]:
        """Get fallback models for a given model."""
        model = self._models.get(model_name)
        if not model:
            return []

        for chain_name, chain in self._fallback_chains.items():
            if model_name in chain:
                idx = chain.index(model_name)
                fallbacks = chain[idx + 1 :]
                return [self._models[n] for n in fallbacks if n in self._models]

        same_provider = [
            m
            for m in self._models.values()
            if m.provider == model.provider and m.name != model_name
        ]
        same_provider.sort(key=lambda m: m.cost_score)
        return same_provider[:3]

    def record_usage(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Record model usage for analytics."""
        if model_name not in self._usage_stats:
            self._usage_stats[model_name] = {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_latency_ms": 0,
                "errors": 0,
            }

        stats = self._usage_stats[model_name]
        stats["requests"] += 1
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["total_latency_ms"] += latency_ms
        if not success:
            stats["errors"] += 1

    def get_usage_stats(self) -> dict[str, dict[str, Any]]:
        """Get usage statistics."""
        result = {}
        for name, stats in self._usage_stats.items():
            model = self._models.get(name)
            if not model:
                continue

            total_cost = (
                (stats["input_tokens"] / 1000) * model.cost_per_1k_input
                + (stats["output_tokens"] / 1000) * model.cost_per_1k_output
            )

            avg_latency = (
                stats["total_latency_ms"] / stats["requests"]
                if stats["requests"] > 0
                else 0
            )

            result[name] = {
                **stats,
                "total_cost": round(total_cost, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "error_rate": (
                    stats["errors"] / stats["requests"]
                    if stats["requests"] > 0
                    else 0
                ),
            }

        return result

    def set_model_availability(self, model_name: str, available: bool) -> None:
        """Set model availability (for circuit breaking)."""
        if model_name in self._models:
            self._models[model_name].is_available = available
            log.info(
                "model availability changed",
                extra={"model": model_name, "available": available},
            )


_router: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """Get the default model router."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
