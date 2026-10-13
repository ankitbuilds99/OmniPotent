"""The Stripe seam: what checkout writes must be what the receivers read.

Three defects met here, and all three were invisible because every one of them
answered 200/"processed":

1. `create_checkout_session` wrote `metadata={organization_id, plan_name}` while
   `plan_from_stripe_event` — the only mounted receiver's mapper — read
   `org_id`/`plan`. A charged customer's `customer.subscription.created` was
   answered `{"status": "ignored"}` and their `orgs.plan` stayed `free`.
2. The metadata org id was `str(billing_uuid)`, which is `uuid5(ns, tenancy_id)`
   — a one-way derivation — so even the renamed key would have matched zero rows
   in `orgs`.
3. `_handle_invoice_paid` built an `Invoice` with no `org_id`, and the field
   defaults to `uuid4()`, so every paid invoice was filed under a random org.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.config as cfg
from app.billing.feature_gate import FeatureGateService
from app.billing.models import (
    BillingCycle,
    Invoice,
    Organization,
    PricingPlan,
    Subscription,
    SubscriptionStatus,
)
from app.billing.plans import PRICING_PLANS, catalog_plan_for, plan_uuid
from app.billing.stripe_integration import (
    StripeBillingService,
    plan_for_price_id,
    subscription_metadata,
)
from app.billing.webhooks import plan_from_stripe_event
from app.database.orgs import org_uuid_for
from app.tenant_context import TenantConfig, reset_tenant, set_tenant

TENANT = "acme"
ORG = org_uuid_for(TENANT)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeStore:
    """Everything StripeBillingService and FeatureGateService touch here."""

    def __init__(self) -> None:
        self.orgs: dict[UUID, Organization] = {}
        self.subscriptions: dict[UUID, Subscription] = {}
        self.invoices: list[Invoice] = []

    async def get_organization(self, org_id: UUID) -> Organization | None:
        return self.orgs.get(org_id)

    async def save_organization(self, org: Organization) -> None:
        self.orgs[org.org_id] = org

    async def get_subscription(self, org_id: UUID) -> Subscription | None:
        return self.subscriptions.get(org_id)

    async def get_subscription_by_stripe_id(self, stripe_id: str) -> Subscription | None:
        for sub in self.subscriptions.values():
            if sub.stripe_subscription_id == stripe_id:
                return sub
        return None

    async def save_subscription(self, sub: Subscription) -> None:
        self.subscriptions[sub.org_id] = sub

    async def save_invoice(self, invoice: Invoice) -> None:
        self.invoices.append(invoice)

    async def get_plan(self, plan_id: UUID) -> PricingPlan | None:
        return next((p for p in PRICING_PLANS.values() if p.plan_id == plan_id), None)

    async def get_plan_by_name(self, name: str) -> PricingPlan | None:
        return PRICING_PLANS.get(name)

    async def get_feature_overrides(self, org_id: UUID) -> list:
        return []

    async def get_usage_summary(self, org_id: UUID, year_month: str):
        return None


class FakeStripe:
    """Records what the service asked Stripe to do, and replays events back."""

    def __init__(self) -> None:
        self.checkout_calls: list[dict[str, Any]] = []
        self.subscription_updates: list[tuple[str, dict[str, Any]]] = []
        self.event: Any = None

    def create_customer(self, **kwargs: Any) -> Any:
        return _Obj(id="cus_test_1")

    def create_checkout_session(self, **kwargs: Any) -> Any:
        self.checkout_calls.append(kwargs)
        return _Obj(id="cs_test_1", url="https://checkout.stripe.test/cs_test_1")

    def get_subscription(self, subscription_id: str) -> Any:
        return _Obj(items=_Obj(data=[_Obj(id="si_1")]))

    def update_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        self.subscription_updates.append((subscription_id, kwargs))
        return _Obj(id=subscription_id)

    def construct_webhook_event(self, payload: bytes, signature: str, secret: str) -> Any:
        return self.event


class _Obj:
    """A Stripe-shaped object: attributes, and `metadata` left as a plain dict.

    That is the shape an event reconstructed from the raw JSON body has, and the
    handlers used to reach into it with `data.metadata.organization_id`.
    """

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def __getattr__(self, name: str) -> Any:  # absent optional fields
        return None


@pytest.fixture
def tenant():
    token = set_tenant(TenantConfig(org_id=TENANT, plan="free", role="owner"))
    yield
    reset_tenant(token)


@pytest.fixture
def multi_tenant(monkeypatch):
    """A deployment with an `orgs` table, so a tenancy org id is routable."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture
