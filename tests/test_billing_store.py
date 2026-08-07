"""Tests for billing store module."""
from __future__ import annotations
import pytest
from uuid import uuid4
from app.billing.models import (
    Organization, Subscription, Invoice, PaymentMethod,
    UsageRecord, UsageSummary, FeatureOverride, MetricType, InvoiceStatus,
)
from app.billing.store import InMemoryBillingStore, FakeStripeClient

class TestInMemoryBillingStore:
    @pytest.fixture
    def store(self):
        return InMemoryBillingStore()

    @pytest.fixture
    def sample_org(self):
        return Organization(org_id=uuid4(), name="Test Org", slug="test-org")

    @pytest.mark.asyncio
    async def test_get_organization_not_found(self, store):
        assert await store.get_organization(uuid4()) is None

    @pytest.mark.asyncio
    async def test_save_and_get_organization(self, store, sample_org):
        await store.save_organization(sample_org)
        result = await store.get_organization(sample_org.org_id)
        assert result is not None
        assert result.name == "Test Org"

    @pytest.mark.asyncio
    async def test_get_subscription_not_found(self, store):
        assert await store.get_subscription(uuid4()) is None

    @pytest.mark.asyncio
    async def test_save_and_get_subscription(self, store, sample_org):
        sub = Subscription(org_id=sample_org.org_id, plan_id=uuid4(), stripe_subscription_id="sub_123")
        await store.save_subscription(sub)
        result = await store.get_subscription(sample_org.org_id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_subscription_by_stripe_id(self, store, sample_org):
        sub = Subscription(org_id=sample_org.org_id, plan_id=uuid4(), stripe_subscription_id="sub_123")
        await store.save_subscription(sub)
        result = await store.get_subscription_by_stripe_id("sub_123")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_plan_by_name(self, store):
        result = await store.get_plan_by_name("team")
        assert result is not None
        assert result.name == "team"

    @pytest.mark.asyncio
    async def test_get_all_plans(self, store):
        result = await store.get_all_plans()
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_save_and_get_invoices(self, store, sample_org):
        invoice = Invoice(invoice_id=uuid4(), org_id=sample_org.org_id, status=InvoiceStatus.PAID)
        await store.save_invoice(invoice)
        result = await store.get_invoices(sample_org.org_id)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_payment_methods_empty(self, store, sample_org):
        result = await store.get_payment_methods(sample_org.org_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_save_payment_method(self, store, sample_org):
        pm = PaymentMethod(org_id=sample_org.org_id, card_last4="4242")
        await store.save_payment_method(pm)
        result = await store.get_payment_methods(sample_org.org_id)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_create_usage_record(self, store, sample_org):
        record = UsageRecord(org_id=sample_org.org_id, metric_type=MetricType.AI_INVESTIGATION)
        await store.create_usage_record(record)
        # Record is stored

    @pytest.mark.asyncio
    async def test_get_usage_summary_not_found(self, store, sample_org):
        assert await store.get_usage_summary(sample_org.org_id, "2024-01") is None

    @pytest.mark.asyncio
    async def test_upsert_usage_summary(self, store, sample_org):
        summary = UsageSummary(org_id=sample_org.org_id, year_month="2024-01", ai_investigations_count=10)
        await store.upsert_usage_summary(summary)
        result = await store.get_usage_summary(sample_org.org_id, "2024-01")
        assert result.ai_investigations_count == 10

    @pytest.mark.asyncio
    async def test_increment_summary_field(self, store, sample_org):
        await store.increment_summary_field(sample_org.org_id, "2024-01", "ai_investigations_count", 5)
        result = await store.get_usage_summary(sample_org.org_id, "2024-01")
        assert result.ai_investigations_count == 5

    @pytest.mark.asyncio
    async def test_get_feature_overrides_empty(self, store, sample_org):
        assert await store.get_feature_overrides(sample_org.org_id) == []

    @pytest.mark.asyncio
    async def test_save_feature_override(self, store, sample_org):
        override = FeatureOverride(org_id=sample_org.org_id, feature_key="api_access", enabled=True)
        await store.save_feature_override(override)
        result = await store.get_feature_overrides(sample_org.org_id)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_reset_clears_all_data(self, store, sample_org):
        await store.save_organization(sample_org)
        store.reset()
        assert await store.get_organization(sample_org.org_id) is None

class TestFakeStripeClient:
    @pytest.fixture
    def client(self):
        return FakeStripeClient()

    def test_create_customer(self, client):
        customer = client.create_customer(email="test@test.com")
        assert customer.id.startswith("cus_")

    def test_get_customer(self, client):
        customer = client.create_customer(email="test@test.com")
        result = client.get_customer(customer.id)
        assert result.id == customer.id

    def test_create_checkout_session(self, client):
        session = client.create_checkout_session(customer="cus_123")
        assert session.id.startswith("cs_")

    def test_create_billing_portal_session(self, client):
        session = client.create_billing_portal_session(customer="cus_123")
        assert session.id.startswith("bps_")

    def test_create_subscription_with_price(self, client):
        sub = client.create_subscription(customer="cus_123", items=[{"price": "price_123"}])
        assert sub.id.startswith("sub_")
        assert sub.status == "active"

    def test_list_invoices(self, client):
        result = client.list_invoices(customer="cus_123")
        assert result.data == []

    def test_construct_webhook_event(self, client):
        import json
        payload = json.dumps({"type": "test.event", "data": {"object": {"id": "123"}}}).encode()
        event = client.construct_webhook_event(payload, "sig", "secret")
        assert event.type == "test.event"

    def test_create_meter_event(self, client):
        event = client.create_meter_event("cus_123", "ai_investigation", 1, "key_123")
        assert event.id.startswith("mtre_")

    def test_reset_clears_all_data(self, client):
        client.create_customer(email="test@test.com")
        client.reset()
        assert len(client._customers) == 0
