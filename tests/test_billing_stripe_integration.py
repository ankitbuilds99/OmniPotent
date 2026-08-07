"""Tests for billing Stripe integration module."""

from __future__ import annotations

import pytest
import time
import json
import hashlib
import hmac
from datetime import datetime, timezone
from uuid import uuid4

from app.billing.models import (
    Organization,
    PricingPlan,
    Subscription,
    SubscriptionStatus,
    BillingCycle,
    InvoiceStatus,
    PlanTier,
)
from app.billing.stripe_integration import (
    StripeBillingService,
    CheckoutResult,
    PortalResult,
    WebhookResult,
    verify_stripe_signature,
)
from app.billing.store import InMemoryBillingStore, FakeStripeClient


class TestCheckoutResult:
    """Tests for CheckoutResult dataclass."""

    def test_fields(self):
        """CheckoutResult should store all fields."""
        result = CheckoutResult(
            checkout_url="https://checkout.stripe.com/pay/123",
            session_id="cs_123",
        )
        assert result.checkout_url == "https://checkout.stripe.com/pay/123"
        assert result.session_id == "cs_123"


class TestPortalResult:
    """Tests for PortalResult dataclass."""

    def test_fields(self):
        """PortalResult should store portal URL."""
        result = PortalResult(portal_url="https://billing.stripe.com/p/123")
        assert result.portal_url == "https://billing.stripe.com/p/123"


class TestWebhookResult:
    """Tests for WebhookResult dataclass."""

    def test_fields(self):
        """WebhookResult should store all fields."""
        result = WebhookResult(
            status="processed",
            event_type="customer.subscription.created",
            data={"id": "sub_123"},
        )
        assert result.status == "processed"
        assert result.event_type == "customer.subscription.created"
        assert result.data == {"id": "sub_123"}


