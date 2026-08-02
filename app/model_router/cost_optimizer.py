"""Cost Optimizer for Model Routing.

Manages model costs and optimizes routing for cost efficiency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from collections import defaultdict

log = logging.getLogger("devcopilot.model_router.cost")


@dataclass
class ModelCost:
    """Cost structure for a model."""
    model_id: str
    input_cost_per_1k: float  # $ per 1000 input tokens
    output_cost_per_1k: float  # $ per 1000 output tokens
    cached_input_cost_per_1k: float = 0.0  # $ per 1000 cached input tokens
    batch_discount: float = 0.5  # Multiplier for batch requests

    @property
    def avg_cost_per_1k(self) -> float:
        """Average cost per 1000 tokens (assuming 1:3 input:output ratio)."""
        return (self.input_cost_per_1k + 3 * self.output_cost_per_1k) / 4


@dataclass
class CostEstimate:
    """Estimated cost for a request."""
    model_id: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float
    with_caching: float = 0.0
    with_batching: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_cost": round(self.estimated_cost, 6),
            "with_caching": round(self.with_caching, 6),
            "with_batching": round(self.with_batching, 6),
        }


@dataclass
class UsageRecord:
    """Record of model usage."""
    model_id: str
    input_tokens: int
    output_tokens: int
    cost: float
    timestamp: datetime
    customer_id: str | None = None
    request_type: str = "inference"


class CostOptimizer:
    """Optimizes model selection for cost efficiency.

    Features:
    - Real-time cost tracking per model
    - Budget management per customer/project
    - Cost prediction for queries
    - Batch optimization recommendations
    """

    # Current model costs (as of implementation date)
    DEFAULT_COSTS = {
        "claude-3-5-sonnet-latest": ModelCost(
            model_id="claude-3-5-sonnet-latest",
            input_cost_per_1k=0.003,
            output_cost_per_1k=0.015,
            cached_input_cost_per_1k=0.0003,
        ),
        "claude-3-5-haiku-latest": ModelCost(
            model_id="claude-3-5-haiku-latest",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.005,
            cached_input_cost_per_1k=0.0001,
        ),
        "claude-3-opus-latest": ModelCost(
            model_id="claude-3-opus-latest",
            input_cost_per_1k=0.015,
            output_cost_per_1k=0.075,
            cached_input_cost_per_1k=0.0015,
        ),
        "gpt-4o": ModelCost(
            model_id="gpt-4o",
            input_cost_per_1k=0.005,
            output_cost_per_1k=0.015,
        ),
        "gpt-4o-mini": ModelCost(
            model_id="gpt-4o-mini",
            input_cost_per_1k=0.00015,
            output_cost_per_1k=0.0006,
        ),
    }

    def __init__(
        self,
        model_costs: dict[str, ModelCost] | None = None,
        budget_limit_daily: float | None = None,
        budget_limit_monthly: float | None = None,
    ):
        self.model_costs = model_costs or self.DEFAULT_COSTS.copy()
        self.budget_limit_daily = budget_limit_daily
        self.budget_limit_monthly = budget_limit_monthly

        # Usage tracking
        self._usage_history: list[UsageRecord] = []
        self._customer_budgets: dict[str, float] = {}
        self._customer_usage: dict[str, list[UsageRecord]] = defaultdict(list)

    def get_model_cost(self, model_id: str) -> ModelCost | None:
        """Get cost structure for a model."""
        return self.model_costs.get(model_id)

    def add_model_cost(self, cost: ModelCost) -> None:
        """Add or update model cost structure."""
        self.model_costs[cost.model_id] = cost

    def estimate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int | None = None,
        use_cache: bool = False,
        use_batch: bool = False,
    ) -> CostEstimate:
        """Estimate cost for a request.

        Args:
            model_id: Target model
            input_tokens: Estimated input tokens
            output_tokens: Estimated output tokens (defaults to 3x input)
            use_cache: Whether prompt caching will be used
            use_batch: Whether batch API will be used

        Returns:
            CostEstimate with cost breakdown
        """
        cost_info = self.model_costs.get(model_id)
        if not cost_info:
            return CostEstimate(
                model_id=model_id,
                estimated_input_tokens=input_tokens,
                estimated_output_tokens=output_tokens or input_tokens * 3,
                estimated_cost=0.0,
            )

        if output_tokens is None:
            output_tokens = input_tokens * 3

        # Standard cost
        input_cost = (input_tokens / 1000) * cost_info.input_cost_per_1k
        output_cost = (output_tokens / 1000) * cost_info.output_cost_per_1k
        standard_cost = input_cost + output_cost

        # With caching
        cached_input_cost = (input_tokens / 1000) * cost_info.cached_input_cost_per_1k
        with_caching = cached_input_cost + output_cost

        # With batching
        with_batching = standard_cost * cost_info.batch_discount

        return CostEstimate(
            model_id=model_id,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost=standard_cost,
            with_caching=with_caching,
            with_batching=with_batching,
        )

    def record_usage(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        customer_id: str | None = None,
        request_type: str = "inference",
    ) -> UsageRecord:
        """Record model usage."""
        cost_info = self.model_costs.get(model_id)
        if cost_info:
            cost = (
                (input_tokens / 1000) * cost_info.input_cost_per_1k
                + (output_tokens / 1000) * cost_info.output_cost_per_1k
            )
        else:
            cost = 0.0

        record = UsageRecord(
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            timestamp=datetime.now(timezone.utc),
            customer_id=customer_id,
            request_type=request_type,
        )

        self._usage_history.append(record)
        if customer_id:
            self._customer_usage[customer_id].append(record)

        # Cleanup old records (keep 30 days)
        self._cleanup_old_records()

        return record

    def get_daily_usage(self, customer_id: str | None = None) -> dict[str, Any]:
        """Get usage statistics for today."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        records = self._get_records_since(today_start, customer_id)

        return self._aggregate_usage(records, "daily")

    def get_monthly_usage(self, customer_id: str | None = None) -> dict[str, Any]:
        """Get usage statistics for this month."""
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        records = self._get_records_since(month_start, customer_id)

        return self._aggregate_usage(records, "monthly")

    def check_budget(
        self,
        customer_id: str | None = None,
        additional_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Check if budget allows for additional spending.

        Returns:
            Dict with budget status and remaining amounts
        """
        daily = self.get_daily_usage(customer_id)
        monthly = self.get_monthly_usage(customer_id)

        daily_remaining = None
        monthly_remaining = None
        can_proceed = True
        reasons: list[str] = []

        # Check daily limit
        if self.budget_limit_daily:
            daily_remaining = self.budget_limit_daily - daily["total_cost"]
            if daily_remaining < additional_cost:
                can_proceed = False
                reasons.append(f"Daily budget exceeded (${daily_remaining:.4f} remaining)")

        # Check monthly limit
        if self.budget_limit_monthly:
            monthly_remaining = self.budget_limit_monthly - monthly["total_cost"]
            if monthly_remaining < additional_cost:
                can_proceed = False
                reasons.append(f"Monthly budget exceeded (${monthly_remaining:.4f} remaining)")

        # Check customer-specific budget
        if customer_id and customer_id in self._customer_budgets:
            customer_budget = self._customer_budgets[customer_id]
            customer_spent = monthly["total_cost"]
            customer_remaining = customer_budget - customer_spent
            if customer_remaining < additional_cost:
                can_proceed = False
                reasons.append(f"Customer budget exceeded (${customer_remaining:.4f} remaining)")
        else:
            customer_remaining = None

        return {
            "can_proceed": can_proceed,
            "reasons": reasons,
            "daily_spent": daily["total_cost"],
            "daily_remaining": daily_remaining,
            "monthly_spent": monthly["total_cost"],
            "monthly_remaining": monthly_remaining,
            "customer_remaining": customer_remaining,
        }

    def set_customer_budget(self, customer_id: str, monthly_budget: float) -> None:
        """Set monthly budget for a customer."""
        self._customer_budgets[customer_id] = monthly_budget

    def recommend_model(
        self,
        min_quality: str,
        estimated_tokens: int,
        max_cost: float | None = None,
    ) -> str | None:
        """Recommend the most cost-effective model meeting quality requirements.

        Args:
            min_quality: Minimum quality tier ("fast", "balanced", "quality", "premium")
            estimated_tokens: Estimated total tokens
            max_cost: Maximum acceptable cost

        Returns:
            Recommended model ID, or None if no model fits
        """
        quality_tiers = {
            "fast": ["gpt-4o-mini", "claude-3-5-haiku-latest"],
            "balanced": ["gpt-4o-mini", "claude-3-5-haiku-latest", "gpt-4o", "claude-3-5-sonnet-latest"],
            "quality": ["gpt-4o", "claude-3-5-sonnet-latest", "claude-3-opus-latest"],
            "premium": ["claude-3-opus-latest"],
        }

        candidates = quality_tiers.get(min_quality, quality_tiers["balanced"])

        best_model = None
        best_cost = float("inf")

        for model_id in candidates:
            if model_id not in self.model_costs:
                continue

            estimate = self.estimate_cost(
                model_id,
                input_tokens=estimated_tokens // 4,
                output_tokens=estimated_tokens * 3 // 4,
            )

            if max_cost and estimate.estimated_cost > max_cost:
                continue

            if estimate.estimated_cost < best_cost:
                best_cost = estimate.estimated_cost
                best_model = model_id

        return best_model

    def get_cost_analytics(self, days: int = 7) -> dict[str, Any]:
        """Get cost analytics for the specified period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = [r for r in self._usage_history if r.timestamp > cutoff]

        if not records:
            return {
                "period_days": days,
                "total_cost": 0.0,
                "total_requests": 0,
                "by_model": {},
                "by_customer": {},
                "daily_breakdown": [],
            }

        # Aggregate by model
        by_model: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"cost": 0.0, "requests": 0, "tokens": 0}
        )
        for r in records:
            by_model[r.model_id]["cost"] += r.cost
            by_model[r.model_id]["requests"] += 1
            by_model[r.model_id]["tokens"] += r.input_tokens + r.output_tokens

        # Aggregate by customer
        by_customer: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"cost": 0.0, "requests": 0}
        )
        for r in records:
            cid = r.customer_id or "unknown"
            by_customer[cid]["cost"] += r.cost
            by_customer[cid]["requests"] += 1

        # Daily breakdown
        daily: dict[str, float] = defaultdict(float)
        for r in records:
            day_key = r.timestamp.strftime("%Y-%m-%d")
            daily[day_key] += r.cost

        return {
            "period_days": days,
            "total_cost": sum(r.cost for r in records),
            "total_requests": len(records),
            "total_tokens": sum(r.input_tokens + r.output_tokens for r in records),
            "by_model": dict(by_model),
            "by_customer": dict(by_customer),
            "daily_breakdown": [
                {"date": k, "cost": v} for k, v in sorted(daily.items())
            ],
        }

    def _get_records_since(
        self,
        since: datetime,
        customer_id: str | None = None,
    ) -> list[UsageRecord]:
        """Get usage records since a timestamp."""
        if customer_id:
            records = self._customer_usage.get(customer_id, [])
        else:
            records = self._usage_history

        return [r for r in records if r.timestamp >= since]

    def _aggregate_usage(
        self,
        records: list[UsageRecord],
        period: str,
    ) -> dict[str, Any]:
        """Aggregate usage records."""
        if not records:
            return {
                "period": period,
                "total_cost": 0.0,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "by_model": {},
            }

        by_model: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"cost": 0.0, "requests": 0}
        )
        for r in records:
            by_model[r.model_id]["cost"] += r.cost
            by_model[r.model_id]["requests"] += 1

        return {
            "period": period,
            "total_cost": sum(r.cost for r in records),
            "total_requests": len(records),
            "total_input_tokens": sum(r.input_tokens for r in records),
            "total_output_tokens": sum(r.output_tokens for r in records),
            "by_model": dict(by_model),
        }

    def _cleanup_old_records(self, days: int = 30) -> None:
        """Remove records older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        self._usage_history = [r for r in self._usage_history if r.timestamp > cutoff]
        for cid in self._customer_usage:
            self._customer_usage[cid] = [
                r for r in self._customer_usage[cid] if r.timestamp > cutoff
            ]
