"""Subscription lifecycle management.

Full subscription lifecycle:
- Trial management with automatic conversion
- Plan upgrades with proration
- Downgrades scheduled at period end
- Dunning and failed payment recovery
- Cancellation flows with win-back
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("devcopilot.billing.subscriptions")


class SubscriptionStatus(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    PAUSED = "paused"
    UNPAID = "unpaid"


class PlanInterval(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


@dataclass
class Plan:
    """Pricing plan definition."""

    id: str
    name: str
    interval: PlanInterval
    price_cents: int

    investigations_per_month: int
    seats: int
    integrations: int
    retention_days: int

    features: list[str] = field(default_factory=list)
    stripe_price_id: str = ""
    stripe_product_id: str = ""


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        interval=PlanInterval.MONTHLY,
        price_cents=0,
        investigations_per_month=50,
        seats=3,
        integrations=2,
        retention_days=7,
        features=["Basic investigations", "Community support"],
    ),
    "starter_monthly": Plan(
        id="starter_monthly",
        name="Starter",
        interval=PlanInterval.MONTHLY,
        price_cents=4900,
        investigations_per_month=500,
        seats=10,
        integrations=5,
        retention_days=30,
        features=["Unlimited investigations", "Email support", "SSO"],
        stripe_price_id="price_starter_monthly",
    ),
    "starter_yearly": Plan(
        id="starter_yearly",
        name="Starter (Annual)",
        interval=PlanInterval.YEARLY,
        price_cents=47000,
        investigations_per_month=500,
        seats=10,
        integrations=5,
        retention_days=30,
        features=["Unlimited investigations", "Email support", "SSO"],
        stripe_price_id="price_starter_yearly",
    ),
    "pro_monthly": Plan(
        id="pro_monthly",
        name="Pro",
        interval=PlanInterval.MONTHLY,
        price_cents=14900,
        investigations_per_month=-1,
        seats=50,
        integrations=20,
        retention_days=90,
        features=[
            "Unlimited investigations",
            "Priority support",
            "SSO + SCIM",
            "Custom integrations",
            "SLA",
        ],
        stripe_price_id="price_pro_monthly",
    ),
    "enterprise": Plan(
        id="enterprise",
        name="Enterprise",
        interval=PlanInterval.YEARLY,
        price_cents=0,
        investigations_per_month=-1,
        seats=-1,
        integrations=-1,
        retention_days=365,
        features=[
            "Everything in Pro",
            "Dedicated support",
            "Custom deployment",
            "Compliance pack",
            "Training",
        ],
    ),
}


@dataclass
class Subscription:
    """Customer subscription state."""

    id: str
    org_id: str
    plan_id: str
    status: SubscriptionStatus

    current_period_start: datetime
    current_period_end: datetime

    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None
    cancel_reason: str = ""

    stripe_subscription_id: str = ""
    stripe_customer_id: str = ""

    investigations_used: int = 0
    seats_used: int = 0

    trial_end: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_active(self) -> bool:
        return self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)

    @property
    def is_trialing(self) -> bool:
        return self.status == SubscriptionStatus.TRIALING

    @property
    def days_until_renewal(self) -> int:
        delta = self.current_period_end - datetime.now(timezone.utc)
        return max(0, delta.days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "is_active": self.is_active,
            "is_trialing": self.is_trialing,
            "current_period_end": self.current_period_end.isoformat(),
            "days_until_renewal": self.days_until_renewal,
            "cancel_at_period_end": self.cancel_at_period_end,
        }


class BillingStore(Protocol):
    """Protocol for billing data storage."""

    async def get_org(self, org_id: str) -> Any: ...
    async def get_subscription(self, org_id: str) -> Subscription | None: ...
    async def save_subscription(self, sub: Subscription) -> None: ...
    async def set_plan(self, org_id: str, plan_id: str) -> None: ...
    async def list_churned_subscriptions(
        self, since: datetime
    ) -> list[Subscription]: ...


class SubscriptionManager:
    """Manages subscription lifecycle."""

    def __init__(self, store: BillingStore, stripe_client: Any = None):
        self._store = store
        self._stripe = stripe_client

    async def create_subscription(
        self,
        org_id: str,
        plan_id: str,
        payment_method_id: str | None = None,
        trial_days: int = 14,
    ) -> Subscription:
        """Create a new subscription with optional trial."""
        plan = PLANS.get(plan_id)
        if not plan:
            raise ValueError(f"Unknown plan: {plan_id}")

        org = await self._store.get_org(org_id)
        if not org:
            raise ValueError(f"Unknown org: {org_id}")

        now = datetime.now(timezone.utc)

        stripe_sub = None
        if plan.stripe_price_id and self._stripe:
            stripe_params: dict[str, Any] = {
                "customer": getattr(org, "stripe_customer_id", ""),
                "items": [{"price": plan.stripe_price_id}],
                "metadata": {"org_id": org_id, "plan": plan_id},
            }

            if trial_days > 0:
                stripe_params["trial_period_days"] = trial_days

            if payment_method_id:
                stripe_params["default_payment_method"] = payment_method_id

            stripe_sub = self._stripe.subscriptions.create(**stripe_params)

        subscription = Subscription(
            id=f"sub_{org_id}_{int(now.timestamp())}",
            org_id=org_id,
            plan_id=plan_id,
            status=(
                SubscriptionStatus.TRIALING
                if trial_days > 0
                else SubscriptionStatus.ACTIVE
            ),
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            trial_end=now + timedelta(days=trial_days) if trial_days > 0 else None,
            stripe_subscription_id=stripe_sub.id if stripe_sub else "",
            stripe_customer_id=getattr(org, "stripe_customer_id", ""),
        )

        await self._store.save_subscription(subscription)
        await self._store.set_plan(org_id, plan_id)

        log.info(
            "Created subscription %s for org %s (plan=%s)",
            subscription.id,
            org_id,
            plan_id,
        )
        return subscription

    async def upgrade_plan(
        self,
        org_id: str,
        new_plan_id: str,
        prorate: bool = True,
    ) -> Subscription:
        """Upgrade to a higher plan with proration."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        old_plan = PLANS.get(subscription.plan_id)
        new_plan = PLANS.get(new_plan_id)

        if not old_plan or not new_plan:
            raise ValueError(f"Unknown plan: {new_plan_id}")

        if new_plan.price_cents <= old_plan.price_cents:
            raise ValueError("Use downgrade_plan for downgrades")

        if subscription.stripe_subscription_id and self._stripe:
            self._stripe.subscriptions.modify(
                subscription.stripe_subscription_id,
                items=[
                    {
                        "id": subscription.stripe_subscription_id,
                        "price": new_plan.stripe_price_id,
                    }
                ],
                proration_behavior="create_prorations" if prorate else "none",
                metadata={"plan": new_plan_id},
            )

        subscription.plan_id = new_plan_id
        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)
        await self._store.set_plan(org_id, new_plan_id)

        log.info("Upgraded org %s from %s to %s", org_id, old_plan.id, new_plan_id)
        return subscription

    async def downgrade_plan(
        self,
        org_id: str,
        new_plan_id: str,
    ) -> Subscription:
        """Downgrade to lower plan at end of billing period."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        new_plan = PLANS.get(new_plan_id)
        if not new_plan:
            raise ValueError(f"Unknown plan: {new_plan_id}")

        if subscription.stripe_subscription_id and self._stripe:
            self._stripe.subscriptions.update(
                subscription.stripe_subscription_id,
                cancel_at_period_end=False,
                items=[
                    {
                        "id": subscription.stripe_subscription_id,
                        "price": new_plan.stripe_price_id,
                    }
                ],
                proration_behavior="none",
                metadata={"pending_plan": new_plan_id},
            )

        log.info(
            "Scheduled downgrade for org %s to %s at period end (%s)",
            org_id,
            new_plan_id,
            subscription.current_period_end,
        )
        return subscription

    async def cancel_subscription(
        self,
        org_id: str,
        reason: str = "",
        immediate: bool = False,
        feedback: str = "",
    ) -> Subscription:
        """Cancel subscription with optional win-back flow."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        now = datetime.now(timezone.utc)

        if subscription.stripe_subscription_id and self._stripe:
            if immediate:
                self._stripe.subscriptions.delete(subscription.stripe_subscription_id)
            else:
                self._stripe.subscriptions.modify(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=True,
                    metadata={"cancel_reason": reason},
                )

        if immediate:
            subscription.status = SubscriptionStatus.CANCELED
            subscription.canceled_at = now
        else:
            subscription.cancel_at_period_end = True

        subscription.cancel_reason = reason
        subscription.updated_at = now
        await self._store.save_subscription(subscription)

        await self._track_cancellation(org_id, reason, feedback)

        log.info(
            "Canceled subscription for org %s (immediate=%s, reason=%s)",
            org_id,
            immediate,
            reason,
        )
        return subscription

    async def _track_cancellation(
        self, org_id: str, reason: str, feedback: str
    ) -> None:
        """Track cancellation for churn analysis."""
        log.info(
            "Cancellation tracked: org=%s reason=%s feedback=%s",
            org_id,
            reason,
            feedback,
        )

    async def handle_payment_failed(self, org_id: str, attempt: int = 1) -> None:
        """Handle failed payment with dunning sequence."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            return

        subscription.status = SubscriptionStatus.PAST_DUE
        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        await self._send_dunning_email(org_id, attempt)

        if attempt >= 3:
            subscription.status = SubscriptionStatus.UNPAID
            await self._store.save_subscription(subscription)
            log.warning(
                "Subscription for org %s marked unpaid after %d failed attempts",
                org_id,
                attempt,
            )

    async def _send_dunning_email(self, org_id: str, attempt: int) -> None:
        """Send dunning email for failed payment."""
        templates = {
            1: "payment_failed_first",
            2: "payment_failed_second",
            3: "payment_failed_final",
        }
        template = templates.get(attempt, "payment_failed_first")
        log.info("Would send %s email for org %s", template, org_id)

    async def reactivate_subscription(self, org_id: str) -> Subscription:
        """Reactivate a canceled or paused subscription."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        if subscription.stripe_subscription_id and self._stripe:
            self._stripe.subscriptions.modify(
                subscription.stripe_subscription_id,
                cancel_at_period_end=False,
            )

        subscription.status = SubscriptionStatus.ACTIVE
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        subscription.cancel_reason = ""
        subscription.updated_at = datetime.now(timezone.utc)

        await self._store.save_subscription(subscription)

        log.info("Reactivated subscription for org %s", org_id)
        return subscription

    async def check_usage_limits(self, org_id: str) -> dict[str, Any]:
        """Check current usage against plan limits."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            return {"within_limits": False, "error": "No subscription"}

        plan = PLANS.get(subscription.plan_id)
        if not plan:
            return {"within_limits": False, "error": "Unknown plan"}

        inv_limit = plan.investigations_per_month
        inv_used = subscription.investigations_used

        return {
            "within_limits": inv_limit == -1 or inv_used < inv_limit,
            "investigations": {
                "used": inv_used,
                "limit": inv_limit,
                "remaining": inv_limit - inv_used if inv_limit != -1 else -1,
            },
            "seats": {
                "used": subscription.seats_used,
                "limit": plan.seats,
            },
        }

    async def increment_usage(self, org_id: str, metric: str = "investigations") -> bool:
        """Increment usage counter, return False if limit exceeded."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            return False

        plan = PLANS.get(subscription.plan_id)
        if not plan:
            return False

        if metric == "investigations":
            limit = plan.investigations_per_month
            if limit != -1 and subscription.investigations_used >= limit:
                return False
            subscription.investigations_used += 1

        await self._store.save_subscription(subscription)
        return True
