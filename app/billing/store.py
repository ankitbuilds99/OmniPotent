"""In-memory billing store implementation for testing and development."""

from __future__ import annotations

import logging
from datetime import datetime, date, timezone
from typing import Any
from uuid import UUID

from app.billing.models import (
    Organization,
    PricingPlan,
    Subscription,
    Invoice,
    PaymentMethod,
    UsageRecord,
    UsageSummary,
    FeatureOverride,
    MetricType,
)
from app.billing.plans import PRICING_PLANS

log = logging.getLogger("devcopilot.billing.store")


class InMemoryBillingStore:
    """In-memory implementation of billing store for testing."""

    def __init__(self):
        self._organizations: dict[UUID, Organization] = {}
        self._subscriptions: dict[UUID, Subscription] = {}
        self._subscriptions_by_stripe: dict[str, Subscription] = {}
        self._plans: dict[str, PricingPlan] = dict(PRICING_PLANS)
        self._invoices: dict[UUID, Invoice] = {}
        self._payment_methods: dict[UUID, list[PaymentMethod]] = {}
        self._usage_records: dict[UUID, list[UsageRecord]] = {}
        self._usage_summaries: dict[str, UsageSummary] = {}
        self._feature_overrides: dict[UUID, list[FeatureOverride]] = {}

    async def get_organization(self, org_id: UUID) -> Organization | None:
        """Get organization by ID."""
        return self._organizations.get(org_id)

    async def save_organization(self, org: Organization) -> None:
        """Save organization."""
        self._organizations[org.org_id] = org

    async def get_subscription(self, org_id: UUID) -> Subscription | None:
        """Get active subscription for organization."""
        return self._subscriptions.get(org_id)

    async def get_subscription_by_stripe_id(
        self, stripe_id: str
    ) -> Subscription | None:
        """Get subscription by Stripe ID."""
        return self._subscriptions_by_stripe.get(stripe_id)

    async def save_subscription(self, sub: Subscription) -> None:
        """Save subscription."""
        self._subscriptions[sub.org_id] = sub
        if sub.stripe_subscription_id:
            self._subscriptions_by_stripe[sub.stripe_subscription_id] = sub

    async def get_plan(self, plan_id: UUID) -> PricingPlan | None:
        """Get plan by ID."""
        for plan in self._plans.values():
            if plan.plan_id == plan_id:
                return plan
        return None

    async def get_plan_by_name(self, name: str) -> PricingPlan | None:
        """Get plan by name."""
        return self._plans.get(name)

    async def get_all_plans(self) -> list[PricingPlan]:
        """Get all plans."""
        return list(self._plans.values())

    async def save_invoice(self, invoice: Invoice) -> None:
        """Save invoice."""
        self._invoices[invoice.invoice_id] = invoice

    async def get_invoices(self, org_id: UUID, limit: int = 10) -> list[Invoice]:
        """Get invoices for organization."""
        invoices = [i for i in self._invoices.values() if i.org_id == org_id]
        return sorted(invoices, key=lambda x: x.created_at, reverse=True)[:limit]

    async def get_payment_methods(self, org_id: UUID) -> list[PaymentMethod]:
        """Get payment methods for organization."""
        return self._payment_methods.get(org_id, [])

    async def save_payment_method(self, pm: PaymentMethod) -> None:
        """Save payment method."""
        if pm.org_id not in self._payment_methods:
            self._payment_methods[pm.org_id] = []

        existing = [p for p in self._payment_methods[pm.org_id] if p.payment_method_id != pm.payment_method_id]
        existing.append(pm)
        self._payment_methods[pm.org_id] = existing

    async def delete_payment_method(self, pm_id: UUID) -> None:
        """Delete payment method."""
        for org_id, pms in self._payment_methods.items():
            self._payment_methods[org_id] = [p for p in pms if p.payment_method_id != pm_id]

    async def create_usage_record(self, record: UsageRecord) -> None:
        """Create usage record."""
        if record.org_id not in self._usage_records:
            self._usage_records[record.org_id] = []
        self._usage_records[record.org_id].append(record)

    async def get_usage_summary(
        self, org_id: UUID, year_month: str
    ) -> UsageSummary | None:
        """Get usage summary for organization and month."""
        key = f"{org_id}:{year_month}"
        return self._usage_summaries.get(key)

    async def upsert_usage_summary(self, summary: UsageSummary) -> None:
        """Insert or update usage summary."""
        key = f"{summary.org_id}:{summary.year_month}"
        self._usage_summaries[key] = summary

    async def increment_summary_field(
        self, org_id: UUID, year_month: str, field: str, increment: int
    ) -> None:
        """Increment a usage summary field."""
        key = f"{org_id}:{year_month}"
        summary = self._usage_summaries.get(key)

        if not summary:
            from uuid import uuid4
            summary = UsageSummary(
                summary_id=uuid4(),
                org_id=org_id,
                year_month=year_month,
            )
            self._usage_summaries[key] = summary

        current = getattr(summary, field, 0)
        setattr(summary, field, current + increment)
        summary.updated_at = datetime.now(timezone.utc)

    async def get_usage_history(
        self, org_id: UUID, months: int
    ) -> list[UsageSummary]:
        """Get usage history for organization."""
        summaries = [
            s for key, s in self._usage_summaries.items()
            if key.startswith(str(org_id))
        ]
        return sorted(summaries, key=lambda x: x.year_month, reverse=True)[:months]

    async def get_usage_records(
        self,
        org_id: UUID,
        metric_type: MetricType | None,
        start_date: date,
        end_date: date,
    ) -> list[UsageRecord]:
        """Get usage records for organization."""
        records = self._usage_records.get(org_id, [])

        filtered = []
        for r in records:
            if r.period_start < start_date or r.period_end > end_date:
                continue
            if metric_type and r.metric_type != metric_type:
                continue
            filtered.append(r)

        return filtered

    async def get_feature_overrides(self, org_id: UUID) -> list[FeatureOverride]:
        """Get feature overrides for organization."""
        return self._feature_overrides.get(org_id, [])

    async def save_feature_override(self, override: FeatureOverride) -> None:
        """Save feature override."""
        if override.org_id not in self._feature_overrides:
            self._feature_overrides[override.org_id] = []

        existing = [
            o for o in self._feature_overrides[override.org_id]
            if o.feature_key != override.feature_key
        ]
        existing.append(override)
        self._feature_overrides[override.org_id] = existing

    async def delete_feature_override(
        self, org_id: UUID, feature_key: str
    ) -> None:
        """Delete feature override."""
        if org_id in self._feature_overrides:
            self._feature_overrides[org_id] = [
                o for o in self._feature_overrides[org_id]
                if o.feature_key != feature_key
            ]

    def reset(self) -> None:
        """Reset all data (for testing)."""
        self._organizations.clear()
        self._subscriptions.clear()
        self._subscriptions_by_stripe.clear()
        self._invoices.clear()
        self._payment_methods.clear()
        self._usage_records.clear()
        self._usage_summaries.clear()
        self._feature_overrides.clear()


