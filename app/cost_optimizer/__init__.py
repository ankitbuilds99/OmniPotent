"""Advanced Cost Optimizer.

Provides comprehensive cost optimization:
- AI/LLM token cost tracking and optimization
- GPU fleet cost tracking across AWS and GCP
- Budget management and alerts
- ROI tracking for automation

Served at ``/api/cost/*`` by :mod:`app.cost_optimizer.api_router`.

Model routing is deliberately not here. This package used to carry a
``CostAwareModelRouter`` that duplicated the wired
:class:`app.model_router.adaptive_router.AdaptiveModelRouter` — two routers deciding
which model to call, by different rules, would disagree. It was deleted on
2026-08-20; the wired router is the one to extend. Two capabilities existed only in
the deleted copy and have no home yet: rate-limit awareness (backing off a provider
that returned 429) and adaptive learning from recorded call outcomes.
"""

from app.cost_optimizer.token_optimizer import (
    TokenOptimizer,
    TokenUsage,
    TokenBudget,
    OptimizationStrategy,
)
from app.cost_optimizer.budget_manager import (
    BudgetManager,
    Budget,
    BudgetAlert,
    BudgetPeriod,
)
from app.cost_optimizer.roi_tracker import (
    ROITracker,
    AutomationROI,
    CostSaving,
    ROIReport,
)
from app.cost_optimizer.gpu_tracker import (
    GPUTracker,
    GPUInstance,
    GPUAvailability,
    GPUReservation,
    GPUType,
    CloudProvider,
    InstanceType,
    CostOptimizationRecommendation,
    create_gpu_tracker,
)

__all__ = [
    "TokenOptimizer",
    "TokenUsage",
    "TokenBudget",
    "OptimizationStrategy",
    "BudgetManager",
    "Budget",
    "BudgetAlert",
    "BudgetPeriod",
    "ROITracker",
    "AutomationROI",
    "CostSaving",
    "ROIReport",
    # GPU Tracker
    "GPUTracker",
    "GPUInstance",
    "GPUAvailability",
    "GPUReservation",
    "GPUType",
    "CloudProvider",
    "InstanceType",
    "CostOptimizationRecommendation",
    "create_gpu_tracker",
]
