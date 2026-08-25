"""Checkout could not succeed in any configuration. These pin the three reasons.

1. **No client.** `app/api/billing_router.py` passed the imported `stripe`
   *module* as the Stripe client. The module has no `create_customer`,
   `create_checkout_session` or `construct_webhook_event`, so a deployment with
   the SDK installed and a key set got an AttributeError — a bare 500 — on the
   first checkout. `StripeSDKClient` is the adapter that actually implements the
   protocol.
2. **No price.** Stripe Price IDs live inside a Stripe account, so every
   `stripe_monthly_price_id` in the shipped catalog is `""` and nothing ever
   assigned one. Checkout hit "No Stripe price configured" for every plan, in
   every deployment, however Stripe was set up. `STRIPE_PRICE_IDS` supplies them.
3. **No organization row.** `billing_organizations` has exactly one writer — the
   Stripe service — so the row does not exist until an org's first Stripe
   interaction, and checkout raised "Organization not found" before creating it.
   The first checkout could never be the first checkout.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import app.config as cfg
from app.billing.models import BillingCycle, Organization
from app.billing.stripe_client import StripeSDKClient, make_stripe_client
from app.billing.stripe_integration import (
    StripeBillingService,
    StripeClient,
    configured_price_id,
)

ORG = UUID("11111111-2222-3333-4444-555555555555")


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeStore:
    """The bits of BillingStore the checkout/portal paths touch."""

    def __init__(self, org: Organization | None = None):
        self.orgs: dict[UUID, Organization] = {org.org_id: org} if org else {}
        self.saved: list[Organization] = []

    async def get_organization(self, org_id: UUID) -> Organization | None:
        return self.orgs.get(org_id)

    async def save_organization(self, org: Organization) -> None:
        self.orgs[org.org_id] = org
        self.saved.append(org)

    async def get_subscription(self, org_id: UUID):
        return None


class FakeSession:
    def __init__(self, **kw):
        self.id = "cs_test_123"
        self.url = "https://checkout.stripe.test/cs_test_123"
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeStripe:
    """Records what the service asked Stripe to do."""

    def __init__(self):
        self.checkout_calls: list[dict] = []
        self.customer_calls: list[dict] = []

    def create_customer(self, **kwargs):
        self.customer_calls.append(kwargs)
        return FakeSession(id="cus_test_1")

    def create_checkout_session(self, **kwargs):
        self.checkout_calls.append(kwargs)
        return FakeSession()

    def create_billing_portal_session(self, **kwargs):
        return FakeSession(url="https://portal.stripe.test/s")


def _service(store: FakeStore, stripe: FakeStripe | None = None) -> StripeBillingService:
    return StripeBillingService(
        store=store,
        stripe_client=stripe or FakeStripe(),
        webhook_secret="whsec_test",
        frontend_url="https://app.test",
    )


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setenv(
        "STRIPE_PRICE_IDS",
        "team:monthly=price_team_m,team:annual=price_team_a,growth:monthly=price_growth_m",
    )
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 1. The client adapter
# --------------------------------------------------------------------------- #
class TestStripeClientAdapter:
    def test_the_adapter_implements_every_protocol_method(self):
        """The `stripe` module implements none of these. That was the 500."""
        required = [
            name for name in StripeClient.__annotations__.keys()
        ] or [
            m for m in dir(StripeClient)
            if not m.startswith("_") and callable(getattr(StripeClient, m, None))
        ]
        for name in required:
            assert callable(getattr(StripeSDKClient, name, None)), f"missing {name}"

    def test_the_bare_stripe_module_would_not_have_worked(self):
        """Pin the actual defect: this is why passing the module 500'd."""
        stripe = pytest.importorskip("stripe")
        assert not hasattr(stripe, "create_checkout_session")
        assert not hasattr(stripe, "create_customer")

    @pytest.mark.parametrize("key", ["", "   "])
    def test_no_api_key_means_no_client(self, key):
        assert make_stripe_client(key) is None

    def test_a_missing_sdk_is_a_503_not_a_crash(self, monkeypatch):
        def no_sdk(self, api_key):
            raise ImportError("No module named 'stripe'")

        monkeypatch.setattr(StripeSDKClient, "__init__", no_sdk)
        assert make_stripe_client("sk_test_123") is None


