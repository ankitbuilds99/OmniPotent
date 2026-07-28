"""Product metrics for startup analytics.

Business metrics:
- MRR (Monthly Recurring Revenue)
- LTV (Customer Lifetime Value)
- Cohort retention analysis
- Usage patterns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("devcopilot.analytics.product_metrics")


class MetricPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass
class RevenueMetrics:
    """Revenue metrics snapshot."""

    period: date
    mrr_cents: int = 0
    arr_cents: int = 0
    new_mrr_cents: int = 0
    expansion_mrr_cents: int = 0
    contraction_mrr_cents: int = 0
    churned_mrr_cents: int = 0
    net_new_mrr_cents: int = 0

    paying_customers: int = 0
    new_customers: int = 0
    churned_customers: int = 0

    arpu_cents: int = 0

    @property
    def mrr_growth_rate(self) -> float:
        if self.mrr_cents == 0:
            return 0.0
        return self.net_new_mrr_cents / self.mrr_cents

    @property
    def net_revenue_retention(self) -> float:
        starting = self.mrr_cents - self.new_mrr_cents
        if starting == 0:
            return 0.0
        ending = starting + self.expansion_mrr_cents - self.contraction_mrr_cents - self.churned_mrr_cents
        return ending / starting

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.isoformat(),
            "mrr": self.mrr_cents / 100,
            "arr": self.arr_cents / 100,
            "new_mrr": self.new_mrr_cents / 100,
            "expansion_mrr": self.expansion_mrr_cents / 100,
            "contraction_mrr": self.contraction_mrr_cents / 100,
            "churned_mrr": self.churned_mrr_cents / 100,
            "net_new_mrr": self.net_new_mrr_cents / 100,
            "paying_customers": self.paying_customers,
            "new_customers": self.new_customers,
            "churned_customers": self.churned_customers,
            "arpu": self.arpu_cents / 100,
            "mrr_growth_rate": self.mrr_growth_rate,
            "net_revenue_retention": self.net_revenue_retention,
        }


@dataclass
class CohortRetention:
    """Retention data for a single cohort."""

    cohort_month: date
    cohort_size: int
    retention_by_month: dict[int, float] = field(default_factory=dict)

    def get_retention(self, month: int) -> float:
        return self.retention_by_month.get(month, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort": self.cohort_month.isoformat(),
            "size": self.cohort_size,
            "retention": self.retention_by_month,
        }


@dataclass
class CohortAnalysis:
    """Cohort retention analysis."""

    cohorts: list[CohortRetention] = field(default_factory=list)
    average_retention: dict[int, float] = field(default_factory=dict)

    def add_cohort(self, cohort: CohortRetention) -> None:
        self.cohorts.append(cohort)
        self._recalculate_averages()

    def _recalculate_averages(self) -> None:
        month_totals: dict[int, list[float]] = {}
        for cohort in self.cohorts:
            for month, retention in cohort.retention_by_month.items():
                if month not in month_totals:
                    month_totals[month] = []
                month_totals[month].append(retention)

        self.average_retention = {
            month: sum(values) / len(values)
            for month, values in month_totals.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohorts": [c.to_dict() for c in self.cohorts],
            "average_retention": self.average_retention,
        }


@dataclass
class UsageMetrics:
    """Usage metrics for a period."""

    period: date
    period_type: MetricPeriod = MetricPeriod.MONTHLY

    active_users: int = 0
    daily_active_users: int = 0
    weekly_active_users: int = 0
    monthly_active_users: int = 0

    investigations_count: int = 0
    integrations_connected: int = 0
    alerts_triggered: int = 0
    api_calls: int = 0

    avg_session_duration_seconds: int = 0
    avg_investigations_per_user: float = 0.0

    feature_usage: dict[str, int] = field(default_factory=dict)

    @property
    def dau_mau_ratio(self) -> float:
        if self.monthly_active_users == 0:
            return 0.0
        return self.daily_active_users / self.monthly_active_users

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.isoformat(),
            "period_type": self.period_type.value,
            "active_users": self.active_users,
            "dau": self.daily_active_users,
            "wau": self.weekly_active_users,
            "mau": self.monthly_active_users,
            "dau_mau_ratio": self.dau_mau_ratio,
            "investigations_count": self.investigations_count,
            "integrations_connected": self.integrations_connected,
            "alerts_triggered": self.alerts_triggered,
            "api_calls": self.api_calls,
            "avg_session_duration_seconds": self.avg_session_duration_seconds,
            "avg_investigations_per_user": self.avg_investigations_per_user,
            "feature_usage": self.feature_usage,
        }


class MetricsStore(Protocol):
    """Protocol for metrics storage."""

    async def get_subscriptions_at(self, date: date) -> list[Any]: ...
    async def get_subscription_changes(
        self, start: date, end: date
    ) -> dict[str, list[Any]]: ...
    async def get_users_by_cohort(self, cohort_month: date) -> list[Any]: ...
    async def get_active_users_in_month(
        self, users: list[str], month: date
    ) -> list[str]: ...
    async def get_usage_stats(
        self, start: date, end: date
    ) -> dict[str, Any]: ...
    async def get_feature_usage(
        self, start: date, end: date
    ) -> dict[str, int]: ...


class AnalyticsManager:
    """Manages product analytics calculations."""

    def __init__(self, store: MetricsStore):
        self._store = store

    async def calculate_mrr(
        self,
        as_of: date | None = None,
    ) -> RevenueMetrics:
        """Calculate MRR and revenue metrics."""
        if as_of is None:
            as_of = date.today()

        subscriptions = await self._store.get_subscriptions_at(as_of)

        mrr_cents = 0
        paying = 0

        for sub in subscriptions:
            if getattr(sub, "status", "") == "active":
                price = getattr(sub, "price_cents", 0)
                interval = getattr(sub, "interval", "monthly")

                if interval == "yearly":
                    mrr_cents += price // 12
                else:
                    mrr_cents += price

                paying += 1

        month_start = as_of.replace(day=1)

        changes = await self._store.get_subscription_changes(month_start, as_of)

        new_mrr = sum(
            getattr(s, "price_cents", 0)
            for s in changes.get("new", [])
        )
        expansion_mrr = sum(
            getattr(s, "delta_cents", 0)
            for s in changes.get("upgraded", [])
        )
        contraction_mrr = sum(
            abs(getattr(s, "delta_cents", 0))
            for s in changes.get("downgraded", [])
        )
        churned_mrr = sum(
            getattr(s, "price_cents", 0)
            for s in changes.get("churned", [])
        )

        return RevenueMetrics(
            period=as_of,
            mrr_cents=mrr_cents,
            arr_cents=mrr_cents * 12,
            new_mrr_cents=new_mrr,
            expansion_mrr_cents=expansion_mrr,
            contraction_mrr_cents=contraction_mrr,
            churned_mrr_cents=churned_mrr,
            net_new_mrr_cents=new_mrr + expansion_mrr - contraction_mrr - churned_mrr,
            paying_customers=paying,
            new_customers=len(changes.get("new", [])),
            churned_customers=len(changes.get("churned", [])),
            arpu_cents=mrr_cents // paying if paying > 0 else 0,
        )

    async def calculate_ltv(
        self,
        avg_mrr_cents: int,
        churn_rate: float,
        gross_margin: float = 0.8,
    ) -> dict[str, Any]:
        """Calculate customer lifetime value."""
        if churn_rate == 0:
            avg_lifespan_months = float("inf")
            ltv_cents = float("inf")
        else:
            avg_lifespan_months = 1 / churn_rate
            ltv_cents = (avg_mrr_cents * gross_margin) / churn_rate

        return {
            "ltv_cents": ltv_cents if ltv_cents != float("inf") else None,
            "ltv_dollars": ltv_cents / 100 if ltv_cents != float("inf") else None,
            "avg_lifespan_months": avg_lifespan_months if avg_lifespan_months != float("inf") else None,
            "churn_rate": churn_rate,
            "gross_margin": gross_margin,
            "inputs": {
                "avg_mrr_cents": avg_mrr_cents,
            },
        }

    async def build_cohort_analysis(
        self,
        months_back: int = 12,
    ) -> CohortAnalysis:
        """Build cohort retention analysis."""
        analysis = CohortAnalysis()
        today = date.today()
        current_month = today.replace(day=1)

        for i in range(months_back):
            cohort_month = current_month - timedelta(days=30 * (months_back - i))
            cohort_month = cohort_month.replace(day=1)

            users = await self._store.get_users_by_cohort(cohort_month)
            if not users:
                continue

            user_ids = [getattr(u, "id", str(u)) for u in users]
            cohort_size = len(user_ids)

            retention: dict[int, float] = {}
            month = cohort_month
            month_index = 0

            while month <= current_month:
                active = await self._store.get_active_users_in_month(user_ids, month)
                retention[month_index] = len(active) / cohort_size if cohort_size > 0 else 0.0
                month = (month + timedelta(days=32)).replace(day=1)
                month_index += 1

            cohort = CohortRetention(
                cohort_month=cohort_month,
                cohort_size=cohort_size,
                retention_by_month=retention,
            )
            analysis.add_cohort(cohort)

        return analysis

    async def calculate_usage_metrics(
        self,
        period_start: date,
        period_end: date | None = None,
        period_type: MetricPeriod = MetricPeriod.MONTHLY,
    ) -> UsageMetrics:
        """Calculate usage metrics for a period."""
        if period_end is None:
            period_end = date.today()

        stats = await self._store.get_usage_stats(period_start, period_end)
        features = await self._store.get_feature_usage(period_start, period_end)

        return UsageMetrics(
            period=period_end,
            period_type=period_type,
            active_users=stats.get("active_users", 0),
            daily_active_users=stats.get("dau", 0),
            weekly_active_users=stats.get("wau", 0),
            monthly_active_users=stats.get("mau", 0),
            investigations_count=stats.get("investigations", 0),
            integrations_connected=stats.get("integrations", 0),
            alerts_triggered=stats.get("alerts", 0),
            api_calls=stats.get("api_calls", 0),
            avg_session_duration_seconds=stats.get("avg_session_seconds", 0),
            avg_investigations_per_user=stats.get("avg_investigations", 0.0),
            feature_usage=features,
        )

    async def get_dashboard_metrics(self) -> dict[str, Any]:
        """Get all metrics for executive dashboard."""
        today = date.today()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

        current_revenue = await self.calculate_mrr(today)
        prev_revenue = await self.calculate_mrr(prev_month_start)

        cohorts = await self.build_cohort_analysis(months_back=6)
        usage = await self.calculate_usage_metrics(month_start, today)

        monthly_churn = (
            current_revenue.churned_customers / prev_revenue.paying_customers
            if prev_revenue.paying_customers > 0
            else 0.0
        )

        ltv = await self.calculate_ltv(
            avg_mrr_cents=current_revenue.arpu_cents,
            churn_rate=monthly_churn,
        )

        return {
            "revenue": current_revenue.to_dict(),
            "revenue_prev_month": prev_revenue.to_dict(),
            "ltv": ltv,
            "cohort_retention": cohorts.to_dict(),
            "usage": usage.to_dict(),
            "health_scores": {
                "net_revenue_retention": current_revenue.net_revenue_retention,
                "dau_mau_ratio": usage.dau_mau_ratio,
                "monthly_churn_rate": monthly_churn,
            },
        }