def single_tenant(monkeypatch):
    """A deployment with no `orgs` table, stated rather than inherited.

    `False` is the default, so a test could read it by saying nothing — and then
    it is really asserting whatever the shell that started pytest happened to
    export, which is how `COPILOT_MULTI_TENANT=true pytest` turned this file red
    while a bare run stayed green. The value is written explicitly (not just
    unset) because `Settings` also reads a `.env`, and only a real environment
    variable outranks that.
    """
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setenv(
        "STRIPE_PRICE_IDS",
        "growth:monthly=price_growth_m,business:monthly=price_business_m,team:monthly=price_team_m",
    )
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture
def service() -> tuple[StripeBillingService, FakeStore, FakeStripe]:
    store, stripe = FakeStore(), FakeStripe()
    svc = StripeBillingService(
        store=store,
        stripe_client=stripe,
        webhook_secret="whsec_test",
        frontend_url="https://app.test",
    )
    return svc, store, stripe


def _subscription_event(etype: str, metadata: dict[str, Any]) -> _Obj:
    now = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    return _Obj(
        type=etype,
        data=_Obj(
            object=_Obj(
                id="sub_test_1",
                customer="cus_test_1",
                status="active",
                metadata=metadata,
                items=_Obj(data=[_Obj(price=_Obj(recurring=_Obj(interval="month")))]),
                current_period_start=now,
                current_period_end=now + 30 * 86400,
                cancel_at_period_end=False,
            )
        ),
    )