class TestStripeBillingService:
    """Tests for StripeBillingService class."""

    @pytest.fixture
    def store(self) -> InMemoryBillingStore:
        """Create a fresh store."""
        return InMemoryBillingStore()

    @pytest.fixture
    def stripe_client(self) -> FakeStripeClient:
        """Create a fake Stripe client."""
        return FakeStripeClient()

    @pytest.fixture
    def service(
        self, store: InMemoryBillingStore, stripe_client: FakeStripeClient
    ) -> StripeBillingService:
        """Create a StripeBillingService instance."""
        return StripeBillingService(
            store=store,
            stripe_client=stripe_client,
            webhook_secret="whsec_test",
            frontend_url="http://localhost:3000",
        )

    @pytest.fixture
    def sample_org(self) -> Organization:
        """Create a sample organization."""
        return Organization(
            org_id=uuid4(),
            name="Test Org",
            slug="test-org",
        )

    @pytest.fixture
    def sample_plan(self) -> PricingPlan:
        """Create a sample plan with Stripe price IDs."""
        return PricingPlan(
            plan_id=uuid4(),
            name="team",
            display_name="Team",
            tier=PlanTier.TEAM,
            monthly_price_cents=29900,
            annual_price_cents=304800,
            stripe_monthly_price_id="price_monthly_123",
            stripe_annual_price_id="price_annual_123",
            trial_days=14,
        )

    # Customer tests

    @pytest.mark.asyncio
    async def test_create_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """create_customer should create Stripe customer and update org."""
        await store.save_organization(sample_org)

        customer_id = await service.create_customer(
            sample_org, "billing@test.org", "Test Org"
        )

        assert customer_id.startswith("cus_")

        org = await store.get_organization(sample_org.org_id)
        assert org.stripe_customer_id == customer_id
        assert org.billing_email == "billing@test.org"

    @pytest.mark.asyncio
    async def test_ensure_customer_existing(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """ensure_customer should return existing customer ID."""
        sample_org.stripe_customer_id = "cus_existing"
        await store.save_organization(sample_org)

        customer_id = await service.ensure_customer(sample_org, "test@test.org")

        assert customer_id == "cus_existing"

    @pytest.mark.asyncio
    async def test_ensure_customer_creates_new(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """ensure_customer should create customer if not exists."""
        await store.save_organization(sample_org)

        customer_id = await service.ensure_customer(sample_org, "test@test.org")

        assert customer_id.startswith("cus_")

    # Checkout session tests

    @pytest.mark.asyncio
    async def test_create_checkout_session(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
        sample_plan: PricingPlan,
    ):
        """create_checkout_session should create session with URL."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)

        # Register plan in manager
        service._plan_manager._plans["team"] = sample_plan

        result = await service.create_checkout_session(
            org_id=sample_org.org_id,
            plan_name="team",
            billing_cycle=BillingCycle.MONTHLY,
        )

        assert isinstance(result, CheckoutResult)
        assert result.checkout_url.startswith("https://")
        assert result.session_id.startswith("cs_")

    @pytest.mark.asyncio
    async def test_create_checkout_session_annual(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
        sample_plan: PricingPlan,
    ):
        """create_checkout_session should use annual price ID."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)
        service._plan_manager._plans["team"] = sample_plan

        result = await service.create_checkout_session(
            org_id=sample_org.org_id,
            plan_name="team",
            billing_cycle=BillingCycle.ANNUAL,
        )

        assert isinstance(result, CheckoutResult)

    @pytest.mark.asyncio
    async def test_create_checkout_session_creates_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
        sample_plan: PricingPlan,
    ):
        """create_checkout_session should create customer if needed."""
        await store.save_organization(sample_org)
        service._plan_manager._plans["team"] = sample_plan

        await service.create_checkout_session(
            org_id=sample_org.org_id,
            plan_name="team",
            email="test@test.org",
        )

        org = await store.get_organization(sample_org.org_id)
        assert org.stripe_customer_id is not None

    @pytest.mark.asyncio
    async def test_create_checkout_session_org_not_found(
        self, service: StripeBillingService
    ):
        """create_checkout_session should raise for nonexistent org."""
        with pytest.raises(ValueError, match="Organization not found"):
            await service.create_checkout_session(uuid4(), "team")

    @pytest.mark.asyncio
    async def test_create_checkout_session_plan_not_found(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """create_checkout_session should raise for unknown plan."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)

        with pytest.raises(ValueError, match="Unknown plan"):
            await service.create_checkout_session(sample_org.org_id, "nonexistent")

    @pytest.mark.asyncio
    async def test_create_checkout_session_no_email(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
        sample_plan: PricingPlan,
    ):
        """create_checkout_session should raise if no customer and no email."""
        await store.save_organization(sample_org)
        service._plan_manager._plans["team"] = sample_plan

        with pytest.raises(ValueError, match="Email required"):
            await service.create_checkout_session(sample_org.org_id, "team")

    @pytest.mark.asyncio
    async def test_create_checkout_session_no_price_id(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """create_checkout_session should raise if no Stripe price configured."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)

        plan = PricingPlan(
            plan_id=uuid4(),
            name="test",
            tier=PlanTier.TEAM,
            # No stripe_monthly_price_id set
        )
        service._plan_manager._plans["test"] = plan

        with pytest.raises(ValueError, match="No Stripe price configured"):
            await service.create_checkout_session(sample_org.org_id, "test")

    # Portal session tests

    @pytest.mark.asyncio
    async def test_create_portal_session(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """create_portal_session should create session with URL."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)

        result = await service.create_portal_session(sample_org.org_id)

        assert isinstance(result, PortalResult)
        assert "billing.stripe.com" in result.portal_url

    @pytest.mark.asyncio
    async def test_create_portal_session_org_not_found(
        self, service: StripeBillingService
    ):
        """create_portal_session should raise for nonexistent org."""
        with pytest.raises(ValueError, match="Organization not found"):
            await service.create_portal_session(uuid4())

    @pytest.mark.asyncio
    async def test_create_portal_session_no_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """create_portal_session should raise if no Stripe customer."""
        await store.save_organization(sample_org)

        with pytest.raises(ValueError, match="no Stripe customer"):
            await service.create_portal_session(sample_org.org_id)

    # Change plan tests

    @pytest.mark.asyncio
    async def test_change_plan(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """change_plan should update subscription."""
        # Setup existing subscription
        stripe_sub = stripe_client.create_subscription(
            customer="cus_123",
            items=[{"price": "price_old"}],
        )

        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id=stripe_sub.id,
        )
        await store.save_subscription(subscription)

        new_plan = PricingPlan(
            plan_id=uuid4(),
            name="growth",
            tier=PlanTier.GROWTH,
            stripe_monthly_price_id="price_growth",
        )
        service._plan_manager._plans["growth"] = new_plan

        result = await service.change_plan(sample_org.org_id, "growth")

        assert result.plan_id == new_plan.plan_id

    @pytest.mark.asyncio
    async def test_change_plan_no_subscription(
        self, service: StripeBillingService, sample_org: Organization
    ):
        """change_plan should raise for nonexistent subscription."""
        with pytest.raises(ValueError, match="No subscription"):
            await service.change_plan(sample_org.org_id, "team")

    @pytest.mark.asyncio
    async def test_change_plan_unknown_plan(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """change_plan should raise for unknown plan."""
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(subscription)

        with pytest.raises(ValueError, match="Unknown plan"):
            await service.change_plan(sample_org.org_id, "nonexistent")

    # Cancel subscription tests

    @pytest.mark.asyncio
    async def test_cancel_subscription_at_period_end(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """cancel_subscription should set cancel_at_period_end."""
        stripe_sub = stripe_client.create_subscription(customer="cus_123", items=[])
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id=stripe_sub.id,
        )
        await store.save_subscription(subscription)

        result = await service.cancel_subscription(
            sample_org.org_id,
            reason="Too expensive",
            immediate=False,
        )

        assert result.cancel_at_period_end is True
        assert result.status == SubscriptionStatus.ACTIVE
        assert result.cancel_reason == "Too expensive"

    @pytest.mark.asyncio
    async def test_cancel_subscription_immediate(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """cancel_subscription should cancel immediately."""
        stripe_sub = stripe_client.create_subscription(customer="cus_123", items=[])
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id=stripe_sub.id,
        )
        await store.save_subscription(subscription)

        result = await service.cancel_subscription(
            sample_org.org_id,
            immediate=True,
        )

        assert result.status == SubscriptionStatus.CANCELED
        assert result.canceled_at is not None

    @pytest.mark.asyncio
    async def test_cancel_subscription_no_subscription(
        self, service: StripeBillingService, sample_org: Organization
    ):
        """cancel_subscription should raise for nonexistent subscription."""
        with pytest.raises(ValueError, match="No subscription"):
            await service.cancel_subscription(sample_org.org_id)

    # Reactivate subscription tests

    @pytest.mark.asyncio
    async def test_reactivate_subscription(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """reactivate_subscription should reactivate canceled subscription."""
        stripe_sub = stripe_client.create_subscription(customer="cus_123", items=[])
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id=stripe_sub.id,
            cancel_at_period_end=True,
        )
        await store.save_subscription(subscription)

        result = await service.reactivate_subscription(sample_org.org_id)

        assert result.status == SubscriptionStatus.ACTIVE
        assert result.cancel_at_period_end is False
        assert result.canceled_at is None

    # Get invoices tests

    @pytest.mark.asyncio
    async def test_get_invoices_no_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """get_invoices should return empty for org without customer."""
        await store.save_organization(sample_org)

        result = await service.get_invoices(sample_org.org_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_invoices(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """get_invoices should return invoices."""
        sample_org.stripe_customer_id = "cus_123"
        await store.save_organization(sample_org)

        result = await service.get_invoices(sample_org.org_id)

        assert isinstance(result, list)

    # Get payment methods tests

    @pytest.mark.asyncio
    async def test_get_payment_methods_no_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """get_payment_methods should return empty for org without customer."""
        await store.save_organization(sample_org)

        result = await service.get_payment_methods(sample_org.org_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_payment_methods(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """get_payment_methods should return payment methods."""
        customer = stripe_client.create_customer(email="test@test.org")
        sample_org.stripe_customer_id = customer.id
        await store.save_organization(sample_org)

        result = await service.get_payment_methods(sample_org.org_id)

        assert isinstance(result, list)

    # Set default payment method tests

    @pytest.mark.asyncio
    async def test_set_default_payment_method(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        stripe_client: FakeStripeClient,
        sample_org: Organization,
    ):
        """set_default_payment_method should update customer."""
        customer = stripe_client.create_customer(email="test@test.org")
        sample_org.stripe_customer_id = customer.id
        await store.save_organization(sample_org)

        await service.set_default_payment_method(sample_org.org_id, "pm_123")

        # Should not raise

    @pytest.mark.asyncio
    async def test_set_default_payment_method_no_customer(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """set_default_payment_method should raise for no customer."""
        await store.save_organization(sample_org)

        with pytest.raises(ValueError, match="no Stripe customer"):
            await service.set_default_payment_method(sample_org.org_id, "pm_123")

    # Webhook handling tests

    @pytest.mark.asyncio
    async def test_handle_webhook_checkout_completed(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """handle_webhook should process checkout.session.completed."""
        await store.save_organization(sample_org)

        payload = json.dumps({
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {
                        "organization_id": str(sample_org.org_id),
                        "plan_name": "team",
                    }
                }
            },
        }).encode()

        result = await service.handle_webhook(payload, "sig")

        assert result.status == "processed"
        assert result.event_type == "checkout.session.completed"

    @pytest.mark.asyncio
    async def test_handle_webhook_subscription_created(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
        sample_plan: PricingPlan,
    ):
        """handle_webhook should process customer.subscription.created."""
        await store.save_organization(sample_org)
        service._plan_manager._plans["team"] = sample_plan

        now = int(datetime.now(timezone.utc).timestamp())
        payload = json.dumps({
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_123",
                    "customer": "cus_123",
                    "status": "active",
                    "current_period_start": now,
                    "current_period_end": now + 30 * 24 * 3600,
                    "trial_start": None,
                    "trial_end": None,
                    "items": {
                        "data": [{
                            "price": {
                                "recurring": {"interval": "month"}
                            }
                        }]
                    },
                    "metadata": {
                        "organization_id": str(sample_org.org_id),
                        "plan_name": "team",
                    }
                }
            },
        }).encode()

        result = await service.handle_webhook(payload, "sig")

        assert result.status == "processed"

        sub = await store.get_subscription(sample_org.org_id)
        assert sub is not None
        assert sub.stripe_subscription_id == "sub_123"

    @pytest.mark.asyncio
    async def test_handle_webhook_subscription_updated(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """handle_webhook should process customer.subscription.updated."""
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_123",
        )
        await store.save_subscription(subscription)

        now = int(datetime.now(timezone.utc).timestamp())
        payload = json.dumps({
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "status": "past_due",
                    "current_period_start": now,
                    "current_period_end": now + 30 * 24 * 3600,
                    "cancel_at_period_end": False,
                    "canceled_at": None,
                }
            },
        }).encode()

        await service.handle_webhook(payload, "sig")

        sub = await store.get_subscription(sample_org.org_id)
        assert sub.status == SubscriptionStatus.PAST_DUE

    @pytest.mark.asyncio
    async def test_handle_webhook_subscription_deleted(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """handle_webhook should process customer.subscription.deleted."""
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_123",
        )
        await store.save_subscription(subscription)

        payload = json.dumps({
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_123"}}
        }).encode()

        await service.handle_webhook(payload, "sig")

        sub = await store.get_subscription(sample_org.org_id)
        assert sub.status == SubscriptionStatus.CANCELED

    @pytest.mark.asyncio
    async def test_handle_webhook_invoice_paid(
        self,
        service: StripeBillingService,
    ):
        """handle_webhook should process invoice.paid."""
        payload = json.dumps({
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "inv_123",
                    "number": "INV-001",
                    "amount_paid": 9900,
                    "currency": "usd",
                    "invoice_pdf": "https://stripe.com/invoice.pdf",
                    "hosted_invoice_url": "https://stripe.com/invoice",
                }
            },
        }).encode()

        result = await service.handle_webhook(payload, "sig")

        assert result.status == "processed"

    @pytest.mark.asyncio
    async def test_handle_webhook_payment_failed(
        self,
        service: StripeBillingService,
        store: InMemoryBillingStore,
        sample_org: Organization,
    ):
        """handle_webhook should process invoice.payment_failed."""
        subscription = Subscription(
            org_id=sample_org.org_id,
            plan_id=uuid4(),
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_123",
        )
        await store.save_subscription(subscription)

        payload = json.dumps({
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "inv_123",
                    "subscription": "sub_123",
                }
            },
        }).encode()

        await service.handle_webhook(payload, "sig")

        sub = await store.get_subscription(sample_org.org_id)
        assert sub.status == SubscriptionStatus.PAST_DUE

    @pytest.mark.asyncio
    async def test_handle_webhook_unknown_event(
        self, service: StripeBillingService
    ):
        """handle_webhook should handle unknown events gracefully."""
        payload = json.dumps({
            "type": "unknown.event",
            "data": {"object": {}}
        }).encode()

        result = await service.handle_webhook(payload, "sig")

        assert result.status == "processed"
        assert result.event_type == "unknown.event"

    # Status mapping tests

    def test_map_subscription_status(self, service: StripeBillingService):
        """_map_subscription_status should map Stripe statuses."""
        assert service._map_subscription_status("trialing") == SubscriptionStatus.TRIALING
        assert service._map_subscription_status("active") == SubscriptionStatus.ACTIVE
        assert service._map_subscription_status("past_due") == SubscriptionStatus.PAST_DUE
        assert service._map_subscription_status("canceled") == SubscriptionStatus.CANCELED
        assert service._map_subscription_status("unpaid") == SubscriptionStatus.UNPAID
        assert service._map_subscription_status("incomplete") == SubscriptionStatus.INCOMPLETE
        assert service._map_subscription_status("paused") == SubscriptionStatus.PAUSED
        assert service._map_subscription_status("unknown") == SubscriptionStatus.ACTIVE

    def test_map_invoice_status(self, service: StripeBillingService):
        """_map_invoice_status should map Stripe statuses."""
        assert service._map_invoice_status("draft") == InvoiceStatus.DRAFT
        assert service._map_invoice_status("open") == InvoiceStatus.OPEN
        assert service._map_invoice_status("paid") == InvoiceStatus.PAID
        assert service._map_invoice_status("void") == InvoiceStatus.VOID
        assert service._map_invoice_status("uncollectible") == InvoiceStatus.UNCOLLECTIBLE
        assert service._map_invoice_status("unknown") == InvoiceStatus.DRAFT


class TestVerifyStripeSignature:
    """Tests for verify_stripe_signature function."""

    def _make_signature(self, payload: bytes, secret: str, timestamp: int) -> str:
        """Create a valid Stripe signature."""
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"t={timestamp},v1={expected_sig}"

    def test_valid_signature(self):
        """verify_stripe_signature should accept valid signature."""
        payload = b'{"type":"test"}'
        secret = "whsec_test"
        timestamp = int(time.time())
        signature = self._make_signature(payload, secret, timestamp)

        result = verify_stripe_signature(payload, signature, secret)

        assert result is True

    def test_empty_signature(self):
        """verify_stripe_signature should reject empty signature."""
        result = verify_stripe_signature(b'{}', "", "secret")
        assert result is False

    def test_invalid_signature(self):
        """verify_stripe_signature should reject invalid signature."""
        payload = b'{"type":"test"}'
        signature = "t=12345,v1=invalid"

        result = verify_stripe_signature(payload, signature, "secret")

        assert result is False

    def test_wrong_secret(self):
        """verify_stripe_signature should reject wrong secret."""
        payload = b'{"type":"test"}'
        timestamp = int(time.time())
        signature = self._make_signature(payload, "right_secret", timestamp)

        result = verify_stripe_signature(payload, signature, "wrong_secret")

        assert result is False

    def test_stale_timestamp(self):
        """verify_stripe_signature should reject stale timestamps."""
        payload = b'{"type":"test"}'
        secret = "whsec_test"
        old_timestamp = int(time.time()) - 400  # > 300 tolerance
        signature = self._make_signature(payload, secret, old_timestamp)

        result = verify_stripe_signature(payload, signature, secret)

        assert result is False

    def test_missing_timestamp(self):
        """verify_stripe_signature should reject missing timestamp."""
        signature = "v1=abc123"

        result = verify_stripe_signature(b'{}', signature, "secret")

        assert result is False

    def test_missing_v1(self):
        """verify_stripe_signature should reject missing v1 signature."""
        signature = "t=12345"

        result = verify_stripe_signature(b'{}', signature, "secret")

        assert result is False
