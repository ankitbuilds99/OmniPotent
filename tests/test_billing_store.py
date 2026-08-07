"""Tests for billing store module."""

from __future__ import annotations

import pytest
from datetime import datetime, date, timedelta, timezone
from uuid import uuid4

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
    PlanTier,
    SubscriptionStatus,
    InvoiceStatus,
)
from app.billing.store import InMemoryBillingStore, FakeStripeClient


class TestInMemoryBillingStore:
    """Tests for InMemoryBillingStore class."""

    @pytest.fixture
    def store(self) -> InMemoryBillingStore:
        """Create a fresh store instance."""
        return InMemoryBillingStore()

    @pytest.fixture
    def sample_org(self) -> Organization:
        """Create a sample organization."""
        return Organization(
            org_id=uuid4(),
            name="Test Org",
            slug="test-org",
            billing_email="billing@test.org",
        )

    @pytest.fixture
    def sample_plan(self) -> PricingPlan:
        """Create a sample plan."""
        return PricingPlan(
            plan_id=uuid4(),
            name="test_plan",
            display_name="Test Plan",
            tier=PlanTier.TEAM,
            monthly_price_cents=9900,
        )

    @pytest.fixture
    def sample_subscription(self, sample_org: Organization, sample_plan: PricingPlan) -> Subscription:
        """Create a sample subscription."""
        return Subscription(
            subscription_id=uuid4(),
            org_id=sample_org.org_id,
            plan_id=sample_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
            stripe_subscription_id="sub_test123",
        )

    # Organization tests

    @pytest.mark.asyncio
    async def test_get_organization_not_found(self, store: InMemoryBillingStore):
        """get_organization should return None for nonexistent org."""
        result = await store.get_organization(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_get_organization(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_organization should persist org for retrieval."""
        await store.save_organization(sample_org)

        result = await store.get_organization(sample_org.org_id)

        assert result is not None
        assert result.org_id == sample_org.org_id
        assert result.name == "Test Org"

    @pytest.mark.asyncio
    async def test_update_organization(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_organization should update existing org."""
        await store.save_organization(sample_org)

        sample_org.name = "Updated Org"
        await store.save_organization(sample_org)

        result = await store.get_organization(sample_org.org_id)
        assert result.name == "Updated Org"

    # Subscription tests

    @pytest.mark.asyncio
    async def test_get_subscription_not_found(self, store: InMemoryBillingStore):
        """get_subscription should return None for nonexistent subscription."""
        result = await store.get_subscription(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_get_subscription(
        self, store: InMemoryBillingStore, sample_subscription: Subscription
    ):
        """save_subscription should persist subscription for retrieval."""
        await store.save_subscription(sample_subscription)

        result = await store.get_subscription(sample_subscription.org_id)

        assert result is not None
        assert result.subscription_id == sample_subscription.subscription_id

    @pytest.mark.asyncio
    async def test_get_subscription_by_stripe_id(
        self, store: InMemoryBillingStore, sample_subscription: Subscription
    ):
        """get_subscription_by_stripe_id should find subscription."""
        await store.save_subscription(sample_subscription)

        result = await store.get_subscription_by_stripe_id("sub_test123")

        assert result is not None
        assert result.subscription_id == sample_subscription.subscription_id

    @pytest.mark.asyncio
    async def test_get_subscription_by_stripe_id_not_found(
        self, store: InMemoryBillingStore
    ):
        """get_subscription_by_stripe_id should return None for nonexistent."""
        result = await store.get_subscription_by_stripe_id("sub_nonexistent")
        assert result is None

    # Plan tests

    @pytest.mark.asyncio
    async def test_get_plan_by_id(self, store: InMemoryBillingStore):
        """get_plan should find plan by ID."""
        # Plans are preloaded from PRICING_PLANS
        plans = await store.get_all_plans()
        if plans:
            plan_id = plans[0].plan_id
            result = await store.get_plan(plan_id)
            assert result is not None
            assert result.plan_id == plan_id

    @pytest.mark.asyncio
    async def test_get_plan_not_found(self, store: InMemoryBillingStore):
        """get_plan should return None for nonexistent plan."""
        result = await store.get_plan(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_plan_by_name(self, store: InMemoryBillingStore):
        """get_plan_by_name should find plan by name."""
        result = await store.get_plan_by_name("team")
        assert result is not None
        assert result.name == "team"

    @pytest.mark.asyncio
    async def test_get_plan_by_name_not_found(self, store: InMemoryBillingStore):
        """get_plan_by_name should return None for nonexistent plan."""
        result = await store.get_plan_by_name("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_plans(self, store: InMemoryBillingStore):
        """get_all_plans should return all plans."""
        result = await store.get_all_plans()
        assert len(result) > 0
        assert all(isinstance(p, PricingPlan) for p in result)

    # Invoice tests

    @pytest.mark.asyncio
    async def test_save_and_get_invoices(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_invoice should persist invoice for retrieval."""
        invoice = Invoice(
            invoice_id=uuid4(),
            org_id=sample_org.org_id,
            stripe_invoice_id="inv_test123",
            amount_cents=9900,
            status=InvoiceStatus.PAID,
        )
        await store.save_invoice(invoice)

        result = await store.get_invoices(sample_org.org_id)

        assert len(result) == 1
        assert result[0].invoice_id == invoice.invoice_id

    @pytest.mark.asyncio
    async def test_get_invoices_sorted_by_date(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_invoices should return invoices sorted by date."""
        old_invoice = Invoice(
            invoice_id=uuid4(),
            org_id=sample_org.org_id,
            stripe_invoice_id="inv_old",
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        new_invoice = Invoice(
            invoice_id=uuid4(),
            org_id=sample_org.org_id,
            stripe_invoice_id="inv_new",
            created_at=datetime.now(timezone.utc),
        )

        await store.save_invoice(old_invoice)
        await store.save_invoice(new_invoice)

        result = await store.get_invoices(sample_org.org_id)

        assert len(result) == 2
        assert result[0].stripe_invoice_id == "inv_new"

    @pytest.mark.asyncio
    async def test_get_invoices_with_limit(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_invoices should respect limit parameter."""
        for i in range(5):
            invoice = Invoice(
                invoice_id=uuid4(),
                org_id=sample_org.org_id,
                stripe_invoice_id=f"inv_{i}",
            )
            await store.save_invoice(invoice)

        result = await store.get_invoices(sample_org.org_id, limit=3)

        assert len(result) == 3

    # Payment method tests

    @pytest.mark.asyncio
    async def test_get_payment_methods_empty(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_payment_methods should return empty list for new org."""
        result = await store.get_payment_methods(sample_org.org_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_save_and_get_payment_methods(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_payment_method should persist payment method."""
        pm = PaymentMethod(
            payment_method_id=uuid4(),
            org_id=sample_org.org_id,
            stripe_payment_method_id="pm_test123",
            card_brand="visa",
            card_last4="4242",
        )
        await store.save_payment_method(pm)

        result = await store.get_payment_methods(sample_org.org_id)

        assert len(result) == 1
        assert result[0].card_last4 == "4242"

    @pytest.mark.asyncio
    async def test_update_payment_method(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_payment_method should update existing payment method."""
        pm_id = uuid4()
        pm = PaymentMethod(
            payment_method_id=pm_id,
            org_id=sample_org.org_id,
            stripe_payment_method_id="pm_test123",
            is_default=False,
        )
        await store.save_payment_method(pm)

        pm.is_default = True
        await store.save_payment_method(pm)

        result = await store.get_payment_methods(sample_org.org_id)
        assert len(result) == 1
        assert result[0].is_default is True

    @pytest.mark.asyncio
    async def test_delete_payment_method(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """delete_payment_method should remove payment method."""
        pm = PaymentMethod(
            payment_method_id=uuid4(),
            org_id=sample_org.org_id,
            stripe_payment_method_id="pm_test123",
        )
        await store.save_payment_method(pm)
        await store.delete_payment_method(pm.payment_method_id)

        result = await store.get_payment_methods(sample_org.org_id)
        assert len(result) == 0

    # Usage record tests

    @pytest.mark.asyncio
    async def test_create_usage_record(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """create_usage_record should store usage record."""
        record = UsageRecord(
            record_id=uuid4(),
            org_id=sample_org.org_id,
            metric_type=MetricType.AI_INVESTIGATION,
            quantity=1,
        )
        await store.create_usage_record(record)

        # Verify through get_usage_records
        records = await store.get_usage_records(
            sample_org.org_id,
            MetricType.AI_INVESTIGATION,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_get_usage_records_filtered_by_type(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_usage_records should filter by metric type."""
        record1 = UsageRecord(
            org_id=sample_org.org_id,
            metric_type=MetricType.AI_INVESTIGATION,
            quantity=1,
        )
        record2 = UsageRecord(
            org_id=sample_org.org_id,
            metric_type=MetricType.API_CALL,
            quantity=1,
        )
        await store.create_usage_record(record1)
        await store.create_usage_record(record2)

        result = await store.get_usage_records(
            sample_org.org_id,
            MetricType.AI_INVESTIGATION,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )
        assert len(result) == 1
        assert result[0].metric_type == MetricType.AI_INVESTIGATION

    # Usage summary tests

    @pytest.mark.asyncio
    async def test_get_usage_summary_not_found(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_usage_summary should return None for nonexistent."""
        result = await store.get_usage_summary(sample_org.org_id, "2024-01")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_usage_summary_create(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """upsert_usage_summary should create new summary."""
        summary = UsageSummary(
            summary_id=uuid4(),
            org_id=sample_org.org_id,
            year_month="2024-01",
            ai_investigations_count=10,
        )
        await store.upsert_usage_summary(summary)

        result = await store.get_usage_summary(sample_org.org_id, "2024-01")

        assert result is not None
        assert result.ai_investigations_count == 10

    @pytest.mark.asyncio
    async def test_upsert_usage_summary_update(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """upsert_usage_summary should update existing summary."""
        summary = UsageSummary(
            summary_id=uuid4(),
            org_id=sample_org.org_id,
            year_month="2024-01",
            ai_investigations_count=10,
        )
        await store.upsert_usage_summary(summary)

        summary.ai_investigations_count = 20
        await store.upsert_usage_summary(summary)

        result = await store.get_usage_summary(sample_org.org_id, "2024-01")
        assert result.ai_investigations_count == 20

    @pytest.mark.asyncio
    async def test_increment_summary_field_new(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """increment_summary_field should create summary if not exists."""
        await store.increment_summary_field(
            sample_org.org_id, "2024-01", "ai_investigations_count", 5
        )

        result = await store.get_usage_summary(sample_org.org_id, "2024-01")

        assert result is not None
        assert result.ai_investigations_count == 5

    @pytest.mark.asyncio
    async def test_increment_summary_field_existing(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """increment_summary_field should increment existing field."""
        summary = UsageSummary(
            summary_id=uuid4(),
            org_id=sample_org.org_id,
            year_month="2024-01",
            ai_investigations_count=10,
        )
        await store.upsert_usage_summary(summary)

        await store.increment_summary_field(
            sample_org.org_id, "2024-01", "ai_investigations_count", 5
        )

        result = await store.get_usage_summary(sample_org.org_id, "2024-01")
        assert result.ai_investigations_count == 15

    @pytest.mark.asyncio
    async def test_get_usage_history(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_usage_history should return summaries sorted by date."""
        for month in ["2024-01", "2024-02", "2024-03"]:
            summary = UsageSummary(
                summary_id=uuid4(),
                org_id=sample_org.org_id,
                year_month=month,
                ai_investigations_count=10,
            )
            await store.upsert_usage_summary(summary)

        result = await store.get_usage_history(sample_org.org_id, months=2)

        assert len(result) == 2
        # Should be sorted descending
        assert result[0].year_month >= result[1].year_month

    # Feature override tests

    @pytest.mark.asyncio
    async def test_get_feature_overrides_empty(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """get_feature_overrides should return empty list for new org."""
        result = await store.get_feature_overrides(sample_org.org_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_save_and_get_feature_overrides(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_feature_override should persist override."""
        override = FeatureOverride(
            override_id=uuid4(),
            org_id=sample_org.org_id,
            feature_key="api_access",
            enabled=True,
        )
        await store.save_feature_override(override)

        result = await store.get_feature_overrides(sample_org.org_id)

        assert len(result) == 1
        assert result[0].feature_key == "api_access"

    @pytest.mark.asyncio
    async def test_save_feature_override_replaces_existing(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """save_feature_override should replace override for same feature."""
        override1 = FeatureOverride(
            org_id=sample_org.org_id,
            feature_key="api_access",
            enabled=True,
        )
        override2 = FeatureOverride(
            org_id=sample_org.org_id,
            feature_key="api_access",
            enabled=False,
        )

        await store.save_feature_override(override1)
        await store.save_feature_override(override2)

        result = await store.get_feature_overrides(sample_org.org_id)
        assert len(result) == 1
        assert result[0].enabled is False

    @pytest.mark.asyncio
    async def test_delete_feature_override(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """delete_feature_override should remove override."""
        override = FeatureOverride(
            org_id=sample_org.org_id,
            feature_key="api_access",
            enabled=True,
        )
        await store.save_feature_override(override)
        await store.delete_feature_override(sample_org.org_id, "api_access")

        result = await store.get_feature_overrides(sample_org.org_id)
        assert len(result) == 0

    # Reset test

    @pytest.mark.asyncio
    async def test_reset_clears_all_data(
        self, store: InMemoryBillingStore, sample_org: Organization
    ):
        """reset should clear all stored data."""
        await store.save_organization(sample_org)

        store.reset()

        result = await store.get_organization(sample_org.org_id)
        assert result is None


class TestFakeStripeClient:
    """Tests for FakeStripeClient class."""

    @pytest.fixture
    def client(self) -> FakeStripeClient:
        """Create a fresh client instance."""
        return FakeStripeClient()

    def test_create_customer(self, client: FakeStripeClient):
        """create_customer should return customer with ID."""
        customer = client.create_customer(
            email="test@example.com",
            name="Test Customer",
            metadata={"org_id": "123"},
        )

        assert customer.id.startswith("cus_")
        assert customer.email == "test@example.com"
        assert customer.name == "Test Customer"

    def test_get_customer(self, client: FakeStripeClient):
        """get_customer should return created customer."""
        customer = client.create_customer(email="test@example.com")

        result = client.get_customer(customer.id)

        assert result.id == customer.id

    def test_get_customer_not_found(self, client: FakeStripeClient):
        """get_customer should return None for nonexistent customer."""
        result = client.get_customer("cus_nonexistent")
        assert result is None

    def test_update_customer(self, client: FakeStripeClient):
        """update_customer should update customer fields."""
        customer = client.create_customer(email="old@example.com")

        client.update_customer(customer.id, email="new@example.com")

        result = client.get_customer(customer.id)
        assert result.email == "new@example.com"

    def test_create_checkout_session(self, client: FakeStripeClient):
        """create_checkout_session should return session with URL."""
        session = client.create_checkout_session(
            customer="cus_123",
            metadata={"plan": "team"},
        )

        assert session.id.startswith("cs_")
        assert "checkout.stripe.com" in session.url

    def test_create_billing_portal_session(self, client: FakeStripeClient):
        """create_billing_portal_session should return session with URL."""
        session = client.create_billing_portal_session(customer="cus_123")

        assert session.id.startswith("bps_")
        assert "billing.stripe.com" in session.url

    def test_create_subscription(self, client: FakeStripeClient):
        """create_subscription should return subscription."""
        sub = client.create_subscription(
            customer="cus_123",
            items=[{"price": "price_123"}],
        )

        assert sub.id.startswith("sub_")
        assert sub.status == "active"
        assert sub.customer == "cus_123"

    def test_create_subscription_with_trial(self, client: FakeStripeClient):
        """create_subscription should handle trial period."""
        sub = client.create_subscription(
            customer="cus_123",
            items=[{"price": "price_123"}],
            trial_period_days=14,
        )

        assert sub.status == "trialing"
        assert sub.trial_start is not None
        assert sub.trial_end is not None

    def test_get_subscription(self, client: FakeStripeClient):
        """get_subscription should return created subscription."""
        sub = client.create_subscription(customer="cus_123", items=[])

        result = client.get_subscription(sub.id)

        assert result.id == sub.id

    def test_update_subscription(self, client: FakeStripeClient):
        """update_subscription should update fields."""
        sub = client.create_subscription(customer="cus_123", items=[])

        client.update_subscription(sub.id, cancel_at_period_end=True)

        result = client.get_subscription(sub.id)
        assert result.cancel_at_period_end is True

    def test_cancel_subscription(self, client: FakeStripeClient):
        """cancel_subscription should set status to canceled."""
        sub = client.create_subscription(customer="cus_123", items=[])

        client.cancel_subscription(sub.id)

        result = client.get_subscription(sub.id)
        assert result.status == "canceled"
        assert result.canceled_at is not None

    def test_list_invoices(self, client: FakeStripeClient):
        """list_invoices should return empty list."""
        result = client.list_invoices(customer="cus_123")
        assert result.data == []

    def test_list_payment_methods(self, client: FakeStripeClient):
        """list_payment_methods should return empty list."""
        result = client.list_payment_methods(customer="cus_123", type="card")
        assert result.data == []

    def test_construct_webhook_event(self, client: FakeStripeClient):
        """construct_webhook_event should parse payload."""
        import json

        payload = json.dumps({
            "type": "customer.subscription.created",
            "data": {"object": {"id": "sub_123"}},
        }).encode()

        event = client.construct_webhook_event(payload, "sig", "secret")

        assert event.type == "customer.subscription.created"
        assert event.data.object.id == "sub_123"

    def test_create_meter_event(self, client: FakeStripeClient):
        """create_meter_event should record event."""
        event = client.create_meter_event(
            customer="cus_123",
            event_name="ai_investigation",
            amount=1,
            idempotency_key="key_123",
        )

        assert event.id.startswith("mtre_")
        assert len(client.events) == 1
        assert client.events[0]["customer_id"] == "cus_123"

    def test_reset_clears_all_data(self, client: FakeStripeClient):
        """reset should clear all stored data."""
        client.create_customer(email="test@example.com")
        client.create_meter_event("cus_1", "event", 1, "key")

        client.reset()

        assert len(client._customers) == 0
        assert len(client.events) == 0
        assert client._counter == 0
