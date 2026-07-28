"""Cost attribution for LLM usage tracking.

Tracks and attributes LLM costs per tenant, user, feature, and request.
Provides detailed breakdowns for billing transparency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from collections import defaultdict

log = logging.getLogger("devcopilot.analytics.cost")


class CostCategory(Enum):
    """Categories for cost attribution."""

    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    FINE_TUNING = "fine_tuning"
    STORAGE = "storage"
    COMPUTE = "compute"


@dataclass
class CostEntry:
    """A single cost entry."""

    id: str
    timestamp: datetime
    tenant_id: str
    user_id: str | None
    category: CostCategory
    model: str
    provider: str
    amount: Decimal
    currency: str = "USD"
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str | None = None
    feature: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "category": self.category.value,
            "model": self.model,
            "provider": self.provider,
            "amount": str(self.amount),
            "currency": self.currency,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "request_id": self.request_id,
            "feature": self.feature,
            "metadata": self.metadata,
        }


@dataclass
class CostSummary:
    """Summary of costs over a period."""

    period_start: datetime
    period_end: datetime
    total_amount: Decimal
    currency: str
    by_category: dict[str, Decimal]
    by_model: dict[str, Decimal]
    by_feature: dict[str, Decimal]
    by_user: dict[str, Decimal]
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_amount": str(self.total_amount),
            "currency": self.currency,
            "by_category": {k: str(v) for k, v in self.by_category.items()},
            "by_model": {k: str(v) for k, v in self.by_model.items()},
            "by_feature": {k: str(v) for k, v in self.by_feature.items()},
            "by_user": {k: str(v) for k, v in self.by_user.items()},
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


@dataclass
class CostBudget:
    """Budget configuration for a tenant."""

    tenant_id: str
    monthly_limit: Decimal
    daily_limit: Decimal | None = None
    alert_thresholds: list[float] = field(default_factory=lambda: [0.5, 0.8, 0.95])
    hard_limit: bool = False
    notified_thresholds: set[float] = field(default_factory=set)


@dataclass
class BudgetStatus:
    """Current budget status."""

    budget: CostBudget
    current_month_spend: Decimal
    current_day_spend: Decimal
    percent_used: float
    days_remaining: int
    projected_month_end: Decimal
    is_over_budget: bool
    alerts: list[str]


class CostTracker:
    """Tracks and attributes costs."""

    def __init__(self):
        self._entries: list[CostEntry] = []
        self._budgets: dict[str, CostBudget] = {}
        self._cost_rates: dict[str, dict[str, Decimal]] = {
            "gpt-4o": {
                "input": Decimal("0.0025"),
                "output": Decimal("0.01"),
            },
            "gpt-4o-mini": {
                "input": Decimal("0.00015"),
                "output": Decimal("0.0006"),
            },
            "claude-3-5-sonnet": {
                "input": Decimal("0.003"),
                "output": Decimal("0.015"),
            },
            "claude-3-5-haiku": {
                "input": Decimal("0.0008"),
                "output": Decimal("0.004"),
            },
            "claude-3-opus": {
                "input": Decimal("0.015"),
                "output": Decimal("0.075"),
            },
            "o1": {
                "input": Decimal("0.015"),
                "output": Decimal("0.06"),
            },
            "o1-mini": {
                "input": Decimal("0.003"),
                "output": Decimal("0.012"),
            },
        }
        self._entry_counter = 0

    def record(
        self,
        tenant_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        user_id: str | None = None,
        request_id: str | None = None,
        feature: str | None = None,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostEntry:
        """Record a cost entry."""
        self._entry_counter += 1

        rates = self._cost_rates.get(model, {"input": Decimal("0"), "output": Decimal("0")})
        input_cost = (Decimal(input_tokens) / 1000) * rates["input"]
        output_cost = (Decimal(output_tokens) / 1000) * rates["output"]
        total_cost = input_cost + output_cost

        entry = CostEntry(
            id=f"cost_{self._entry_counter}",
            timestamp=datetime.now(timezone.utc),
            tenant_id=tenant_id,
            user_id=user_id,
            category=CostCategory.LLM_INPUT if input_tokens > output_tokens else CostCategory.LLM_OUTPUT,
            model=model,
            provider=provider or self._infer_provider(model),
            amount=total_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            feature=feature,
            metadata=metadata or {},
        )

        self._entries.append(entry)

        log.debug(
            "cost recorded",
            extra={
                "tenant_id": tenant_id,
                "model": model,
                "amount": str(total_cost),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

        return entry

    def _infer_provider(self, model: str) -> str:
        """Infer provider from model name."""
        if model.startswith(("gpt-", "o1")):
            return "openai"
        elif model.startswith("claude"):
            return "anthropic"
        elif model.startswith("gemini"):
            return "google"
        else:
            return "unknown"

    def get_summary(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
    ) -> CostSummary:
        """Get cost summary for a period."""
        entries = [
            e
            for e in self._entries
            if e.tenant_id == tenant_id and start <= e.timestamp <= end
        ]

        by_category: dict[str, Decimal] = defaultdict(Decimal)
        by_model: dict[str, Decimal] = defaultdict(Decimal)
        by_feature: dict[str, Decimal] = defaultdict(Decimal)
        by_user: dict[str, Decimal] = defaultdict(Decimal)
        total_input = 0
        total_output = 0

        for entry in entries:
            by_category[entry.category.value] += entry.amount
            by_model[entry.model] += entry.amount
            if entry.feature:
                by_feature[entry.feature] += entry.amount
            if entry.user_id:
                by_user[entry.user_id] += entry.amount
            total_input += entry.input_tokens
            total_output += entry.output_tokens

        return CostSummary(
            period_start=start,
            period_end=end,
            total_amount=sum(e.amount for e in entries),
            currency="USD",
            by_category=dict(by_category),
            by_model=dict(by_model),
            by_feature=dict(by_feature),
            by_user=dict(by_user),
            total_requests=len(entries),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
        )

    def set_budget(
        self,
        tenant_id: str,
        monthly_limit: Decimal,
        daily_limit: Decimal | None = None,
        alert_thresholds: list[float] | None = None,
        hard_limit: bool = False,
    ) -> CostBudget:
        """Set budget for a tenant."""
        budget = CostBudget(
            tenant_id=tenant_id,
            monthly_limit=monthly_limit,
            daily_limit=daily_limit,
            alert_thresholds=alert_thresholds or [0.5, 0.8, 0.95],
            hard_limit=hard_limit,
        )
        self._budgets[tenant_id] = budget
        return budget

    def check_budget(self, tenant_id: str) -> BudgetStatus | None:
        """Check budget status for a tenant."""
        budget = self._budgets.get(tenant_id)
        if not budget:
            return None

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        month_entries = [
            e
            for e in self._entries
            if e.tenant_id == tenant_id and e.timestamp >= month_start
        ]
        day_entries = [
            e for e in month_entries if e.timestamp >= day_start
        ]

        month_spend = sum(e.amount for e in month_entries)
        day_spend = sum(e.amount for e in day_entries)

        percent_used = float(month_spend / budget.monthly_limit) if budget.monthly_limit else 0

        days_in_month = 30
        days_elapsed = now.day
        days_remaining = days_in_month - days_elapsed

        daily_avg = month_spend / Decimal(days_elapsed) if days_elapsed > 0 else Decimal(0)
        projected = month_spend + (daily_avg * Decimal(days_remaining))

        alerts = []
        for threshold in budget.alert_thresholds:
            if percent_used >= threshold and threshold not in budget.notified_thresholds:
                alerts.append(f"Budget {int(threshold * 100)}% used")
                budget.notified_thresholds.add(threshold)

        is_over = month_spend >= budget.monthly_limit

        return BudgetStatus(
            budget=budget,
            current_month_spend=month_spend,
            current_day_spend=day_spend,
            percent_used=percent_used,
            days_remaining=days_remaining,
            projected_month_end=projected,
            is_over_budget=is_over,
            alerts=alerts,
        )

    def can_proceed(self, tenant_id: str, estimated_cost: Decimal) -> tuple[bool, str]:
        """Check if a request can proceed based on budget."""
        budget = self._budgets.get(tenant_id)
        if not budget:
            return True, "no_budget_set"

        if not budget.hard_limit:
            return True, "soft_limit"

        status = self.check_budget(tenant_id)
        if not status:
            return True, "no_status"

        if status.current_month_spend + estimated_cost > budget.monthly_limit:
            return False, "monthly_limit_exceeded"

        if budget.daily_limit:
            if status.current_day_spend + estimated_cost > budget.daily_limit:
                return False, "daily_limit_exceeded"

        return True, "within_budget"

    def get_top_users(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top users by cost."""
        entries = [
            e
            for e in self._entries
            if e.tenant_id == tenant_id
            and e.user_id
            and start <= e.timestamp <= end
        ]

        by_user: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"amount": Decimal(0), "requests": 0, "tokens": 0}
        )

        for entry in entries:
            user_id = entry.user_id
            if user_id:
                by_user[user_id]["amount"] += entry.amount
                by_user[user_id]["requests"] += 1
                by_user[user_id]["tokens"] += entry.input_tokens + entry.output_tokens

        result = [
            {
                "user_id": uid,
                "amount": str(data["amount"]),
                "requests": data["requests"],
                "tokens": data["tokens"],
            }
            for uid, data in by_user.items()
        ]

        result.sort(key=lambda x: Decimal(x["amount"]), reverse=True)
        return result[:limit]

    def get_daily_breakdown(
        self,
        tenant_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get daily cost breakdown."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        entries = [
            e
            for e in self._entries
            if e.tenant_id == tenant_id and e.timestamp >= start
        ]

        by_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"amount": Decimal(0), "requests": 0}
        )

        for entry in entries:
            day = entry.timestamp.strftime("%Y-%m-%d")
            by_day[day]["amount"] += entry.amount
            by_day[day]["requests"] += 1

        result = []
        current = start
        while current <= now:
            day = current.strftime("%Y-%m-%d")
            data = by_day.get(day, {"amount": Decimal(0), "requests": 0})
            result.append(
                {
                    "date": day,
                    "amount": str(data["amount"]),
                    "requests": data["requests"],
                }
            )
            current += timedelta(days=1)

        return result

    def export_entries(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Export cost entries for billing."""
        entries = [
            e
            for e in self._entries
            if e.tenant_id == tenant_id and start <= e.timestamp <= end
        ]
        return [e.to_dict() for e in entries]


_tracker: CostTracker | None = None


def get_cost_tracker() -> CostTracker:
    """Get the default cost tracker."""
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker
