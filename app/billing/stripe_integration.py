"""Stripe billing integration service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict
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


class _BillingPeriod(TypedDict, total=False):
    """The period keywords ``Subscription`` accepts, each independently optional.

    A plain ``dict[str, datetime]`` would say only "some string keys", so
    unpacking it into ``Subscription(...)`` reads to a type checker as possibly
    supplying *any* field -- including ``cancel_at_period_end: bool`` and
    ``stripe_customer_id: str``. Naming the two keys is what makes the unpack
    checkable while still letting either one be absent.
    """

    current_period_start: datetime
    current_period_end: datetime


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


def configured_price_id(plan_name: str, cycle: BillingCycle) -> str:
    """The Stripe Price for a catalog plan and cycle, from STRIPE_PRICE_IDS.

    Prices live inside a Stripe account, so they cannot ship in the catalog —
    every `PricingPlan.stripe_monthly_price_id` / `stripe_annual_price_id` in
    app/billing/plans.py is the empty string, and nothing ever assigned one. That
    empty string reached the "No Stripe price configured" guard below, so
    checkout failed for every plan in every deployment, however Stripe was set
    up. The deployment supplies the mapping instead.
    """
    from app.config import get_settings

    key = f"{(plan_name or '').strip().lower()}:{cycle.value}"
    return get_settings().stripe_prices.get(key, "")


def plan_for_price_id(price_id: str) -> tuple[str, BillingCycle] | None:
    """The catalog plan and cycle a Stripe Price sells, or None if it sells none.

    The inverse of `configured_price_id`, over the same STRIPE_PRICE_IDS mapping,
    so a subscription can be read back to a tier without trusting metadata. That
    matters for changes made in Stripe's own customer portal: those rewrite the
    subscription item's Price and leave the metadata this product wrote at
    checkout untouched, so the Price is the only field that tells the truth about
    which tier the customer is on now.
    """
    wanted = (price_id or "").strip()
    if not wanted:
        return None
    from app.config import get_settings

    for key, value in get_settings().stripe_prices.items():
        if value != wanted:
            continue
        name, _, cycle = key.partition(":")
        try:
            return name, BillingCycle(cycle)
        except ValueError:
            log.warning(
                "STRIPE_PRICE_IDS entry %r names a billing cycle that does not "
                "exist; expected one of %s",
                key,
                ", ".join(c.value for c in BillingCycle),
            )
    return None


def tenancy_org_id_for(org_id: UUID) -> str:
    """The tenancy org id (`orgs.id`) whose billing rows live under `org_id`.

    Empty when there is no tenant on the request — a single-tenant deployment has
    no `orgs` row to move — or when the context belongs to a different tenant,
    which is the same guard `FeatureGateService._plan_from_tenant_context` uses to
    keep one tenant's plan from being reported for another.

    Read from the context rather than derived, because the derivation only runs
    one way: `org_uuid_for` hashes the tenant's string id into a UUID.
    """
    from app.database.orgs import org_uuid_for
    from app.tenant_context import get_tenant

    cfg = get_tenant()
    if cfg is None or cfg.org_id in ("", "-"):
        # Said out loud, because the resulting subscription is one the tenant-plan
        # receiver will answer "ignored" for ever after, and that reply is
        # indistinguishable from a bug. There is nothing to name here: a
        # single-tenant deployment has no `orgs` row, `set_plan` would match zero
        # rows for any id we invented, and metering is skipped entirely when no
        # tenant is on the request (app/metering.py). The subscription is still
        # attributable through `organization_id`, which is what the
        # `billing_subscriptions` handlers and the feature gate use.
        log.info(
            "No tenant on the request, so subscription metadata for billing org "
            "%s carries organization_id only; the tenant-plan receiver has no "
            "orgs row to move on this deployment and the billing store row is "
            "the record of what was bought",
            org_id,
        )
        return ""
    if org_uuid_for(cfg.org_id) != org_id:
        log.warning(
            "Tenant %s is on the request but the billing org is %s; leaving the "
            "tenancy org id off this subscription's metadata rather than "
            "labelling it with the wrong tenant",
            cfg.org_id,
            org_id,
        )
        return ""
    return cfg.org_id


def subscription_metadata(org_id: UUID, plan_name: str) -> dict[str, str]:
    """The metadata every subscription this product creates carries.

    Two consumers read it and they used to read different keys. The handlers on
    `StripeBillingService` read `organization_id`/`plan_name` and write the
    `billing_subscriptions` row; `plan_from_stripe_event` — which is what the
    mounted receiver `POST /webhooks/stripe` calls — reads `org_id`/`plan` and
    hands the result to `TenantStore.set_plan`. Checkout wrote only the first
    pair, so a completed, charged checkout produced an event the receiver
    answered `{"status": "ignored"}` and the customer stayed on the free plan.
    Writing both from one place is what keeps the producer and the two consumers
    on the same contract.

    `org_id` is the *tenancy* org id and not `str(org_id)`: the billing UUID is
    derived one-way from the tenancy id, so `set_plan` would match zero rows if
    it were sent that instead. It is omitted rather than faked when no tenant is
    on the request — a single-tenant deployment has no `orgs` row, so a synthetic
    value would turn the receiver's harmless "ignored" into a permanent 404 and
    an endless Stripe retry. `organization_id` is always present, and that is the
    key the `billing_subscriptions` handlers and `FeatureGateService` route on,
    so the tier is recorded and enforced on those deployments too. The receiver
    knows about the omission and reports it as expected rather than as a fault
    there (`_report_unroutable` in app/billing/webhooks.py); it used to log an
    ERROR for every delivery on a deployment where this is the designed answer.
    """
    meta = {
        "organization_id": str(org_id),
        "plan_name": plan_name,
        "plan": plan_name,
    }
    tenancy_org = tenancy_org_id_for(org_id)
    if tenancy_org:
        meta["org_id"] = tenancy_org
    return meta


def _event_metadata(data: Any) -> dict[str, Any]:
    """Metadata off a Stripe event object, whichever shape it arrives in.

    The SDK hands back a `StripeObject` (a dict subclass, so both attribute and
    key access work), but an event reconstructed from the raw JSON body is a
    plain dict and `data.metadata.organization_id` raises `AttributeError` on it.
    Reading it one way meant the handlers worked only for the shape the SDK
    happened to produce.
    """
    meta = getattr(data, "metadata", None)
    if meta is None and isinstance(data, dict):
        meta = data.get("metadata")
    if isinstance(meta, dict):
        return dict(meta)
    if meta is None:
        return {}
    return dict(vars(meta))


def _field(obj: Any, name: str) -> Any:
    """One field off a Stripe object, whichever shape it arrived in.

    `getattr` alone is wrong for the plain-dict shape a raw JSON body decodes to:
    `data.items` there is the dict's own method, not the subscription's line
    items, so the lookup would silently walk into `dict.items` instead of failing.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _epoch(value: Any) -> datetime | None:
    """A Stripe unix timestamp as an aware datetime, or None if it did not arrive.

    None rather than a substituted "now": these become the period and trial dates
    the customer is shown and the cancellation date support reads, and a date
    nothing sent is not a date that happened.
    """
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        log.warning(
            "Stripe sent %r where a unix timestamp was expected; the field was "
            "left unset rather than guessed",
            value,
        )
        return None


def subscription_price_id(data: Any) -> str:
    """The Stripe Price the subscription's first line item is on, if any.

    Public because the tenant-plan mapper in app/billing/webhooks.py needs the
    same answer this module's handlers do: the Price is what a change made in
    Stripe's own customer portal rewrites, and both records have to follow it or
    they disagree about which tier the customer is on.
    """
    rows = _field(_field(data, "items"), "data") or []
    if not rows:
        return ""
    return str(_field(_field(rows[0], "price"), "id") or "")


def _org_from_metadata(meta: dict[str, Any]) -> UUID | None:
    """The billing org an event belongs to, from either metadata spelling.

    `organization_id` is the billing UUID these tables are keyed by;
    `org_id` is the tenancy org id it is derived from, so a subscription that
    carries only the latter — the key the tenant-plan receiver routes on — can
    still be filed against the right org.
    """
    raw = str(meta.get("organization_id") or "").strip()
    if raw:
        try:
            return UUID(raw)
        except ValueError:
            log.error("Stripe metadata organization_id is not a UUID: %r", raw)
    tenancy_org = str(meta.get("org_id") or "").strip()
    if tenancy_org:
        from app.database.orgs import org_uuid_for

        return org_uuid_for(tenancy_org)
    return None


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

    async def ensure_organization(self, org_id: UUID) -> Organization:
        """The billing organization row, created on first use.

        `billing_organizations` has exactly one writer — this service — so the
        row does not exist until the org's first Stripe interaction. Checkout
        used to raise "Organization not found" for any org that had never checked
        out before, which is every org: the very first checkout could never
        succeed. Creating the row here makes the first one the one that creates
        it.
        """
        org = await self._store.get_organization(org_id)
        if org is not None:
            return org

        slug = str(org_id)
        org = Organization(org_id=org_id, name=slug, slug=slug)
        await self._store.save_organization(org)
        log.info("Created billing organization row for org %s", org_id)
        return org

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
        org = await self.ensure_organization(org_id)

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
        price_id = price_id or configured_price_id(plan_name, billing_cycle)

        if not price_id:
            raise ValueError(
                f"No Stripe price configured for {plan_name} {billing_cycle.value}"
            )

        # One metadata contract for the session and for the subscription it
        # creates: the subscription's copy is what Stripe echoes back on
        # `customer.subscription.*`, which is the only thing the receiver sees.
        metadata = subscription_metadata(org_id, plan_name)

        session_params: dict[str, Any] = {
            "customer": org.stripe_customer_id,
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url
            or f"{self._frontend_url}/settings/billing?success=true",
            "cancel_url": cancel_url
            or f"{self._frontend_url}/settings/billing?canceled=true",
            "metadata": {**metadata, "billing_cycle": billing_cycle.value},
            "subscription_data": {"metadata": dict(metadata)},
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
        org = await self.ensure_organization(org_id)

        if not org.stripe_customer_id:
            # There is nothing to manage until the org has bought something; the
            # portal is a view onto a Stripe customer, not a way to become one.
            raise ValueError(
                "This organization has no Stripe customer yet — start a "
                "subscription before opening the billing portal."
            )

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
        price_id = price_id or configured_price_id(new_plan_name, cycle)

        if not price_id:
            raise ValueError(f"No price for {new_plan_name} {cycle.value}")

        if subscription.stripe_subscription_id:
            stripe_sub = self._stripe.get_subscription(subscription.stripe_subscription_id)
            # `stripe_sub.items` is the one attribute read that cannot be spelled
            # with a dot: the SDK's StripeObject is a dict subclass, so plain
            # attribute lookup finds `dict.items` — the method — and never reaches
            # the subscription's line items at all. `_field` goes through the
            # mapping, which is also the shape a decoded JSON body has.
            rows = _field(_field(stripe_sub, "items"), "data") or []
            if not rows:
                raise ValueError(
                    f"Stripe subscription {subscription.stripe_subscription_id} has no "
                    "line items, so there is nothing to move onto the new price"
                )

            self._stripe.update_subscription(
                subscription.stripe_subscription_id,
                items=[{"id": _field(rows[0], "id"), "price": price_id}],
                proration_behavior="create_prorations" if prorate else "none",
                # Every key, not just `plan_name`: a Stripe metadata update
                # merges, so rewriting one half of the pair left the other
                # naming the plan the customer had just moved off, and the
                # `customer.subscription.updated` event Stripe sends back would
                # have re-applied the old tier.
                metadata=subscription_metadata(org_id, new_plan_name),
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
        """Handle Stripe webhook events.

        These handlers are the only thing that writes `billing_subscriptions`
        after a real payment. They are reached from `POST /webhooks/stripe`, via
        `BillingAPI.handle_webhook` and `_record_stripe_delivery` in
        app/api/main.py; for a long time nothing called them at all, so a paid
        subscription existed only in Stripe — `GET /billing/subscription` answered
        null for a customer who had paid and change/cancel/reactivate raised "No
        subscription for org".

        The raw body and the header are taken rather than a parsed event because
        the signature is verified here, against this service's own secret, before
        anything is written: the receiver's verification authorises the *tenant
        plan* half, and a store that trusts someone else's check has no way to
        tell a forged body from a signed one.
        """
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

        # Field by field rather than `event.type` / `event.data.object`: the SDK
        # returns a StripeObject, but a webhook secret is per-account and test
        # doubles (and any caller that hands back a decoded JSON body) produce
        # plain dicts, on which attribute access raises AttributeError — which
        # this receiver would report as a failed delivery for a perfectly good
        # event.
        event_type = str(_field(event, "type") or "")
        data = _field(_field(event, "data"), "object")

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

        # "processed" for an event nothing handled was an empty success: the
        # caller could not tell a subscription that had been written from a
        # delivery this service has no handler for.
        return WebhookResult(
            status="processed" if handler else "ignored",
            event_type=event_type,
        )

    async def _handle_checkout_completed(self, data: Any) -> None:
        """Handle successful checkout."""
        meta = _event_metadata(data)
        org_id = _org_from_metadata(meta)
        plan_name = meta.get("plan_name") or meta.get("plan") or ""
        if org_id is None:
            log.error(
                "Stripe checkout session %s completed but its metadata names no "
                "organization (keys: %s); the subscription it created cannot be "
                "matched to a customer",
                _field(data, "id") or "?",
                ", ".join(sorted(str(k) for k in meta)) or "none",
            )
            return
        log.info("Checkout completed for org %s, plan %s", org_id, plan_name)

    async def _handle_subscription_created(self, data: Any) -> None:
        """Handle new subscription."""
        meta = _event_metadata(data)
        org_id = _org_from_metadata(meta)
        plan_name = meta.get("plan_name") or meta.get("plan") or ""
        if org_id is None:
            log.error(
                "Stripe subscription %s was created but its metadata names no "
                "organization (keys: %s), so no subscription row was written and "
                "the customer who paid for it has no plan here",
                _field(data, "id") or "?",
                ", ".join(sorted(str(k) for k in meta)) or "none",
            )
            return

        # The Price the subscription is actually on outranks the metadata, for
        # the same reason it does on an update: a checkout completed from a
        # payment link, or a subscription created by hand in the dashboard,
        # carries whatever metadata (if any) somebody typed, while the Price is
        # what the customer is being charged for.
        priced = plan_for_price_id(subscription_price_id(data))
        plan = self._plan_manager.get_plan(priced[0] if priced else plan_name)
        if not plan:
            log.error(
                "Stripe subscription %s names plan %r (price %s), which this "
                "catalog does not sell, so no subscription row was written",
                _field(data, "id") or "?",
                priced[0] if priced else plan_name,
                subscription_price_id(data) or "none",
            )
            return

        existing = await self._store.get_subscription(org_id)
        if existing and existing.is_active:
            existing.status = SubscriptionStatus.CANCELED
            existing.canceled_at = datetime.now(timezone.utc)
            await self._store.save_subscription(existing)

        billing_cycle = self._billing_cycle_for(data, priced[1] if priced else None)

        # Only the period fields Stripe actually sent are set; the rest keep the
        # dataclass defaults. Writing a fabricated period would put a renewal
        # date in front of the customer that nothing measured.
        periods: _BillingPeriod = {}
        period_start = _epoch(_field(data, "current_period_start"))
        if period_start is not None:
            periods["current_period_start"] = period_start
        period_end = _epoch(_field(data, "current_period_end"))
        if period_end is not None:
            periods["current_period_end"] = period_end
        if len(periods) != 2:
            log.warning(
                "Stripe subscription %s arrived without a complete billing period; "
                "the row keeps the default window rather than a made-up one",
                _field(data, "id") or "?",
            )

        subscription = Subscription(
            subscription_id=uuid4(),
            org_id=org_id,
            plan_id=plan.plan_id,
            status=self._map_subscription_status(_field(data, "status")),
            billing_cycle=billing_cycle,
            stripe_subscription_id=str(_field(data, "id") or ""),
            stripe_customer_id=str(_field(data, "customer") or ""),
            trial_start=_epoch(_field(data, "trial_start")),
            trial_end=_epoch(_field(data, "trial_end")),
            **periods,
        )

        await self._store.save_subscription(subscription)
        log.info("Created subscription for org %s (plan=%s)", org_id, plan.name)

    @staticmethod
    def _billing_cycle_for(data: Any, priced_cycle: BillingCycle | None) -> BillingCycle:
        """The cycle a subscription is billed on, from its first line item.

        This defaulted to ANNUAL and read `data.items.data[0]...` unguarded. On a
        plain dict `data.items` is `dict.items` — the method — so the walk landed
        somewhere unrelated rather than failing, and any subscription whose item
        shape did not match was silently recorded as annual: a monthly customer
        whose row says annual is invoiced against the wrong period everywhere the
        row is read. The STRIPE_PRICE_IDS mapping is the fallback, because it
        knows which cycle each Price sells.
        """
        rows = _field(_field(data, "items"), "data") or []
        if isinstance(rows, list) and rows:
            interval = _field(_field(_field(rows[0], "price"), "recurring"), "interval")
            if interval == "month":
                return BillingCycle.MONTHLY
            if interval == "year":
                return BillingCycle.ANNUAL
        if priced_cycle is not None:
            return priced_cycle
        log.warning(
            "Stripe subscription %s carries no billing interval; recording it as "
            "monthly, the shorter of the two, so a renewal is never assumed to be "
            "further away than it is",
            _field(data, "id") or "?",
        )
        return BillingCycle.MONTHLY

    async def _handle_subscription_updated(self, data: Any) -> None:
        """Handle subscription update, including a change of tier.

        A plan change made in Stripe's own customer portal — which is what
        `POST /billing/portal` sends customers to — arrives here and nowhere
        else, and it rewrites the subscription item's Price while leaving the
        metadata written at checkout naming the tier they moved *off*. This used
        to copy across the dates and the cancel flag only, so the local row kept
        the old `plan_id` forever: the record every feature gate resolves the
        customer's tier from said Team while Stripe was billing them for Growth.
        """
        stripe_id = str(_field(data, "id") or "")
        subscription = await self._store.get_subscription_by_stripe_id(stripe_id)
        if not subscription:
            log.warning("Subscription not found for update: %s", stripe_id or "?")
            return

        self._apply_price_change(subscription, data)
        subscription.status = self._map_subscription_status(_field(data, "status"))
        # Each date only if it arrived: overwriting a stored period with `None`
        # would erase the renewal date the customer is shown.
        period_start = _epoch(_field(data, "current_period_start"))
        period_end = _epoch(_field(data, "current_period_end"))
        if period_start is not None:
            subscription.current_period_start = period_start
        if period_end is not None:
            subscription.current_period_end = period_end
        subscription.cancel_at_period_end = bool(_field(data, "cancel_at_period_end"))

        canceled_at = _epoch(_field(data, "canceled_at"))
        if canceled_at is not None:
            subscription.canceled_at = canceled_at

        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        log.info("Updated subscription %s", stripe_id or "?")

    def _apply_price_change(self, subscription: Subscription, data: Any) -> None:
        """Move a stored subscription onto the tier its Stripe Price now sells.

        The Price is trusted over the metadata because only one of the two is
        rewritten when the change is made outside this product. A Price this
        deployment does not sell is reported rather than guessed at: leaving the
        tier alone is recoverable, while silently picking one is a billing
        decision made on no evidence.
        """
        price_id = subscription_price_id(data)
        if not price_id:
            return
        priced = plan_for_price_id(price_id)
        if priced is None:
            log.warning(
                "Stripe subscription %s is on price %s, which no STRIPE_PRICE_IDS "
                "entry maps to a plan; leaving its tier as it was",
                _field(data, "id") or "?",
                price_id,
            )
            return
        plan_name, cycle = priced
        plan = self._plan_manager.get_plan(plan_name)
        if plan is None:
            log.error(
                "STRIPE_PRICE_IDS maps price %s to plan %r, which this catalog "
                "does not contain; the tier of subscription %s was left as it was",
                price_id,
                plan_name,
                _field(data, "id") or "?",
            )
            return
        if plan.plan_id != subscription.plan_id or cycle != subscription.billing_cycle:
            log.info(
                "Subscription %s moved to %s (%s) in Stripe",
                _field(data, "id") or "?",
                plan_name,
                cycle.value,
            )
        subscription.plan_id = plan.plan_id
        subscription.billing_cycle = cycle

    async def _handle_subscription_deleted(self, data: Any) -> None:
        """Handle subscription cancellation."""
        stripe_id = str(_field(data, "id") or "")
        subscription = await self._store.get_subscription_by_stripe_id(stripe_id)
        if not subscription:
            log.warning("Subscription not found for deletion: %s", stripe_id or "?")
            return

        subscription.status = SubscriptionStatus.CANCELED
        subscription.canceled_at = datetime.now(timezone.utc)
        subscription.updated_at = datetime.now(timezone.utc)
        await self._store.save_subscription(subscription)

        log.info("Canceled subscription %s", stripe_id or "?")

    async def _handle_invoice_paid(self, data: Any) -> None:
        """Handle successful payment.

        The org is looked up rather than left to `Invoice.org_id`'s default,
        which is `uuid4()`: every paid invoice was filed under a fresh random
        org, and `save_invoice` calls `ensure_org`, so each payment also minted
        an `organizations` row that belonged to nobody. The customer's invoice
        list — keyed by their own org — stayed empty.
        """
        invoice_id = str(_field(data, "id") or "?")
        stripe_subscription_id = str(_field(data, "subscription") or "")
        subscription = (
            await self._store.get_subscription_by_stripe_id(stripe_subscription_id)
            if stripe_subscription_id
            else None
        )
        org_id = (
            subscription.org_id if subscription else _org_from_metadata(_event_metadata(data))
        )
        if org_id is None:
            # Storing it under an invented org would make the row unreachable
            # while looking like a success. Say which invoice was dropped and
            # why instead.
            log.error(
                "Stripe invoice %s was paid but belongs to no organization we "
                "know (subscription=%r); it was not stored",
                invoice_id,
                stripe_subscription_id or None,
            )
            return

        amount_paid = _field(data, "amount_paid")
        if amount_paid is None:
            # An invoice row is a record of money that moved. Defaulting the
            # amount to 0 would put "paid $0.00" in the customer's invoice list
            # and in every total computed from it.
            log.error(
                "Stripe invoice %s carries no amount_paid, so what was charged is "
                "unknown; it was not stored rather than stored as zero",
                invoice_id,
            )
            return

        invoice = Invoice(
            invoice_id=uuid4(),
            org_id=org_id,
            subscription_id=subscription.subscription_id if subscription else None,
            stripe_invoice_id=invoice_id,
            invoice_number=str(_field(data, "number") or ""),
            amount_cents=int(amount_paid),
            amount_paid_cents=int(amount_paid),
            currency=str(_field(data, "currency") or ""),
            status=InvoiceStatus.PAID,
            invoice_pdf_url=str(_field(data, "invoice_pdf") or ""),
            hosted_invoice_url=str(_field(data, "hosted_invoice_url") or ""),
            paid_at=datetime.now(timezone.utc),
        )

        await self._store.save_invoice(invoice)
        log.info("Invoice paid: %s", invoice_id)

    async def _handle_payment_failed(self, data: Any) -> None:
        """Handle failed payment."""
        stripe_subscription_id = str(_field(data, "subscription") or "")
        subscription = (
            await self._store.get_subscription_by_stripe_id(stripe_subscription_id)
            if stripe_subscription_id
            else None
        )
        if subscription:
            subscription.status = SubscriptionStatus.PAST_DUE
            subscription.updated_at = datetime.now(timezone.utc)
            await self._store.save_subscription(subscription)
        else:
            # Past-due is what suspends access, so a failed payment that matched
            # no local subscription must not pass silently: the customer keeps
            # full service while Stripe believes they are in dunning.
            log.error(
                "Stripe reported a failed payment on invoice %s for subscription "
                "%r, which matches no row here; no subscription was marked past due",
                _field(data, "id") or "?",
                stripe_subscription_id or None,
            )

        log.warning("Payment failed for invoice %s", _field(data, "id") or "?")

    async def _handle_customer_updated(self, data: Any) -> None:
        """Handle customer update."""
        log.info("Customer updated: %s", _field(data, "id") or "?")

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
