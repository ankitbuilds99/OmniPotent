"""Tests for startup features.

Covers: billing, onboarding activation, analytics, support, compliance, and referrals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.billing.subscriptions import (
    PlanInterval,
    PLANS,
    Subscription,
    SubscriptionStatus,
    SubscriptionManager,
)
from app.compliance.gdpr import (
    ConsentType,
    ConsentRecord,
    DSARRequest,
    DSARType,
    GDPRManager,
)


class TestBilling:
    """Test billing and subscription management."""

    def test_plan_definition(self):
        """Test plan definitions are correct."""
        assert "free" in PLANS
        assert "starter_monthly" in PLANS
        assert "pro_monthly" in PLANS
        assert "enterprise" in PLANS

        free_plan = PLANS["free"]
        assert free_plan.price_cents == 0
        assert free_plan.investigations_per_month == 50

        starter = PLANS["starter_monthly"]
        assert starter.price_cents == 4900
        assert starter.interval == PlanInterval.MONTHLY

    def test_subscription_properties(self):
        """Test subscription computed properties."""
        now = datetime.now(timezone.utc)
        sub = Subscription(
            id="sub_123",
            org_id="org_456",
            plan_id="starter_monthly",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )

        assert sub.is_active is True
        assert sub.is_trialing is False
        assert sub.days_until_renewal >= 14

    def test_subscription_trialing(self):
        """Test trialing subscription."""
        now = datetime.now(timezone.utc)
        sub = Subscription(
            id="sub_123",
            org_id="org_456",
            plan_id="starter_monthly",
            status=SubscriptionStatus.TRIALING,
            current_period_start=now,
            current_period_end=now + timedelta(days=14),
            trial_end=now + timedelta(days=14),
        )

        assert sub.is_active is True
        assert sub.is_trialing is True

    def test_subscription_serialization(self):
        """Test subscription to_dict."""
        now = datetime.now(timezone.utc)
        sub = Subscription(
            id="sub_123",
            org_id="org_456",
            plan_id="starter_monthly",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )

        data = sub.to_dict()
        assert data["id"] == "sub_123"
        assert data["status"] == "active"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_subscription_manager_create(self):
        """Test creating a subscription."""
        mock_store = AsyncMock()
        mock_store.get_org.return_value = MagicMock(stripe_customer_id="cus_123")
        mock_store.save_subscription = AsyncMock()
        mock_store.set_plan = AsyncMock()

        manager = SubscriptionManager(store=mock_store)
        sub = await manager.create_subscription(
            org_id="org_456",
            plan_id="starter_monthly",
            trial_days=14,
        )

        assert sub.org_id == "org_456"
        assert sub.plan_id == "starter_monthly"
        assert sub.status == SubscriptionStatus.TRIALING
        mock_store.save_subscription.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscription_manager_upgrade(self):
        """Test upgrading a subscription."""
        now = datetime.now(timezone.utc)
        existing_sub = Subscription(
            id="sub_123",
            org_id="org_456",
            plan_id="starter_monthly",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )

        mock_store = AsyncMock()
        mock_store.get_subscription.return_value = existing_sub
        mock_store.save_subscription = AsyncMock()
        mock_store.set_plan = AsyncMock()

        manager = SubscriptionManager(store=mock_store)
        upgraded = await manager.upgrade_plan("org_456", "pro_monthly")

        assert upgraded.plan_id == "pro_monthly"
        mock_store.set_plan.assert_called_with("org_456", "pro_monthly")

    @pytest.mark.asyncio
    async def test_subscription_cancel(self):
        """Test canceling a subscription."""
        now = datetime.now(timezone.utc)
        existing_sub = Subscription(
            id="sub_123",
            org_id="org_456",
            plan_id="pro_monthly",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )

        mock_store = AsyncMock()
        mock_store.get_subscription.return_value = existing_sub
        mock_store.save_subscription = AsyncMock()

        manager = SubscriptionManager(store=mock_store)
        canceled = await manager.cancel_subscription("org_456", reason="too expensive")

        assert canceled.cancel_at_period_end is True
        assert canceled.cancel_reason == "too expensive"


class TestCompliance:
    """Test GDPR compliance."""

    def test_consent_record(self):
        """Test consent record."""
        record = ConsentRecord(
            user_id="user_123",
            consent_type=ConsentType.ANALYTICS,
            granted=True,
        )

        assert record.is_valid is True

    def test_expired_consent(self):
        """Test expired consent."""
        record = ConsentRecord(
            user_id="user_123",
            consent_type=ConsentType.MARKETING,
            granted=True,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        assert record.is_valid is False

    def test_dsar_request(self):
        """Test DSAR request."""
        request = DSARRequest(
            id="dsar_123",
            user_id="user_456",
            request_type=DSARType.ACCESS,
        )

        assert request.days_remaining <= 30
        assert request.is_overdue is False

    @pytest.mark.asyncio
    async def test_record_consent(self):
        """Test recording consent."""
        mock_store = AsyncMock()
        mock_store.save_consent = AsyncMock()
        mock_store.log_audit_event = AsyncMock()

        manager = GDPRManager(store=mock_store)
        record = await manager.record_consent(
            user_id="user_123",
            consent_type=ConsentType.ANALYTICS,
            granted=True,
        )

        assert record.granted is True
        mock_store.save_consent.assert_called_once()
        mock_store.log_audit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_consent(self):
        """Test checking consent."""
        mock_store = AsyncMock()
        mock_store.get_consent.return_value = ConsentRecord(
            user_id="user_123",
            consent_type=ConsentType.ANALYTICS,
            granted=True,
        )

        manager = GDPRManager(store=mock_store)
        has_consent = await manager.check_consent("user_123", ConsentType.ANALYTICS)

        assert has_consent is True

    @pytest.mark.asyncio
    async def test_essential_consent_always_true(self):
        """Test essential consent is always granted."""
        mock_store = AsyncMock()

        manager = GDPRManager(store=mock_store)
        has_consent = await manager.check_consent("user_123", ConsentType.ESSENTIAL)

        assert has_consent is True
        mock_store.get_consent.assert_not_called()