# --------------------------------------------------------------------------- #
# 1. Checkout and the tenant-plan receiver speak the same metadata
# --------------------------------------------------------------------------- #
class TestCheckoutMetadataContract:
    async def test_the_event_our_own_checkout_produces_maps_to_a_plan(
        self, service, prices, tenant
    ):
        """The round trip the product actually performs. It resolved to None.

        The plan it resolves to is the one the customer bought: this is the value
        `set_plan` writes to `orgs.plan`, which is the only place a
        webhook-upgraded deployment records the tier, so answering "team" for a
        Growth checkout would lose it before anything could read it back.
        """
        svc, _, stripe = service

        await svc.create_checkout_session(
            ORG, "growth", BillingCycle.MONTHLY, email="a@b.test"
        )
        metadata = stripe.checkout_calls[0]["subscription_data"]["metadata"]

        mapping = plan_from_stripe_event(
            {"type": "customer.subscription.created",
             "data": {"object": {"id": "sub_1", "metadata": metadata}}}
        )

        assert mapping == (TENANT, "growth"), "a paid Growth checkout applied no plan"

    async def test_the_metadata_org_id_is_the_one_set_plan_matches(
        self, service, prices, tenant
    ):
        """`organization_id` is the derived billing UUID; `orgs.id` is the tenant
        string it was derived from, and the derivation has no inverse."""
        svc, _, stripe = service

        await svc.create_checkout_session(
            ORG, "growth", BillingCycle.MONTHLY, email="a@b.test"
        )
        metadata = stripe.checkout_calls[0]["subscription_data"]["metadata"]

        assert metadata["org_id"] == TENANT
        assert metadata["organization_id"] == str(ORG)
        assert metadata["org_id"] != metadata["organization_id"]

    async def test_the_session_and_the_subscription_carry_the_same_contract(
        self, service, prices, tenant
    ):
        """Stripe echoes the *subscription's* metadata on subscription events, so
        writing the pair on only one of the two would leave the receiver blind."""
        svc, _, stripe = service

        await svc.create_checkout_session(
            ORG, "business", BillingCycle.MONTHLY, email="a@b.test"
        )
        call = stripe.checkout_calls[0]

        for meta in (call["metadata"], call["subscription_data"]["metadata"]):
            assert meta["org_id"] == TENANT
            assert meta["plan"] == "business"
            assert meta["plan_name"] == "business"

    def test_a_subscription_written_before_the_contract_is_still_read(self):
        """`plan_name` alone is what older subscriptions in a live Stripe account
        carry; the receiver accepts it rather than ignoring the upgrade."""
        mapping = plan_from_stripe_event(
            {"type": "customer.subscription.updated",
             "data": {"object": {"metadata": {"org_id": "o1", "plan_name": "scale"}}}}
        )
        assert mapping == ("o1", "enterprise")

    def test_an_unroutable_subscription_event_says_why(self, caplog, multi_tenant):
        """It returned None, which the receiver reports as `ignored` — the same
        answer as for an event type we do not handle.

        Multi-tenant, because that is the deployment where this is a fault: there
        is an `orgs` row that should have been moved and the metadata does not
        say which."""
        with caplog.at_level(logging.ERROR, logger="omnipotent.billing.webhooks"):
            mapping = plan_from_stripe_event(
                {"type": "customer.subscription.created",
                 "data": {"object": {"id": "sub_9",
                                     "metadata": {"organization_id": str(ORG)}}}}
            )

        assert mapping is None
        assert "sub_9" in caplog.text and "organization_id" in caplog.text
        webhook_records = [
            r for r in caplog.records if r.name == "omnipotent.billing.webhooks"
        ]
        assert [r.levelno for r in webhook_records] == [logging.ERROR]

    def test_the_same_event_on_a_single_tenant_deployment_is_not_an_error(
        self, caplog, single_tenant
    ):
        """`subscription_metadata` leaves `org_id` off when no tenant is on the
        request, on purpose — there is no `orgs` row, and an invented id would
        make `set_plan` match zero rows and turn the receiver's harmless
        "ignored" into a permanent 404 and an endless Stripe retry.

        So on a single-tenant deployment this is the shape of *every* delivery,
        and logging an error for each one buried the deliveries that really are
        broken. The subscription is not lost: `organization_id` is always written
        and it is the key `billing_subscriptions` and `FeatureGateService` route
        on there.
        """
        with caplog.at_level(logging.DEBUG, logger="omnipotent.billing.webhooks"):
            mapping = plan_from_stripe_event(
                {"type": "customer.subscription.created",
                 "data": {"object": {"id": "sub_9",
                                     "metadata": {"organization_id": str(ORG)}}}}
            )

        assert mapping is None
        webhook_records = [
            r for r in caplog.records if r.name == "omnipotent.billing.webhooks"
        ]
        assert [r.levelno for r in webhook_records] == [logging.INFO]
        assert "sub_9" in caplog.text and "single-tenant" in caplog.text


class TestPlanChangeMetadata:
    async def test_a_plan_change_rewrites_both_halves_of_the_pair(self, service, prices):
        """Stripe merges metadata updates, so rewriting `plan_name` alone left
        `plan` naming the tier the customer had just left — and that stale key is
        what the `customer.subscription.updated` event would have re-applied."""
        svc, store, stripe = service
        store.subscriptions[ORG] = Subscription(
            org_id=ORG,
            plan_id=plan_uuid("team"),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_1",
        )

        await svc.change_plan(ORG, "growth", BillingCycle.MONTHLY)

        _, kwargs = stripe.subscription_updates[0]
        assert kwargs["metadata"]["plan"] == "growth"
        assert kwargs["metadata"]["plan_name"] == "growth"


