"""LLM module for DevOps Copilot.

Provides model routing, semantic caching, and cost optimization.
"""

from app.llm_advanced.model_router import (
    ComplexityAnalyzer,
    ModelConfig,
    ModelRouter,
    ModelTier,
    RoutingContext,
    RoutingDecision,
    TaskComplexity,
    get_model_router,
)
from app.llm_advanced.semantic_cache import (
    CacheEntry,
    CacheStats,
    SemanticCache,
    TieredSemanticCache,
    cosine_similarity,
    get_semantic_cache,
)

__all__ = [
    # Model Router
    "ComplexityAnalyzer",
    "ModelConfig",
    "ModelRouter",
    "ModelTier",
    "RoutingContext",
    "RoutingDecision",
    "TaskComplexity",
    "get_model_router",
    # Semantic Cache
    "CacheEntry",
    "CacheStats",
    "SemanticCache",
    "TieredSemanticCache",
    "cosine_similarity",
    "get_semantic_cache",
]
