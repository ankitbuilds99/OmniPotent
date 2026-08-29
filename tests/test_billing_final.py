"""The last four billing defects, each pinned by what a customer would notice.

All four share a shape: something answered successfully while the customer lost
what they had paid for.

1. A tier change bought in Stripe's own customer portal rewrites the
   subscription item's Price and leaves the metadata naming the tier the
   customer moved off. `plan_from_stripe_event` read the metadata, so `orgs.plan`
   — the quota bracket every meter reads — stayed on the old tier while
   `billing_subscriptions` (which follows the Price) moved. The upgrade arrived
   as features and not as quota.
2. `get_billing_api` memoised whatever `get_stripe_service()` answered on the
   first call. One early call before STRIPE_API_KEY was readable pinned a
   Stripe-less `BillingAPI` for the life of the process, and every Stripe
   delivery after that reported `billing_store: not_configured` on a deployment
   that was configured.
3. `change_plan` reached the subscription's line items with `stripe_sub.items`.
   The SDK's objects are `dict` subclasses, so that lookup finds `dict.items` —
   the bound method — and never the line items.
4. A subscription event that carries no tenancy org id is a fault on a
   multi-tenant deployment and the designed answer on a single-tenant one, where
   there is no `orgs` row to move at all. Both logged ERROR, so the deployment
   where it means something drowned in the one where it does not.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import pytest

import app.config as cfg
from app.billing import runtime as billing_runtime
from app.billing.api import BillingUnavailableError
from app.billing.models import (
    BillingCycle,
    Subscription,
    SubscriptionStatus,
)
from app.billing.plans import plan_uuid
from app.billing.stripe_integration import StripeBillingService, subscription_price_id
from app.billing.webhooks import plan_from_stripe_event
from app.database.orgs import org_uuid_for

TENANT = "acme"
ORG = org_uuid_for(TENANT)
PRICES = "growth:monthly=price_growth_m,team:monthly=price_team_m,starter:monthly=price_starter_m"


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_IDS", PRICES)
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


def _subscription_event(
    price_id: str | None,
    stated_plan: str = "team",
    etype: str = "customer.subscription.updated",
) -> dict:
    """A delivery as the receiver sees it: a plain dict decoded from the body."""
    obj: dict[str, Any] = {
        "id": "sub_test_1",
        "status": "active",
        "metadata": {"org_id": TENANT, "plan": stated_plan, "plan_name": stated_plan},
    }
    if price_id is not None:
        obj["items"] = {
            "data": [{"id": "si_1", "price": {"id": price_id, "recurring": {"interval": "month"}}}]
        }
    return {"type": etype, "data": {"object": obj}}


# --------------------------------------------------------------------------- #
# 1. A portal upgrade moves the quota, not just the features
# --------------------------------------------------------------------------- #
class TestThePriceOutranksStaleMetadata:
    def test_a_portal_upgrade_applies_the_tier_the_customer_is_billed_for(self, prices):
        """Stripe does not rewrite metadata when the customer changes plan in the
        portal, so the Price is the only field that says what they pay for now.

        Read from the metadata alone this returned "team" — the tier they left —
        and `set_plan` wrote it, so a Growth customer stayed capped at Team's
        1000 investigations a month while their subscription row and every
        feature gate said Growth.
        """
        mapping = plan_from_stripe_event(_subscription_event("price_growth_m", "team"))

        assert mapping == (TENANT, "growth")

    def test_a_downgrade_is_followed_too(self, prices):
        """The same precedence in the other direction. Trusting stale metadata
        here would keep serving a tier nobody is paying for any more."""
        mapping = plan_from_stripe_event(_subscription_event("price_team_m", "growth"))

        assert mapping == (TENANT, "team")

    def test_the_price_is_translated_into_the_enforcement_vocabulary(self, prices):
        """`orgs.plan` holds a quota plan; STRIPE_PRICE_IDS names catalog tiers.
        Starter is spelled `free` on this side, and the translation happens here
        rather than at the call site."""
        mapping = plan_from_stripe_event(_subscription_event("price_starter_m", "team"))

        assert mapping == (TENANT, "free")

    def test_a_created_event_follows_its_price_as_well(self, prices):
        """A checkout completed from a payment link, or a subscription created by
        hand in the dashboard, carries whatever metadata somebody typed."""
        mapping = plan_from_stripe_event(
            _subscription_event("price_growth_m", "team", "customer.subscription.created")
        )

        assert mapping == (TENANT, "growth")

    def test_a_price_nothing_maps_falls_back_to_the_metadata(self, prices):
        """A deployment can sell through Stripe without configuring
        STRIPE_PRICE_IDS at all, and the metadata is then the only evidence
        there is. Guessing a tier from an unmapped Price would be a billing
        decision made on nothing."""
        mapping = plan_from_stripe_event(
            _subscription_event("price_from_another_account", "growth")
        )

        assert mapping == (TENANT, "growth")

    def test_a_subscription_with_no_line_items_falls_back_to_the_metadata(self, prices):
        mapping = plan_from_stripe_event(_subscription_event(None, "growth"))

        assert mapping == (TENANT, "growth")

    def test_a_price_mapped_to_a_tier_we_do_not_sell_is_reported_not_applied(
        self, monkeypatch, caplog
    ):
        """A typo in STRIPE_PRICE_IDS must not reject every delivery for that
        price, nor silently invent a tier: it is named and the metadata stands."""
        monkeypatch.setenv("STRIPE_PRICE_IDS", "platinum:monthly=price_growth_m")
        cfg.get_settings.cache_clear()
        try:
            with caplog.at_level(logging.WARNING, logger="omnipotent.billing.webhooks"):
                mapping = plan_from_stripe_event(_subscription_event("price_growth_m", "team"))
        finally:
            cfg.get_settings.cache_clear()

        assert mapping == (TENANT, "team")
        assert "platinum" in caplog.text and "price_growth_m" in caplog.text

    def test_a_cancellation_ignores_the_price_entirely(self, prices):
        """`customer.subscription.deleted` arrives with the Price still on it;
        what it means is that nobody is paying for it any more."""
        mapping = plan_from_stripe_event(
            _subscription_event("price_growth_m", "growth", "customer.subscription.deleted")
        )

        assert mapping == (TENANT, "free")


# --------------------------------------------------------------------------- #
# 2. The BillingAPI singleton picks Stripe up when it becomes readable
# --------------------------------------------------------------------------- #
class _FakeStripeAdapter:
    """Enough of the StripeClient protocol for one unhandled webhook event."""

    def construct_webhook_event(self, payload: bytes, signature: str, secret: str) -> Any:
        return {"type": "payment_intent.created", "data": {"object": {"id": "pi_1"}}}


@pytest.fixture
def clean_runtime(monkeypatch):
    """The process-wide billing singletons, restored afterwards.

    They are module globals shared by every test in the session, so leaving a
    service built here in place would change what an unrelated test sees.
    """
    saved = (
        billing_runtime._BILLING_STORE,
        billing_runtime._STRIPE_SERVICE,
        billing_runtime._BILLING_API,
        billing_runtime._BILLING_API_STRIPE,
    )
    billing_runtime._STRIPE_SERVICE = None
    billing_runtime._BILLING_API = None
    billing_runtime._BILLING_API_STRIPE = None
    cfg.get_settings.cache_clear()
    yield
    (
        billing_runtime._BILLING_STORE,
        billing_runtime._STRIPE_SERVICE,
        billing_runtime._BILLING_API,
        billing_runtime._BILLING_API_STRIPE,
    ) = saved
    cfg.get_settings.cache_clear()


class TestTheBillingApiFollowsTheConfiguration:
    async def test_a_billing_api_built_before_the_key_picks_it_up_afterwards(
        self, clean_runtime, monkeypatch
    ):
        """The failure this closes: one early call — a health check, a feature
        flag, an import-time warm-up — before STRIPE_API_KEY is in the
        environment used to pin `stripe_service=None` for the life of the
        process. Every real delivery after that answered
        `billing_store: not_configured` and `billing_subscriptions` stayed empty
        while customers were being charged.
        """
        monkeypatch.delenv("STRIPE_API_KEY", raising=False)
        cfg.get_settings.cache_clear()
        early = billing_runtime.get_billing_api()
        with pytest.raises(BillingUnavailableError):
            await early.handle_webhook(b"{}", "sig")

        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        monkeypatch.setattr(
            billing_runtime, "make_stripe_client", lambda key: _FakeStripeAdapter()
        )
        cfg.get_settings.cache_clear()

        result = await billing_runtime.get_billing_api().handle_webhook(b"{}", "sig")

        assert result == {"status": "ignored", "event_type": "payment_intent.created"}

    def test_the_instance_is_still_shared_once_it_is_settled(
        self, clean_runtime, monkeypatch
    ):
        """Rebuilding happens on the transition only. A fresh BillingAPI per call
        would mean a fresh FeatureGateService per call, which is the kind of
        per-call state the singleton exists to prevent."""
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        monkeypatch.setattr(
            billing_runtime, "make_stripe_client", lambda key: _FakeStripeAdapter()
        )
        cfg.get_settings.cache_clear()

        assert billing_runtime.get_billing_api() is billing_runtime.get_billing_api()

    def test_an_unconfigured_deployment_keeps_one_instance_too(
        self, clean_runtime, monkeypatch
    ):
        """No Stripe key is a steady state, not a transition; it must not rebuild
        on every call."""
        monkeypatch.delenv("STRIPE_API_KEY", raising=False)
        cfg.get_settings.cache_clear()

        assert billing_runtime.get_billing_api() is billing_runtime.get_billing_api()


# --------------------------------------------------------------------------- #
# 3. `change_plan` reads line items through the mapping, not the attribute
# --------------------------------------------------------------------------- #
class StripeObjectLike(dict):
    """A `dict` subclass with attribute access, as the Stripe SDK's objects are.

    The point of the shape: `obj.items` resolves to `dict.items` — found on the
    class, so `__getattr__` never runs — which is why the subscription's line
    items cannot be reached with a dot.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class _ChangePlanStore:
    def __init__(self, subscription: Subscription) -> None:
        self.subscription = subscription
        self.saved: list[Subscription] = []

    async def get_subscription(self, org_id: UUID) -> Subscription | None:
        return self.subscription if org_id == self.subscription.org_id else None

    async def save_subscription(self, sub: Subscription) -> None:
        self.saved.append(sub)


