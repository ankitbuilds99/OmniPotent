"""Cost Optimizer for Model Routing.

Manages model costs and optimizes routing for cost efficiency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from collections import defaultdict

log = logging.getLogger("omnipotent.model_router.cost")


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


def evaluate_budget(
    *,
    daily_spent: float,
    monthly_spent: float,
    daily_limit: float | None,
    monthly_limit: float | None,
    additional_cost: float = 0.0,
) -> dict[str, Any]:
    """Would `additional_cost` more dollars fit under the caps that are set?

    Pure arithmetic, no storage: the same decision has to be made against the
    in-process history (`CostOptimizer.check_budget`) and against the persisted
    ledger (`app.model_router.usage_store.check_budget`), and two copies of a
    money rule is one copy too many.

    A limit of None is "no cap", which reports a null remaining rather than a
    made-up one. A cap of 0 is a real cap: `if limit` would have read it as
    unset and let the spend through.
    """
    daily_remaining = None if daily_limit is None else daily_limit - daily_spent
    monthly_remaining = None if monthly_limit is None else monthly_limit - monthly_spent

    reasons: list[str] = []
    if daily_remaining is not None and daily_remaining < additional_cost:
        reasons.append(f"Daily budget exceeded (${daily_remaining:.4f} remaining)")
    if monthly_remaining is not None and monthly_remaining < additional_cost:
        reasons.append(f"Monthly budget exceeded (${monthly_remaining:.4f} remaining)")

    return {
        "can_proceed": not reasons,
        "reasons": reasons,
        "daily_spent": daily_spent,
        "daily_remaining": daily_remaining,
        "monthly_spent": monthly_spent,
        "monthly_remaining": monthly_remaining,
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
        # The dated ids the rest of the app actually passes to the SDK. The
        # table above held only `-latest` aliases, so every real call missed
        # it and was priced at exactly $0.00 — see `_cost_for`.
        "claude-opus-4-20250514": ModelCost(
            model_id="claude-opus-4-20250514",
            input_cost_per_1k=0.015,
            output_cost_per_1k=0.075,
            cached_input_cost_per_1k=0.0015,
        ),
        "claude-sonnet-4-20250514": ModelCost(
            model_id="claude-sonnet-4-20250514",
            input_cost_per_1k=0.003,
            output_cost_per_1k=0.015,
            cached_input_cost_per_1k=0.0003,
        ),
        "claude-haiku-4-20250514": ModelCost(
            model_id="claude-haiku-4-20250514",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.005,
            cached_input_cost_per_1k=0.0001,
        ),
        "claude-3-5-sonnet-20241022": ModelCost(
            model_id="claude-3-5-sonnet-20241022",
            input_cost_per_1k=0.003,
            output_cost_per_1k=0.015,
            cached_input_cost_per_1k=0.0003,
        ),
        "claude-3-5-sonnet-20240620": ModelCost(
            model_id="claude-3-5-sonnet-20240620",
            input_cost_per_1k=0.003,
            output_cost_per_1k=0.015,
            cached_input_cost_per_1k=0.0003,
        ),
        "claude-3-5-haiku-20241022": ModelCost(
            model_id="claude-3-5-haiku-20241022",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.005,
            cached_input_cost_per_1k=0.0001,
        ),
        "claude-3-opus-20240229": ModelCost(
            model_id="claude-3-opus-20240229",
            input_cost_per_1k=0.015,
            output_cost_per_1k=0.075,
            cached_input_cost_per_1k=0.0015,
        ),
        "claude-3-haiku-20240307": ModelCost(
            model_id="claude-3-haiku-20240307",
            input_cost_per_1k=0.00025,
            output_cost_per_1k=0.00125,
            cached_input_cost_per_1k=0.00003,
        ),
        # --- the ids `app.llm._DEFAULTS` actually resolves to ----------------
        # COPILOT_MODEL/COPILOT_FAST_MODEL default to "", so *these* are what
        # every real request runs on. None of them were in the table, so all
        # of them fell through to `_cost_for`'s most-expensive-known fallback:
        # the cheap fast model (plan/reflect/report/verify) was billed at the
        # Opus-3 rate, ~15x its real price. Prices are per 1k tokens, from each
        # provider's published list price.
        "claude-opus-4-8": ModelCost(          # $5 / $25 per MTok
            model_id="claude-opus-4-8",
            input_cost_per_1k=0.005,
            output_cost_per_1k=0.025,
            cached_input_cost_per_1k=0.0005,   # cache read = 0.1x input
        ),
        "claude-haiku-4-5": ModelCost(         # $1 / $5 per MTok
            model_id="claude-haiku-4-5",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.005,
            cached_input_cost_per_1k=0.0001,
        ),
        "gemini-1.5-pro": ModelCost(           # $1.25 / $5 per MTok
            model_id="gemini-1.5-pro",
            input_cost_per_1k=0.00125,
            output_cost_per_1k=0.005,
            cached_input_cost_per_1k=0.0003125,
        ),
        "gemini-1.5-flash": ModelCost(         # $0.075 / $0.30 per MTok
            model_id="gemini-1.5-flash",
            input_cost_per_1k=0.000075,
            output_cost_per_1k=0.0003,
            cached_input_cost_per_1k=0.00001875,
        ),
        "llama-3.3-70b-versatile": ModelCost(  # Groq: $0.59 / $0.79 per MTok
            model_id="llama-3.3-70b-versatile",
            input_cost_per_1k=0.00059,
            output_cost_per_1k=0.00079,
        ),
        "llama-3.1-8b-instant": ModelCost(     # Groq: $0.05 / $0.08 per MTok
            model_id="llama-3.1-8b-instant",
            input_cost_per_1k=0.00005,
            output_cost_per_1k=0.00008,
        ),
        "deepseek-chat": ModelCost(            # $0.27 / $1.10 per MTok
            model_id="deepseek-chat",
            input_cost_per_1k=0.00027,
            output_cost_per_1k=0.0011,
            cached_input_cost_per_1k=0.00007,
        ),
    }

    # What an unknown model is priced at. Deliberately the most expensive
    # entry in the table rather than zero: this number is what `check_budget`
    # compares a limit against, so guessing low means a customer whose model
    # is not listed never hits their cap. Guessing high stops spending that
    # might have been affordable, which is the recoverable mistake.
    _FALLBACK_COST_KEY = "claude-3-opus-latest"

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
        """Get cost structure for a model, if it is one we have a price for."""
        return self.model_costs.get(model_id)

    def _cost_for(self, model_id: str) -> ModelCost:
        """A price for `model_id`, guessing high rather than free.

        A miss used to be worth $0.00: `estimate_cost` returned a zero
        estimate and `record_usage` filed the call as costing nothing, so
        `check_budget` summed those zeros and every limit passed. That is not
        a rare edge — the table shipped with only `-latest` aliases while the
        rest of the app calls dated ids like `claude-sonnet-4-20250514`, so
        the models the product actually runs on were all free. A cost tracker
        that reports zero is worse than one that reports nothing, because the
        number looks like an answer.

        An unknown model is priced at the most expensive one we know, and
        logged so the table can be corrected.
        """
        cost_info = self.model_costs.get(model_id)
        if cost_info is not None:
            return cost_info

        fallback = self.model_costs.get(self._FALLBACK_COST_KEY) or max(
            self.model_costs.values(), key=lambda c: c.avg_cost_per_1k
        )
        log.warning(
            "no price for model; charging at the most expensive known rate",
            extra={"unpriced_model": model_id, "priced_as": fallback.model_id},
        )
        return ModelCost(
            model_id=model_id,
            input_cost_per_1k=fallback.input_cost_per_1k,
            output_cost_per_1k=fallback.output_cost_per_1k,
            cached_input_cost_per_1k=fallback.cached_input_cost_per_1k,
            batch_discount=fallback.batch_discount,
        )

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
        cost_info = self._cost_for(model_id)

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

    def price(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """What a call that has already happened cost, in dollars.

        Public because the ledger writers (`usage_ledger`, the API's
        record-usage endpoint) need the price without also filing the call in
        this object's in-process history.
        """
        cost_info = self._cost_for(model_id)
        return (
            (input_tokens / 1000) * cost_info.input_cost_per_1k
            + (output_tokens / 1000) * cost_info.output_cost_per_1k
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
        cost = self.price(model_id, input_tokens, output_tokens)

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

        verdict = evaluate_budget(
            daily_spent=daily["total_cost"],
            monthly_spent=monthly["total_cost"],
            daily_limit=self.budget_limit_daily,
            monthly_limit=self.budget_limit_monthly,
            additional_cost=additional_cost,
        )
        can_proceed = verdict["can_proceed"]
        reasons = list(verdict["reasons"])

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
            **verdict,
            "can_proceed": can_proceed,
            "reasons": reasons,
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