class TestPortalPlanChange:
    """An upgrade bought in Stripe's own customer portal.

    `POST /billing/portal` sends customers there deliberately, and a tier change
    made in it reaches this product only as `customer.subscription.updated`. The
    metadata still names the tier they moved off — Stripe does not rewrite it —
    so the subscription item's Price is the only field that says what they are
    paying for now.
    """

    def _updated_event(self, price_id: str, stale_plan: str = "team") -> _Obj:
        now = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
        return _Obj(
            type="customer.subscription.updated",
            data=_Obj(object=_Obj(
                id="sub_test_1",
                status="active",
                metadata={"org_id": TENANT, "plan": stale_plan, "plan_name": stale_plan},
                items=_Obj(data=[_Obj(id="si_1", price=_Obj(
                    id=price_id, recurring=_Obj(interval="month")))]),
                current_period_start=now,
                current_period_end=now + 30 * 86400,
                cancel_at_period_end=False,
            )),
        )

    def _seed(self, store) -> Subscription:
        sub = Subscription(
            org_id=ORG,
            plan_id=plan_uuid("team"),
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=BillingCycle.ANNUAL,
            stripe_subscription_id="sub_test_1",
        )
        store.subscriptions[ORG] = sub
        return sub

    async def test_an_upgrade_bought_in_the_portal_moves_the_stored_tier(
        self, service, prices
    ):
        """The handler copied the dates across and left `plan_id` alone, so the
        row the feature gate reads still said Team after the customer had been
        moved to — and billed for — Growth."""
        svc, store, stripe = service
        self._seed(store)
        stripe.event = self._updated_event("price_growth_m")

        await svc.handle_webhook(b"{}", "sig")

        assert store.subscriptions[ORG].plan_id == plan_uuid("growth")
        assert store.subscriptions[ORG].billing_cycle is BillingCycle.MONTHLY

    async def test_a_price_this_deployment_does_not_sell_is_reported(
        self, service, prices, caplog
    ):
        """Guessing a tier from a price nothing maps would be a billing decision
        made on no evidence; the stored tier stands and the price is named."""
        svc, store, stripe = service
        self._seed(store)
        stripe.event = self._updated_event("price_from_another_account")

        with caplog.at_level(logging.WARNING, logger="omnipotent.billing.stripe"):
            await svc.handle_webhook(b"{}", "sig")

        assert store.subscriptions[ORG].plan_id == plan_uuid("team")
        assert "price_from_another_account" in caplog.text

    def test_a_configured_price_resolves_back_to_the_plan_that_sells_it(self, prices):
        assert plan_for_price_id("price_business_m") == ("business", BillingCycle.MONTHLY)
        assert plan_for_price_id("price_nobody_configured") is None
        assert plan_for_price_id("") is None


# --------------------------------------------------------------------------- #
# 2. The billing-store handlers
# --------------------------------------------------------------------------- #
class TestSubscriptionHandlers:
    async def test_the_checkout_metadata_creates_the_subscription_row(
        self, service, prices, tenant
    ):
        """End to end: what checkout emits, through handle_webhook, into the row
        the feature gate reads — and the tier that comes back is the tier bought,
        not the one the three-value quota vocabulary can represent."""
        svc, store, stripe = service

        await svc.create_checkout_session(
            ORG, "growth", BillingCycle.MONTHLY, email="a@b.test"
        )
        metadata = stripe.checkout_calls[0]["subscription_data"]["metadata"]
        stripe.event = _subscription_event("customer.subscription.created", metadata)

        result = await svc.handle_webhook(b"{}", "sig")

        assert result.status == "processed"
        saved = store.subscriptions[ORG]
        assert saved.plan_id == plan_uuid("growth")

        gate = FeatureGateService(store)
        plan = await gate.get_organization_plan(ORG)
        assert plan is not None and plan.name == "growth"
        assert (await gate.can_access_feature(ORG, "service_dependency_map")).allowed

    async def test_a_subscription_with_no_org_is_reported_not_guessed(
        self, service, caplog
    ):
        svc, store, _ = service
        svc._stripe.event = _subscription_event(
            "customer.subscription.created", {"plan_name": "growth"}
        )

        with caplog.at_level(logging.ERROR, logger="omnipotent.billing.stripe"):
            await svc.handle_webhook(b"{}", "sig")

        assert store.subscriptions == {}
        assert "names no organization" in caplog.text

    async def test_an_event_with_no_handler_is_not_reported_as_processed(self, service):
        svc, _, stripe = service
        stripe.event = _Obj(type="payment_intent.created", data=_Obj(object=_Obj(id="pi_1")))

        result = await svc.handle_webhook(b"{}", "sig")

        assert result.status == "ignored"
        assert result.event_type == "payment_intent.created"


