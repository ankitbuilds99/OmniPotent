"""Adaptive Model Router - Intelligent model selection.

Routes requests to the most appropriate model based on:
- Task complexity
- Latency requirements
- Cost constraints
- Model availability
- Historical performance
"""

from app.router.adaptive import (
    AdaptiveRouter,
    RouterConfig,
    RouteDecision,
    ModelProfile,
    create_router,
)
from app.router.classifier import (
    TaskClassifier,
    TaskComplexity,
    TaskType,
)
from app.router.metrics import (
    RouterMetrics,
    ModelPerformance,
)

__all__ = [
    "AdaptiveRouter",
    "RouterConfig",
    "RouteDecision",
    "ModelProfile",
    "create_router",
    "TaskClassifier",
    "TaskComplexity",
    "TaskType",
    "RouterMetrics",
    "ModelPerformance",
]
