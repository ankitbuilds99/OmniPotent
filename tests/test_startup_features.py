"""Tests for startup features.

Covers: billing, onboarding activation, analytics, support, compliance, and referrals.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.billing.subscriptions import (
    PlanInterval,
    PLANS,
    Subscription,
    SubscriptionStatus,
    SubscriptionManager,
)
from app.onboarding.flow import (
    ActivationMilestone,
    ActivationState,
    ActivationTracker,
    MILESTONE_CONFIG,
)
from app.analytics.product_metrics import (
    RevenueMetrics,
    CohortAnalysis,
    CohortRetention,
    UsageMetrics,
)
from app.support.help_system import (
    Ticket,
    TicketStatus,
    TicketPriority,
    KnowledgeArticle,
    SupportManager,
)
from app.compliance.gdpr import (
    ConsentType,
    ConsentRecord,
    DSARRequest,
    DSARType,
    GDPRManager,
)
from app.growth.referral import (
    ReferralLink,
    ReferralManager,
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


class TestActivation:
    """Test activation tracking."""

    def test_milestone_config(self):
        """Test milestone configuration."""
        assert ActivationMilestone.SIGNUP in MILESTONE_CONFIG
        assert ActivationMilestone.FIRST_INVESTIGATION in MILESTONE_CONFIG

        signup_cfg = MILESTONE_CONFIG[ActivationMilestone.SIGNUP]
        assert signup_cfg["required"] is True
        assert signup_cfg["points"] == 10

    def test_activation_state(self):
        """Test activation state properties."""
        state = ActivationState(
            user_id="user_123",
            org_id="org_456",
            completed_milestones={
                ActivationMilestone.SIGNUP.value: datetime.now(timezone.utc),
                ActivationMilestone.FIRST_LOGIN.value: datetime.now(timezone.utc),
            },
            activation_score=20,
        )

        assert state.completion_percentage > 0
        assert state.is_activated is False

    @pytest.mark.asyncio
    async def test_tracker_start(self):
        """Test starting activation tracking."""
        mock_store = AsyncMock()
        mock_store.save_activation = AsyncMock()

        tracker = ActivationTracker(store=mock_store)
        state = await tracker.start_activation("user_123", "org_456")

        assert state.user_id == "user_123"
        assert ActivationMilestone.SIGNUP.value in state.completed_milestones
        assert state.activation_score == 10
        mock_store.save_activation.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_milestone(self):
        """Test completing a milestone."""
        initial_state = ActivationState(
            user_id="user_123",
            org_id="org_456",
            completed_milestones={
                ActivationMilestone.SIGNUP.value: datetime.now(timezone.utc),
            },
            activation_score=10,
        )

        mock_store = AsyncMock()
        mock_store.get_activation.return_value = initial_state
        mock_store.save_activation = AsyncMock()

        tracker = ActivationTracker(store=mock_store)
        state = await tracker.complete_milestone(
            "user_123", ActivationMilestone.FIRST_LOGIN
        )

        assert ActivationMilestone.FIRST_LOGIN.value in state.completed_milestones
        assert state.activation_score == 20

    @pytest.mark.asyncio
    async def test_get_progress(self):
        """Test getting activation progress."""
        initial_state = ActivationState(
            user_id="user_123",
            org_id="org_456",
            completed_milestones={
                ActivationMilestone.SIGNUP.value: datetime.now(timezone.utc),
            },
            activation_score=10,
        )

        mock_store = AsyncMock()
        mock_store.get_activation.return_value = initial_state

        tracker = ActivationTracker(store=mock_store)
        progress = await tracker.get_progress("user_123")

        assert "milestones" in progress
        assert progress["score"] == 10
        assert progress["is_activated"] is False


class TestProductMetrics:
    """Test product analytics metrics."""

    def test_revenue_metrics(self):
        """Test revenue metrics calculations."""
        metrics = RevenueMetrics(
            period=date.today(),
            mrr_cents=100000,
            new_mrr_cents=10000,
            expansion_mrr_cents=5000,
            contraction_mrr_cents=2000,
            churned_mrr_cents=3000,
            net_new_mrr_cents=10000,
            paying_customers=20,
            arpu_cents=5000,
        )

        assert metrics.arr_cents == 0
        assert metrics.mrr_growth_rate == 0.1
        data = metrics.to_dict()
        assert data["mrr"] == 1000.0
        assert data["paying_customers"] == 20

    def test_cohort_retention(self):
        """Test cohort retention data."""
        cohort = CohortRetention(
            cohort_month=date(2024, 1, 1),
            cohort_size=100,
            retention_by_month={0: 1.0, 1: 0.8, 2: 0.7, 3: 0.65},
        )

        assert cohort.get_retention(1) == 0.8
        assert cohort.get_retention(99) == 0.0

    def test_cohort_analysis(self):
        """Test cohort analysis aggregation."""
        analysis = CohortAnalysis()

        cohort1 = CohortRetention(
            cohort_month=date(2024, 1, 1),
            cohort_size=100,
            retention_by_month={0: 1.0, 1: 0.8},
        )
        cohort2 = CohortRetention(
            cohort_month=date(2024, 2, 1),
            cohort_size=150,
            retention_by_month={0: 1.0, 1: 0.9},
        )

        analysis.add_cohort(cohort1)
        analysis.add_cohort(cohort2)

        assert len(analysis.cohorts) == 2
        assert abs(analysis.average_retention[1] - 0.85) < 0.001

    def test_usage_metrics(self):
        """Test usage metrics."""
        metrics = UsageMetrics(
            period=date.today(),
            daily_active_users=100,
            monthly_active_users=500,
            investigations_count=1000,
        )

        assert metrics.dau_mau_ratio == 0.2
        data = metrics.to_dict()
        assert data["dau"] == 100


class TestSupport:
    """Test support ticket system."""

    def test_ticket_creation(self):
        """Test ticket properties."""
        ticket = Ticket(
            id="tkt_123",
            org_id="org_456",
            user_id="user_789",
            subject="Help needed",
            description="Something is broken",
            priority=TicketPriority.HIGH,
        )

        assert ticket.is_open is True
        assert ticket.is_sla_breached is False
        assert ticket.response_time_minutes is None

    def test_ticket_sla_calculation(self):
        """Test SLA calculations."""
        ticket = Ticket(
            id="tkt_123",
            org_id="org_456",
            user_id="user_789",
            subject="Urgent issue",
            description="Production is down",
            priority=TicketPriority.URGENT,
        )

        assert ticket.sla_response_due > ticket.created_at
        response_deadline = ticket.sla_response_due - ticket.created_at
        assert response_deadline.total_seconds() / 60 == 60

    def test_knowledge_article(self):
        """Test knowledge article."""
        article = KnowledgeArticle(
            id="kb_123",
            title="How to configure alerts",
            content="Step 1: ...",
            helpful_count=10,
            not_helpful_count=2,
        )

        assert article.helpfulness_score == 10 / 12

    @pytest.mark.asyncio
    async def test_support_manager_create_ticket(self):
        """Test creating a support ticket."""
        mock_store = AsyncMock()
        mock_store.create_ticket = AsyncMock()

        manager = SupportManager(store=mock_store)
        ticket = await manager.create_ticket(
            org_id="org_456",
            user_id="user_789",
            subject="Production down",
            description="Everything is broken",
        )

        assert ticket.priority == TicketPriority.URGENT
        mock_store.create_ticket.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_ticket(self):
        """Test resolving a ticket."""
        existing = Ticket(
            id="tkt_123",
            org_id="org_456",
            user_id="user_789",
            subject="Issue",
            description="Description",
            status=TicketStatus.IN_PROGRESS,
        )

        mock_store = AsyncMock()
        mock_store.get_ticket.return_value = existing
        mock_store.update_ticket = AsyncMock()

        manager = SupportManager(store=mock_store)
        resolved = await manager.resolve_ticket("tkt_123", "Fixed the config")

        assert resolved.status == TicketStatus.RESOLVED
        assert resolved.resolved_at is not None


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


class TestReferral:
    """Test referral system."""

    def test_referral_link(self):
        """Test referral link properties."""
        link = ReferralLink(
            id="ref_abc",
            code="ABC123",
            referrer_id="user_123",
            referrer_org_id="org_456",
            clicks=100,
            signups=20,
            conversions=5,
        )

        assert link.signup_rate == 0.2
        assert link.conversion_rate == 0.25
        assert link.is_active is True

    def test_expired_referral_link(self):
        """Test expired referral link."""
        link = ReferralLink(
            id="ref_abc",
            code="ABC123",
            referrer_id="user_123",
            referrer_org_id="org_456",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        assert link.is_active is False

    @pytest.mark.asyncio
    async def test_create_referral_link(self):
        """Test creating a referral link."""
        mock_store = AsyncMock()
        mock_store.create_link = AsyncMock()

        manager = ReferralManager(store=mock_store)
        link = await manager.create_referral_link(
            user_id="user_123",
            org_id="org_456",
        )

        assert link.referrer_id == "user_123"
        assert len(link.code) > 0
        mock_store.create_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_click(self):
        """Test tracking a referral click."""
        existing_link = ReferralLink(
            id="ref_abc",
            code="ABC123",
            referrer_id="user_123",
            referrer_org_id="org_456",
            clicks=5,
        )

        mock_store = AsyncMock()
        mock_store.get_link_by_code.return_value = existing_link
        mock_store.update_link = AsyncMock()

        manager = ReferralManager(store=mock_store)
        link = await manager.track_click("ABC123")

        assert link.clicks == 6
        mock_store.update_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_calculate_viral_coefficient(self):
        """Test viral coefficient calculation."""
        mock_store = AsyncMock()
        mock_store.get_total_users.return_value = 1000
        mock_store.get_referred_users_count.return_value = 100
        mock_store.get_referrers_count.return_value = 50

        manager = ReferralManager(store=mock_store)
        k = await manager.calculate_viral_coefficient(days=30)

        assert k["total_users"] == 1000
        assert k["referred_users"] == 100
        assert k["invites_per_user"] == 2.0
        assert k["k_factor"] == 0.2
