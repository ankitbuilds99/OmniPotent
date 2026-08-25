"""Stripe billing integration service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.billing.models import (
    Organization,
    PricingPlan,
    Subscription,
    Invoice,
    PaymentMethod,
    SubscriptionStatus,
    BillingCycle,
    InvoiceStatus,
)
from app.billing.plans import PlanManager

log = logging.getLogger("omnipotent.billing.stripe")


class StripeClient(Protocol):
    """Protocol for Stripe client operations."""

    def create_customer(self, **kwargs: Any) -> Any: ...
    def get_customer(self, customer_id: str) -> Any: ...
    def update_customer(self, customer_id: str, **kwargs: Any) -> Any: ...
    def create_checkout_session(self, **kwargs: Any) -> Any: ...
    def create_billing_portal_session(self, **kwargs: Any) -> Any: ...
    def create_subscription(self, **kwargs: Any) -> Any: ...
    def get_subscription(self, subscription_id: str) -> Any: ...
    def update_subscription(self, subscription_id: str, **kwargs: Any) -> Any: ...
    def cancel_subscription(self, subscription_id: str, **kwargs: Any) -> Any: ...
    def get_invoice(self, invoice_id: str) -> Any: ...
    def list_invoices(self, **kwargs: Any) -> Any: ...
    def get_payment_method(self, payment_method_id: str) -> Any: ...
    def list_payment_methods(self, **kwargs: Any) -> Any: ...
    def attach_payment_method(self, payment_method_id: str, **kwargs: Any) -> Any: ...
    def detach_payment_method(self, payment_method_id: str) -> Any: ...
    def construct_webhook_event(
        self, payload: bytes, signature: str, secret: str
    ) -> Any: ...


class BillingStore(Protocol):
    """Protocol for billing data storage."""

    async def get_organization(self, org_id: UUID) -> Organization | None: ...
    async def save_organization(self, org: Organization) -> None: ...
    async def get_subscription(self, org_id: UUID) -> Subscription | None: ...
    async def get_subscription_by_stripe_id(
        self, stripe_id: str
    ) -> Subscription | None: ...
    async def save_subscription(self, sub: Subscription) -> None: ...
    async def get_plan_by_name(self, name: str) -> PricingPlan | None: ...
    async def save_invoice(self, invoice: Invoice) -> None: ...
    async def get_payment_methods(self, org_id: UUID) -> list[PaymentMethod]: ...
    async def save_payment_method(self, pm: PaymentMethod) -> None: ...
    async def delete_payment_method(self, pm_id: UUID) -> None: ...


@dataclass
class CheckoutResult:
    """Result of creating a checkout session."""

    checkout_url: str
    session_id: str


@dataclass
class PortalResult:
    """Result of creating a billing portal session."""

    portal_url: str


@dataclass
class WebhookResult:
    """Result of processing a webhook."""

    status: str
    event_type: str
    data: dict[str, Any] | None = None


class StripeBillingService:
    """Handle Stripe billing operations."""

    def __init__(
        self,
        store: BillingStore,
        stripe_client: StripeClient,
        webhook_secret: str,
        frontend_url: str = "http://localhost:3000",
    ):
        self._store = store
        self._stripe = stripe_client
        self._webhook_secret = webhook_secret
        self._frontend_url = frontend_url
        self._plan_manager = PlanManager()

    async def create_customer(
        self,
        org: Organization,
        email: str,
        name: str | None = None,
    ) -> str:
        """Create a Stripe customer for an organization."""
        customer = self._stripe.create_customer(
            email=email,
            name=name or org.name,
            metadata={
                "organization_id": str(org.org_id),
                "organization_slug": org.slug,
            },
        )

        org.stripe_customer_id = customer.id
        org.billing_email = email
        org.updated_at = datetime.now(timezone.utc)
        await self._store.save_organization(org)

        log.info("Created Stripe customer %s for org %s", customer.id, org.org_id)
        return customer.id

    async def ensure_customer(
        self,
        org: Organization,
        email: str,
    ) -> str:
        """Ensure organization has a Stripe customer."""
        if org.stripe_customer_id:
            return org.stripe_customer_id
        return await self.create_customer(org, email)

    async def create_checkout_session(
        self,
        org_id: UUID,
        plan_name: str,
        billing_cycle: BillingCycle = BillingCycle.MONTHLY,
        success_url: str | None = None,
        cancel_url: str | None = None,
        email: str | None = None,
        trial_days: int | None = None,
        promotion_code: str | None = None,
    ) -> CheckoutResult:
        """Create a Stripe Checkout session for subscription."""
        org = await self._store.get_organization(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        plan = self._plan_manager.get_plan(plan_name)
        if not plan:
            raise ValueError(f"Unknown plan: {plan_name}")

        if not org.stripe_customer_id:
            if not email:
                raise ValueError("Email required to create customer")
            await self.create_customer(org, email)

        if billing_cycle == BillingCycle.ANNUAL:
            price_id = plan.stripe_annual_price_id
        else:
            price_id = plan.stripe_monthly_price_id

        if not price_id:
            raise ValueError(
                f"No Stripe price configured for {plan_name} {billing_cycle.value}"
            )

        session_params: dict[str, Any] = {
            "customer": org.stripe_customer_id,
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url
            or f"{self._frontend_url}/settings/billing?success=true",
            "cancel_url": cancel_url
            or f"{self._frontend_url}/settings/billing?canceled=true",
            "metadata": {
                "organization_id": str(org_id),
                "plan_name": plan_name,
                "billing_cycle": billing_cycle.value,
            },
            "subscription_data": {
                "metadata": {
                    "organization_id": str(org_id),
                    "plan_name": plan_name,
                }
            },
            "allow_promotion_codes": True,
        }

        if trial_days is not None and trial_days > 0:
            session_params["subscription_data"]["trial_period_days"] = trial_days
        elif plan.trial_days > 0:
            session_params["subscription_data"]["trial_period_days"] = plan.trial_days

        if promotion_code:
            session_params["discounts"] = [{"promotion_code": promotion_code}]
            session_params.pop("allow_promotion_codes", None)

        session = self._stripe.create_checkout_session(**session_params)

        log.info(
            "Created checkout session %s for org %s (plan=%s)",
            session.id,
            org_id,
            plan_name,
        )

        return CheckoutResult(
            checkout_url=session.url,
            session_id=session.id,
        )

    async def create_portal_session(
        self,
        org_id: UUID,
        return_url: str | None = None,
    ) -> PortalResult:
        """Create a Stripe Customer Portal session."""
        org = await self._store.get_organization(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        if not org.stripe_customer_id:
            raise ValueError("Organization has no Stripe customer")

        session = self._stripe.create_billing_portal_session(
            customer=org.stripe_customer_id,
            return_url=return_url or f"{self._frontend_url}/settings/billing",
        )

        return PortalResult(portal_url=session.url)

    async def change_plan(
        self,
        org_id: UUID,
        new_plan_name: str,
        billing_cycle: BillingCycle | None = None,
        prorate: bool = True,
    ) -> Subscription:
        """Change subscription plan."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        new_plan = self._plan_manager.get_plan(new_plan_name)
        if not new_plan:
            raise ValueError(f"Unknown plan: {new_plan_name}")

        cycle = billing_cycle or subscription.billing_cycle
        if cycle == BillingCycle.ANNUAL:
            price_id = new_plan.stripe_annual_price_id
        else:
            price_id = new_plan.stripe_monthly_price_id

        if not price_id:
            raise ValueError(f"No price for {new_plan_name} {cycle.value}")

        if subscription.stripe_subscription_id:
            stripe_sub = self._stripe.get_subscription(subscription.stripe_subscription_id)

            self._stripe.update_subscription(
                subscription.stripe_subscription_id,
                items=[{"id": stripe_sub.items.data[0].id, "price": price_id}],
                proration_behavior="create_prorations" if prorate else "none",
                metadata={"plan_name": new_plan_name},
            )

        subscription.plan_id = new_plan.plan_id
        subscription.billing_cycle = cycle
        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        log.info("Changed plan for org %s to %s", org_id, new_plan_name)
        return subscription

    async def cancel_subscription(
        self,
        org_id: UUID,
        reason: str = "",
        immediate: bool = False,
        feedback: str = "",
    ) -> Subscription:
        """Cancel subscription."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        now = datetime.now(timezone.utc)

        if subscription.stripe_subscription_id:
            if immediate:
                self._stripe.cancel_subscription(subscription.stripe_subscription_id)
            else:
                self._stripe.update_subscription(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=True,
                    metadata={
                        "cancel_reason": reason,
                        "cancel_feedback": feedback,
                    },
                )

        if immediate:
            subscription.status = SubscriptionStatus.CANCELED
            subscription.canceled_at = now
        else:
            subscription.cancel_at_period_end = True

        subscription.cancel_reason = reason
        subscription.updated_at = now
        await self._store.save_subscription(subscription)

        log.info(
            "Canceled subscription for org %s (immediate=%s, reason=%s)",
            org_id,
            immediate,
            reason,
        )
        return subscription

    async def reactivate_subscription(self, org_id: UUID) -> Subscription:
        """Reactivate a canceled subscription."""
        subscription = await self._store.get_subscription(org_id)
        if not subscription:
            raise ValueError(f"No subscription for org: {org_id}")

        if subscription.stripe_subscription_id:
            self._stripe.update_subscription(
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

    async def get_invoices(
        self,
        org_id: UUID,
        limit: int = 10,
    ) -> list[Invoice]:
        """Get invoices for an organization."""
        org = await self._store.get_organization(org_id)
        if not org or not org.stripe_customer_id:
            return []

        stripe_invoices = self._stripe.list_invoices(
            customer=org.stripe_customer_id,
            limit=limit,
        )

        invoices = []
        for inv in stripe_invoices.data:
            invoice = Invoice(
                invoice_id=uuid4(),
                org_id=org_id,
                stripe_invoice_id=inv.id,
                invoice_number=inv.number or "",
                amount_cents=inv.amount_due,
                amount_paid_cents=inv.amount_paid,
                currency=inv.currency,
                status=self._map_invoice_status(inv.status),
                invoice_pdf_url=inv.invoice_pdf or "",
                hosted_invoice_url=inv.hosted_invoice_url or "",
                due_date=datetime.fromtimestamp(inv.due_date, tz=timezone.utc)
                if inv.due_date
                else None,
                paid_at=datetime.fromtimestamp(inv.status_transitions.paid_at, tz=timezone.utc)
                if inv.status_transitions and inv.status_transitions.paid_at
                else None,
                period_start=datetime.fromtimestamp(inv.period_start, tz=timezone.utc)
                if inv.period_start
                else None,
                period_end=datetime.fromtimestamp(inv.period_end, tz=timezone.utc)
                if inv.period_end
                else None,
            )
            invoices.append(invoice)

        return invoices

    async def get_payment_methods(self, org_id: UUID) -> list[PaymentMethod]:
        """Get payment methods for an organization."""
        org = await self._store.get_organization(org_id)
        if not org or not org.stripe_customer_id:
            return []

        stripe_pms = self._stripe.list_payment_methods(
            customer=org.stripe_customer_id,
            type="card",
        )

        customer = self._stripe.get_customer(org.stripe_customer_id)
        default_pm_id = (
            customer.invoice_settings.default_payment_method
            if customer.invoice_settings
            else None
        )

        payment_methods = []
        for pm in stripe_pms.data:
            payment_method = PaymentMethod(
                payment_method_id=uuid4(),
                org_id=org_id,
                stripe_payment_method_id=pm.id,
                type=pm.type,
                card_brand=pm.card.brand if pm.card else "",
                card_last4=pm.card.last4 if pm.card else "",
                card_exp_month=pm.card.exp_month if pm.card else 0,
                card_exp_year=pm.card.exp_year if pm.card else 0,
                is_default=pm.id == default_pm_id,
                billing_details=pm.billing_details.to_dict() if pm.billing_details else {},
            )
            payment_methods.append(payment_method)

        return payment_methods

    async def set_default_payment_method(
        self,
        org_id: UUID,
        payment_method_id: str,
    ) -> None:
        """Set the default payment method."""
        org = await self._store.get_organization(org_id)
        if not org or not org.stripe_customer_id:
            raise ValueError("Organization has no Stripe customer")

        self._stripe.update_customer(
            org.stripe_customer_id,
            invoice_settings={"default_payment_method": payment_method_id},
        )

        log.info("Set default payment method for org %s", org_id)

    async def remove_payment_method(
        self,
        org_id: UUID,
        payment_method_id: str,
    ) -> None:
        """Remove a payment method."""
        self._stripe.detach_payment_method(payment_method_id)
        log.info("Removed payment method %s for org %s", payment_method_id, org_id)

    async def handle_webhook(
        self,
        payload: bytes,
        signature: str,
    ) -> WebhookResult:
        """Handle Stripe webhook events."""
        try:
            event = self._stripe.construct_webhook_event(
                payload, signature, self._webhook_secret
            )
        except ValueError as e:
            log.error("Invalid webhook payload: %s", e)
            raise ValueError("Invalid payload")
        except Exception as e:
            log.error("Invalid webhook signature: %s", e)
            raise ValueError("Invalid signature")

        event_type = event.type
        data = event.data.object

        log.info("Processing webhook event: %s", event_type)

        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "customer.subscription.created": self._handle_subscription_created,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_payment_failed,
            "customer.updated": self._handle_customer_updated,
        }

        handler = handlers.get(event_type)
        if handler:
            await handler(data)

        return WebhookResult(
            status="processed",
            event_type=event_type,
        )

    async def _handle_checkout_completed(self, data: Any) -> None:
        """Handle successful checkout."""
        org_id = UUID(data.metadata.organization_id)
        plan_name = data.metadata.plan_name
        log.info("Checkout completed for org %s, plan %s", org_id, plan_name)

    async def _handle_subscription_created(self, data: Any) -> None:
        """Handle new subscription."""
        org_id = UUID(data.metadata.organization_id)
        plan_name = data.metadata.plan_name

        plan = self._plan_manager.get_plan(plan_name)
        if not plan:
            log.error("Unknown plan in subscription created: %s", plan_name)
            return

        existing = await self._store.get_subscription(org_id)
        if existing and existing.is_active:
            existing.status = SubscriptionStatus.CANCELED
            existing.canceled_at = datetime.now(timezone.utc)
            await self._store.save_subscription(existing)

        billing_cycle = BillingCycle.ANNUAL
        if data.items.data:
            interval = data.items.data[0].price.recurring.interval
            if interval == "month":
                billing_cycle = BillingCycle.MONTHLY

        subscription = Subscription(
            subscription_id=uuid4(),
            org_id=org_id,
            plan_id=plan.plan_id,
            status=self._map_subscription_status(data.status),
            billing_cycle=billing_cycle,
            stripe_subscription_id=data.id,
            stripe_customer_id=data.customer,
            current_period_start=datetime.fromtimestamp(
                data.current_period_start, tz=timezone.utc
            ),
            current_period_end=datetime.fromtimestamp(
                data.current_period_end, tz=timezone.utc
            ),
            trial_start=datetime.fromtimestamp(data.trial_start, tz=timezone.utc)
            if data.trial_start
            else None,
            trial_end=datetime.fromtimestamp(data.trial_end, tz=timezone.utc)
            if data.trial_end
            else None,
        )

        await self._store.save_subscription(subscription)
        log.info("Created subscription for org %s (plan=%s)", org_id, plan_name)

    async def _handle_subscription_updated(self, data: Any) -> None:
        """Handle subscription update."""
        subscription = await self._store.get_subscription_by_stripe_id(data.id)
        if not subscription:
            log.warning("Subscription not found for update: %s", data.id)
            return

        subscription.status = self._map_subscription_status(data.status)
        subscription.current_period_start = datetime.fromtimestamp(
            data.current_period_start, tz=timezone.utc
        )
        subscription.current_period_end = datetime.fromtimestamp(
            data.current_period_end, tz=timezone.utc
        )
        subscription.cancel_at_period_end = data.cancel_at_period_end

        if data.canceled_at:
            subscription.canceled_at = datetime.fromtimestamp(
                data.canceled_at, tz=timezone.utc
            )

        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        log.info("Updated subscription %s", data.id)

    async def _handle_subscription_deleted(self, data: Any) -> None:
        """Handle subscription cancellation."""
        subscription = await self._store.get_subscription_by_stripe_id(data.id)
        if not subscription:
            log.warning("Subscription not found for deletion: %s", data.id)
            return

        subscription.status = SubscriptionStatus.CANCELED
        subscription.canceled_at = datetime.now(timezone.utc)
        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        log.info("Canceled subscription %s", data.id)

    async def _handle_invoice_paid(self, data: Any) -> None:
        """Handle successful payment."""
        invoice = Invoice(
            invoice_id=uuid4(),
            stripe_invoice_id=data.id,
            invoice_number=data.number or "",
            amount_cents=data.amount_paid,
            amount_paid_cents=data.amount_paid,
            currency=data.currency,
            status=InvoiceStatus.PAID,
            invoice_pdf_url=data.invoice_pdf or "",
            hosted_invoice_url=data.hosted_invoice_url or "",
            paid_at=datetime.now(timezone.utc),
        )

        await self._store.save_invoice(invoice)
        log.info("Invoice paid: %s", data.id)

    async def _handle_payment_failed(self, data: Any) -> None:
        """Handle failed payment."""
        subscription = await self._store.get_subscription_by_stripe_id(
            data.subscription
        )
        if subscription:
            subscription.status = SubscriptionStatus.PAST_DUE
            subscription.updated_at = datetime.now(timezone.utc)
            await self._store.save_subscription(subscription)

        log.warning("Payment failed for invoice %s", data.id)

    async def _handle_customer_updated(self, data: Any) -> None:
        """Handle customer update."""
        log.info("Customer updated: %s", data.id)

    def _map_subscription_status(self, stripe_status: str) -> SubscriptionStatus:
        """Map Stripe status to our status."""
        mapping = {
            "trialing": SubscriptionStatus.TRIALING,
            "active": SubscriptionStatus.ACTIVE,
            "past_due": SubscriptionStatus.PAST_DUE,
            "canceled": SubscriptionStatus.CANCELED,
            "unpaid": SubscriptionStatus.UNPAID,
            "incomplete": SubscriptionStatus.INCOMPLETE,
            "incomplete_expired": SubscriptionStatus.CANCELED,
            "paused": SubscriptionStatus.PAUSED,
        }
        return mapping.get(stripe_status, SubscriptionStatus.ACTIVE)

    def _map_invoice_status(self, stripe_status: str) -> InvoiceStatus:
        """Map Stripe invoice status to our status."""
        mapping = {
            "draft": InvoiceStatus.DRAFT,
            "open": InvoiceStatus.OPEN,
            "paid": InvoiceStatus.PAID,
            "void": InvoiceStatus.VOID,
            "uncollectible": InvoiceStatus.UNCOLLECTIBLE,
        }
        return mapping.get(stripe_status, InvoiceStatus.DRAFT)


# `verify_stripe_signature` used to live here, a second implementation of the
# Stripe-Signature check with no callers: `app.billing.__init__` exports the one
# in app/billing/webhooks.py, which is what POST /webhooks/stripe uses. This copy
# raised ValueError instead of returning False on a header Stripe did not send
# (`dict(item.split("=", 1) ...)` on 'garbage', `int()` on a non-numeric `t`), so
# whichever caller found it first would have turned a forged header into a 500.