class _ChangePlanStripe:
    def __init__(self, stripe_sub: Any) -> None:
        self.stripe_sub = stripe_sub
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def get_subscription(self, subscription_id: str) -> Any:
        return self.stripe_sub

    def update_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        self.updates.append((subscription_id, kwargs))
        return self.stripe_sub


class TestChangePlanOnSdkShapedObjects:
    def _service(self, stripe_sub: Any):
        store = _ChangePlanStore(
            Subscription(
                org_id=ORG,
                plan_id=plan_uuid("team"),
                status=SubscriptionStatus.ACTIVE,
                billing_cycle=BillingCycle.MONTHLY,
                stripe_subscription_id="sub_test_1",
            )
        )
        stripe = _ChangePlanStripe(stripe_sub)
        return (
            StripeBillingService(
                store=store, stripe_client=stripe, webhook_secret="whsec_test"
            ),
            store,
            stripe,
        )

    def test_the_shape_this_is_about_really_does_shadow_items(self):
        """If this stops being true the guard below is no longer needed."""
        obj = StripeObjectLike(items=StripeObjectLike(data=[StripeObjectLike(id="si_1")]))

        assert callable(obj.items), "dict.items no longer shadows the attribute"
        assert subscription_price_id(obj) == ""  # no price on this item
        assert obj["items"]["data"][0]["id"] == "si_1"

    async def test_the_existing_line_item_is_the_one_that_is_repriced(self, prices):
        """Stripe replaces the item only when it is named by id; without it the
        update either fails or adds a second line the customer is billed for."""
        stripe_sub = StripeObjectLike(
            id="sub_test_1",
            items=StripeObjectLike(data=[StripeObjectLike(id="si_1")]),
        )
        svc, store, stripe = self._service(stripe_sub)

        await svc.change_plan(ORG, "growth", BillingCycle.MONTHLY)

        _, kwargs = stripe.updates[0]
        assert kwargs["items"] == [{"id": "si_1", "price": "price_growth_m"}]
        assert store.saved[0].plan_id == plan_uuid("growth")

    async def test_a_subscription_with_no_line_items_is_refused_not_crashed(self, prices):
        """This was an IndexError out of the handler — a 500 that reads as an
        outage. It is a 400 with a reason now (billing_router maps ValueError)."""
        stripe_sub = StripeObjectLike(id="sub_test_1", items=StripeObjectLike(data=[]))
        svc, store, stripe = self._service(stripe_sub)

        with pytest.raises(ValueError, match="no line items"):
            await svc.change_plan(ORG, "growth", BillingCycle.MONTHLY)

        assert stripe.updates == [], "nothing may be repriced on a guess"
        assert store.saved == [], "the local row must not move either"