class FakeStripeClient:
    """Fake Stripe client for testing."""

    def __init__(self):
        self._customers: dict[str, Any] = {}
        self._subscriptions: dict[str, Any] = {}
        self._sessions: dict[str, Any] = {}
        self._counter = 0
        self.events: list[dict[str, Any]] = []

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    def create_customer(self, **kwargs: Any) -> Any:
        """Create a customer."""
        customer_id = self._next_id("cus")
        customer = type("Customer", (), {
            "id": customer_id,
            "email": kwargs.get("email"),
            "name": kwargs.get("name"),
            "metadata": kwargs.get("metadata", {}),
            "invoice_settings": type("InvoiceSettings", (), {
                "default_payment_method": None
            })(),
        })()
        self._customers[customer_id] = customer
        return customer

    def get_customer(self, customer_id: str) -> Any:
        """Get a customer."""
        return self._customers.get(customer_id)

    def update_customer(self, customer_id: str, **kwargs: Any) -> Any:
        """Update a customer."""
        customer = self._customers.get(customer_id)
        if customer:
            for key, value in kwargs.items():
                setattr(customer, key, value)
        return customer

    def create_checkout_session(self, **kwargs: Any) -> Any:
        """Create a checkout session."""
        session_id = self._next_id("cs")
        session = type("Session", (), {
            "id": session_id,
            "url": f"https://checkout.stripe.com/c/pay/{session_id}",
            "customer": kwargs.get("customer"),
            "metadata": kwargs.get("metadata", {}),
        })()
        self._sessions[session_id] = session
        return session

    def create_billing_portal_session(self, **kwargs: Any) -> Any:
        """Create a billing portal session."""
        session_id = self._next_id("bps")
        return type("PortalSession", (), {
            "id": session_id,
            "url": f"https://billing.stripe.com/p/session/{session_id}",
        })()

    def create_subscription(self, **kwargs: Any) -> Any:
        """Create a subscription."""
        sub_id = self._next_id("sub")
        now = int(datetime.now(timezone.utc).timestamp())
        sub = type("Subscription", (), {
            "id": sub_id,
            "customer": kwargs.get("customer"),
            "status": "trialing" if kwargs.get("trial_period_days") else "active",
            "current_period_start": now,
            "current_period_end": now + 30 * 24 * 3600,
            "trial_start": now if kwargs.get("trial_period_days") else None,
            "trial_end": now + kwargs.get("trial_period_days", 0) * 24 * 3600 if kwargs.get("trial_period_days") else None,
            "cancel_at_period_end": False,
            "canceled_at": None,
            "metadata": kwargs.get("metadata", {}),
            "items": type("Items", (), {
                "data": [type("Item", (), {
                    "id": self._next_id("si"),
                    "price": type("Price", (), {
                        "id": kwargs.get("items", [{}])[0].get("price"),
                        "recurring": type("Recurring", (), {"interval": "month"})(),
                    })(),
                })()],
            })(),
        })()
        self._subscriptions[sub_id] = sub
        return sub

    def get_subscription(self, subscription_id: str) -> Any:
        """Get a subscription."""
        return self._subscriptions.get(subscription_id)

    def update_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        """Update a subscription."""
        sub = self._subscriptions.get(subscription_id)
        if sub:
            for key, value in kwargs.items():
                if key == "items":
                    continue
                setattr(sub, key, value)
        return sub

    def cancel_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        """Cancel a subscription."""
        sub = self._subscriptions.get(subscription_id)
        if sub:
            sub.status = "canceled"
            sub.canceled_at = int(datetime.now(timezone.utc).timestamp())
        return sub

    def list_invoices(self, **kwargs: Any) -> Any:
        """List invoices."""
        return type("InvoiceList", (), {"data": []})()

    def get_invoice(self, invoice_id: str) -> Any:
        """Get an invoice."""
        return None

    def list_payment_methods(self, **kwargs: Any) -> Any:
        """List payment methods."""
        return type("PMList", (), {"data": []})()

    def get_payment_method(self, payment_method_id: str) -> Any:
        """Get a payment method."""
        return None

    def attach_payment_method(self, payment_method_id: str, **kwargs: Any) -> Any:
        """Attach a payment method."""
        return None

    def detach_payment_method(self, payment_method_id: str) -> Any:
        """Detach a payment method."""
        return None

    def construct_webhook_event(
        self, payload: bytes, signature: str, secret: str
    ) -> Any:
        """Construct a webhook event."""
        import json
        data = json.loads(payload)
        return type("Event", (), {
            "type": data.get("type"),
            "data": type("Data", (), {
                "object": type("Object", (), data.get("data", {}).get("object", {}))(),
            })(),
        })()

    def create_meter_event(
        self,
        customer: str,
        event_name: str,
        amount: int,
        idempotency_key: str,
    ) -> Any:
        """Create a meter event (usage-based billing)."""
        event_id = self._next_id("mtre")
        event = {
            "id": event_id,
            "customer_id": customer,
            "event_name": event_name,
            "amount": amount,
            "idempotency_key": idempotency_key,
        }
        self.events.append(event)
        return type("MeterEvent", (), event)()

    def reset(self) -> None:
        """Reset all data."""
        self._customers.clear()
        self._subscriptions.clear()
        self._sessions.clear()
        self._counter = 0
        self.events.clear()