# --------------------------------------------------------------------------- #
# 2. Price resolution
# --------------------------------------------------------------------------- #
class TestPriceResolution:
    def test_the_shipped_catalog_carries_no_price_ids(self):
        """Which is correct — they are account-specific — and why the deployment
        has to supply them. Without this the guard below can never pass."""
        from app.billing.plans import PRICING_PLANS

        assert all(
            not p.stripe_monthly_price_id and not p.stripe_annual_price_id
            for p in PRICING_PLANS.values()
        )

    def test_monthly_and_annual_resolve_separately(self, prices):
        assert configured_price_id("team", BillingCycle.MONTHLY) == "price_team_m"
        assert configured_price_id("team", BillingCycle.ANNUAL) == "price_team_a"

    def test_plan_names_are_matched_case_insensitively(self, prices):
        assert configured_price_id("  TEAM ", BillingCycle.MONTHLY) == "price_team_m"

    def test_an_unconfigured_plan_or_cycle_resolves_to_empty(self, prices):
        assert configured_price_id("growth", BillingCycle.ANNUAL) == ""
        assert configured_price_id("scale", BillingCycle.MONTHLY) == ""
        assert configured_price_id("", BillingCycle.MONTHLY) == ""

    def test_a_malformed_entry_does_not_take_the_others_down(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_IDS", "garbage,team:monthly=price_ok,=,x=")
        cfg.get_settings.cache_clear()
        assert configured_price_id("team", BillingCycle.MONTHLY) == "price_ok"
        cfg.get_settings.cache_clear()

    async def test_checkout_sends_the_configured_price_to_stripe(self, prices):
        stripe = FakeStripe()
        svc = _service(FakeStore(), stripe)

        await svc.create_checkout_session(ORG, "team", BillingCycle.MONTHLY, email="a@b.test")

        assert stripe.checkout_calls, "checkout never reached Stripe"
        line_items = stripe.checkout_calls[0]["line_items"]
        assert line_items[0]["price"] == "price_team_m"

    async def test_checkout_without_a_configured_price_says_which_plan(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_IDS", "")
        cfg.get_settings.cache_clear()
        svc = _service(FakeStore())

        with pytest.raises(ValueError, match="price"):
            await svc.create_checkout_session(ORG, "team", BillingCycle.MONTHLY, email="a@b.test")
        cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 3. The organization row
# --------------------------------------------------------------------------- #
class TestOrganizationBootstrap:
    async def test_the_first_checkout_creates_the_billing_org(self, prices):
        """It used to raise "Organization not found" — for every org, forever."""
        store = FakeStore()
        svc = _service(store)

        result = await svc.create_checkout_session(
            ORG, "team", BillingCycle.MONTHLY, email="a@b.test"
        )

        assert result.checkout_url.startswith("https://checkout.stripe.test/")
        assert ORG in store.orgs
        # ...and the Stripe customer created along the way was attached to it.
        assert store.orgs[ORG].stripe_customer_id == "cus_test_1"

    async def test_an_existing_org_is_not_overwritten(self, prices):
        existing = Organization(org_id=ORG, name="Acme", slug="acme")
        store = FakeStore(existing)
        svc = _service(store)

        await svc.create_checkout_session(ORG, "team", BillingCycle.MONTHLY, email="a@b.test")

        # The only write is the customer attach; the name is never reset to the
        # placeholder that ensure_organization uses for a brand-new row.
        assert store.orgs[ORG].name == "Acme"
        assert all(o.name == "Acme" for o in store.saved)

    async def test_ensure_organization_is_idempotent(self):
        store = FakeStore()
        svc = _service(store)

        first = await svc.ensure_organization(ORG)
        second = await svc.ensure_organization(ORG)

        assert first.org_id == second.org_id == ORG
        assert len(store.saved) == 1

    async def test_the_portal_explains_that_there_is_nothing_to_manage_yet(self):
        """Previously "Organization has no Stripe customer", after a lookup that
        raised "Organization not found" first."""
        svc = _service(FakeStore())

        with pytest.raises(ValueError, match="no Stripe customer yet"):
            await svc.create_portal_session(uuid4())

    async def test_the_portal_opens_once_there_is_a_customer(self):
        org = Organization(org_id=ORG, name="Acme", slug="acme")
        org.stripe_customer_id = "cus_existing"
        svc = _service(FakeStore(org))

        result = await svc.create_portal_session(ORG, return_url="https://app.test/billing")

        assert result.portal_url == "https://portal.stripe.test/s"