class TestInvoiceHandler:
    async def test_a_paid_invoice_is_filed_under_the_org_that_paid(self, service):
        """`Invoice.org_id` defaults to `uuid4()`, so an invoice built without one
        was stored against a random org — and `save_invoice` calls `ensure_org`,
        minting an `organizations` row for it."""
        svc, store, stripe = service
        subscription = Subscription(
            org_id=ORG,
            plan_id=plan_uuid("growth"),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test_1",
        )
        store.subscriptions[ORG] = subscription
        stripe.event = _Obj(
            type="invoice.paid",
            data=_Obj(object=_Obj(
                id="in_1", subscription="sub_test_1", number="A-1",
                amount_paid=69900, currency="usd",
                invoice_pdf="", hosted_invoice_url="", metadata={},
            )),
        )

        await svc.handle_webhook(b"{}", "sig")

        assert len(store.invoices) == 1
        assert store.invoices[0].org_id == ORG
        assert store.invoices[0].subscription_id == subscription.subscription_id

    async def test_an_invoice_belonging_to_nobody_is_dropped_loudly(
        self, service, caplog
    ):
        svc, store, stripe = service
        stripe.event = _Obj(
            type="invoice.paid",
            data=_Obj(object=_Obj(
                id="in_orphan", subscription="sub_unknown", number="", amount_paid=100,
                currency="usd", invoice_pdf="", hosted_invoice_url="", metadata={},
            )),
        )

        with caplog.at_level(logging.ERROR, logger="omnipotent.billing.stripe"):
            await svc.handle_webhook(b"{}", "sig")

        assert store.invoices == []
        assert "in_orphan" in caplog.text


# --------------------------------------------------------------------------- #
# 3. The entitlement resolver
# --------------------------------------------------------------------------- #
class TestCatalogTierResolution:
    @pytest.mark.parametrize("tier", ["growth", "business", "scale"])
    def test_a_tier_the_customer_bought_resolves_to_itself(self, tier):
        """This ran every value through the quota→display map, whose default is
        Starter, so the tier a paying customer actually holds answered "starter"
        and every gated feature was withdrawn at once."""
        assert catalog_plan_for(tier).name == tier

    def test_the_enforced_vocabulary_still_resolves(self):
        assert catalog_plan_for("free").name == "starter"
        assert catalog_plan_for("team").name == "team"
        assert catalog_plan_for("enterprise").name == "enterprise"

    def test_a_value_nobody_recognises_is_reported(self, caplog):
        """Free-tier entitlements are the safe answer, but a stored plan nothing
        understands must not look like a customer who never paid."""
        with caplog.at_level(logging.WARNING, logger="omnipotent.billing.plans"):
            assert catalog_plan_for("pro_monthly").name == "starter"
        assert "pro_monthly" in caplog.text


class TestMetadataHelper:
    def test_no_tenant_on_the_request_means_no_invented_org_id(self):
        """Single-tenant deployments have no `orgs` row; the key is left out
        rather than filled with a value `set_plan` would never match."""
        meta = subscription_metadata(uuid4(), "team")
        assert "org_id" not in meta
        assert meta["plan"] == "team"

    def test_a_mismatched_tenant_is_not_labelled_with_the_wrong_org(self, tenant):
        meta = subscription_metadata(org_uuid_for("someone-else"), "team")
        assert "org_id" not in meta
