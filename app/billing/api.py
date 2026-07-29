"""Billing API endpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.billing.models import (
    BillingCycle,
    MetricType,
)
from app.billing.plans import PlanManager
from app.billing.feature_gate import FeatureGateService
from app.billing.usage_tracker import UsageTracker, UsageReporter
from app.billing.stripe_integration import StripeBillingService

log = logging.getLogger("devcopilot.billing.api")


@dataclass
class PlanResponse:
    """Plan information for API response."""

    name: str
    display_name: str
    description: str
    tier: str
    monthly_price: float
    annual_price: float
    annual_monthly_price: float
    annual_savings_percent: float
    max_users: int
    max_ai_investigations_monthly: int | None
    max_alerts_monthly: int | None
    features: dict[str, Any]
    is_current: bool = False
    is_popular: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tier": self.tier,
            "monthly_price": self.monthly_price,
            "annual_price": self.annual_price,
            "annual_monthly_price": self.annual_monthly_price,
            "annual_savings_percent": self.annual_savings_percent,
            "max_users": self.max_users,
            "max_ai_investigations_monthly": self.max_ai_investigations_monthly,
            "max_alerts_monthly": self.max_alerts_monthly,
            "features": self.features,
            "is_current": self.is_current,
            "is_popular": self.is_popular,
        }


@dataclass
class SubscriptionResponse:
    """Subscription information for API response."""

    plan: PlanResponse
    status: str
    billing_cycle: str
    is_trialing: bool
    days_remaining_trial: int | None
    current_period_end: str | None
    days_until_renewal: int
    cancel_at_period_end: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plan": self.plan.to_dict(),
            "status": self.status,
            "billing_cycle": self.billing_cycle,
            "is_trialing": self.is_trialing,
            "days_remaining_trial": self.days_remaining_trial,
            "current_period_end": self.current_period_end,
            "days_until_renewal": self.days_until_renewal,
            "cancel_at_period_end": self.cancel_at_period_end,
        }


@dataclass
class UsageResponse:
    """Usage information for API response."""

    plan: dict[str, str]
    limits: dict[str, dict[str, Any]]
    features: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plan": self.plan,
            "limits": self.limits,
            "features": self.features,
        }


class BillingAPI:
    """API handlers for billing operations."""

    def __init__(
        self,
        store: Any,
        stripe_service: StripeBillingService | None = None,
    ):
        self._store = store
        self._stripe = stripe_service
        self._plan_manager = PlanManager()
        self._feature_gate = FeatureGateService(store)
        self._usage_tracker = UsageTracker(store)
        self._usage_reporter = UsageReporter(self._usage_tracker)

    async def list_plans(
        self,
        org_id: UUID | None = None,
        include_enterprise: bool = False,
    ) -> list[dict[str, Any]]:
        """List all available pricing plans."""
        plans = self._plan_manager.get_public_plans()

        if include_enterprise:
            enterprise = self._plan_manager.get_plan("enterprise")
            if enterprise:
                plans.append(enterprise)

        current_plan_name = None
        if org_id:
            current_plan = await self._feature_gate.get_organization_plan(org_id)
            if current_plan:
                current_plan_name = current_plan.name

        return [
            PlanResponse(
                name=p.name,
                display_name=p.display_name,
                description=p.description,
                tier=p.tier.value,
                monthly_price=p.monthly_price,
                annual_price=p.annual_price,
                annual_monthly_price=p.annual_monthly_price,
                annual_savings_percent=p.annual_savings_percent,
                max_users=p.max_users,
                max_ai_investigations_monthly=p.max_ai_investigations_monthly,
                max_alerts_monthly=p.max_alerts_monthly,
                features=p.features.to_dict(),
                is_current=(p.name == current_plan_name),
                is_popular=(p.name == "growth"),
            ).to_dict()
            for p in plans
        ]

    async def get_plan_comparison(self) -> list[dict[str, Any]]:
        """Get plan comparison data for pricing page."""
        return self._plan_manager.get_plan_comparison()

    async def get_subscription(
        self,
        org_id: UUID,
    ) -> dict[str, Any] | None:
        """Get current subscription details."""
        subscription = await self._store.get_subscription(org_id)

        if not subscription:
            return None

        plan = await self._store.get_plan(subscription.plan_id)
        if not plan:
            plan = self._plan_manager.get_community_plan()

        return SubscriptionResponse(
            plan=PlanResponse(
                name=plan.name,
                display_name=plan.display_name,
                description=plan.description,
                tier=plan.tier.value,
                monthly_price=plan.monthly_price,
                annual_price=plan.annual_price,
                annual_monthly_price=plan.annual_monthly_price,
                annual_savings_percent=plan.annual_savings_percent,
                max_users=plan.max_users,
                max_ai_investigations_monthly=plan.max_ai_investigations_monthly,
                max_alerts_monthly=plan.max_alerts_monthly,
                features=plan.features.to_dict(),
                is_current=True,
            ),
            status=subscription.status.value,
            billing_cycle=subscription.billing_cycle.value,
            is_trialing=subscription.is_trialing,
            days_remaining_trial=subscription.days_remaining_trial,
            current_period_end=subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None,
            days_until_renewal=subscription.days_until_renewal,
            cancel_at_period_end=subscription.cancel_at_period_end,
        ).to_dict()

    async def get_usage(self, org_id: UUID) -> dict[str, Any]:
        """Get current usage and limits."""
        return await self._feature_gate.get_organization_limits(org_id)

    async def get_usage_stats(
        self,
        org_id: UUID,
        year_month: str | None = None,
    ) -> dict[str, Any]:
        """Get usage statistics."""
        stats = await self._usage_tracker.get_usage_stats(org_id, year_month)
        return stats.to_dict()

    async def get_usage_history(
        self,
        org_id: UUID,
        months: int = 12,
    ) -> list[dict[str, Any]]:
        """Get usage history."""
        history = await self._usage_tracker.get_usage_history(org_id, months)
        return [h.to_dict() for h in history]

    async def get_usage_report(
        self,
        org_id: UUID,
        year_month: str | None = None,
    ) -> dict[str, Any]:
        """Get monthly usage report."""
        return await self._usage_reporter.generate_monthly_report(org_id, year_month)

    async def get_usage_forecast(
        self,
        org_id: UUID,
        months_ahead: int = 3,
    ) -> dict[str, Any]:
        """Get usage forecast."""
        return await self._usage_reporter.generate_usage_forecast(org_id, months_ahead)

    async def create_checkout(
        self,
        org_id: UUID,
        plan_name: str,
        billing_cycle: str = "monthly",
        email: str | None = None,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe checkout session."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        cycle = BillingCycle.ANNUAL if billing_cycle == "annual" else BillingCycle.MONTHLY

        result = await self._stripe.create_checkout_session(
            org_id=org_id,
            plan_name=plan_name,
            billing_cycle=cycle,
            success_url=success_url,
            cancel_url=cancel_url,
            email=email,
        )

        return {
            "checkout_url": result.checkout_url,
            "session_id": result.session_id,
        }

    async def create_portal(
        self,
        org_id: UUID,
        return_url: str | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe customer portal session."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        result = await self._stripe.create_portal_session(org_id, return_url)
        return {"portal_url": result.portal_url}

    async def change_plan(
        self,
        org_id: UUID,
        new_plan_name: str,
        billing_cycle: str | None = None,
    ) -> dict[str, Any]:
        """Change subscription plan."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        cycle = None
        if billing_cycle:
            cycle = BillingCycle.ANNUAL if billing_cycle == "annual" else BillingCycle.MONTHLY

        subscription = await self._stripe.change_plan(
            org_id=org_id,
            new_plan_name=new_plan_name,
            billing_cycle=cycle,
        )

        return subscription.to_dict()

    async def calculate_proration(
        self,
        org_id: UUID,
        new_plan_name: str,
    ) -> dict[str, Any]:
        """Calculate proration for plan change."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            return {"error": "No active subscription"}

        current_plan = await self._store.get_plan(subscription.plan_id)
        if not current_plan:
            return {"error": "Current plan not found"}

        return self._plan_manager.calculate_proration(
            from_plan=current_plan.name,
            to_plan=new_plan_name,
            days_remaining=subscription.days_until_renewal,
            billing_cycle=subscription.billing_cycle.value,
        )

    async def cancel_subscription(
        self,
        org_id: UUID,
        reason: str = "",
        immediate: bool = False,
        feedback: str = "",
    ) -> dict[str, Any]:
        """Cancel subscription."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        subscription = await self._stripe.cancel_subscription(
            org_id=org_id,
            reason=reason,
            immediate=immediate,
            feedback=feedback,
        )

        return subscription.to_dict()

    async def reactivate_subscription(
        self,
        org_id: UUID,
    ) -> dict[str, Any]:
        """Reactivate canceled subscription."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        subscription = await self._stripe.reactivate_subscription(org_id)
        return subscription.to_dict()

    async def get_invoices(
        self,
        org_id: UUID,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get invoices for organization."""
        if not self._stripe:
            return []

        invoices = await self._stripe.get_invoices(org_id, limit)
        return [inv.to_dict() for inv in invoices]

    async def get_payment_methods(
        self,
        org_id: UUID,
    ) -> list[dict[str, Any]]:
        """Get payment methods for organization."""
        if not self._stripe:
            return []

        pms = await self._stripe.get_payment_methods(org_id)
        return [pm.to_dict() for pm in pms]

    async def check_feature(
        self,
        org_id: UUID,
        feature_key: str,
    ) -> dict[str, Any]:
        """Check if a feature is available."""
        result = await self._feature_gate.can_access_feature(org_id, feature_key)

        if result.allowed:
            return {"available": True, "feature": feature_key}

        upgrade_info = await self._feature_gate.get_upgrade_prompt(org_id, feature_key)
        return {
            "available": False,
            "feature": feature_key,
            "reason": result.reason,
            "upgrade": upgrade_info,
        }

    async def check_usage_limit(
        self,
        org_id: UUID,
        metric_type: str,
        increment: int = 1,
    ) -> dict[str, Any]:
        """Check if within usage limits."""
        mt = MetricType(metric_type)
        result = await self._feature_gate.check_usage_limit(org_id, mt, increment)

        return {
            "within_limit": result.within_limit,
            "reason": result.reason,
            "current_usage": result.current_usage,
            "limit": result.limit,
            "remaining": result.remaining,
        }

    async def get_feature_status(
        self,
        org_id: UUID,
        features: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get status of multiple features."""
        return await self._feature_gate.get_feature_status(org_id, features)

    async def get_upgrade_options(
        self,
        org_id: UUID,
    ) -> list[dict[str, Any]]:
        """Get available upgrade options."""
        current_plan = await self._feature_gate.get_organization_plan(org_id)
        if not current_plan:
            current_plan = self._plan_manager.get_community_plan()

        options = self._plan_manager.get_upgrade_options(current_plan.name)
        return [p.to_dict() for p in options]

    async def handle_webhook(
        self,
        payload: bytes,
        signature: str,
    ) -> dict[str, Any]:
        """Handle Stripe webhook."""
        if not self._stripe:
            raise ValueError("Stripe not configured")

        result = await self._stripe.handle_webhook(payload, signature)
        return {
            "status": result.status,
            "event_type": result.event_type,
        }

    async def track_usage(
        self,
        org_id: UUID,
        metric_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Track a usage event."""
        mt = MetricType(metric_type)

        if mt == MetricType.AI_INVESTIGATION:
            await self._usage_tracker.track_ai_investigation(
                org_id,
                incident_id=metadata.get("incident_id") if metadata else None,
                metadata=metadata,
            )
        elif mt == MetricType.ALERT_PROCESSED:
            await self._usage_tracker.track_alert_processed(
                org_id,
                count=metadata.get("count", 1) if metadata else 1,
                metadata=metadata,
            )
        elif mt == MetricType.API_CALL:
            await self._usage_tracker.track_api_call(
                org_id,
                endpoint=metadata.get("endpoint", "") if metadata else "",
                method=metadata.get("method", "GET") if metadata else "GET",
                metadata=metadata,
            )
        elif mt == MetricType.POSTMORTEM_GENERATED:
            await self._usage_tracker.track_postmortem_generated(
                org_id,
                incident_id=metadata.get("incident_id", "") if metadata else "",
                metadata=metadata,
            )
        elif mt == MetricType.AUTO_FIX_PR:
            await self._usage_tracker.track_auto_fix_pr(
                org_id,
                incident_id=metadata.get("incident_id", "") if metadata else "",
                pr_url=metadata.get("pr_url") if metadata else None,
                metadata=metadata,
            )
        elif mt == MetricType.SIMILAR_SEARCH:
            await self._usage_tracker.track_similar_search(
                org_id,
                incident_id=metadata.get("incident_id") if metadata else None,
                query=metadata.get("query") if metadata else None,
                results_count=metadata.get("results_count", 0) if metadata else 0,
                metadata=metadata,
            )

        return {"tracked": True, "metric_type": metric_type}
