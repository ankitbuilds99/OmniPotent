"""Tests for billing feature gate module."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from app.billing.models import (
    PricingPlan,
    Subscription,
    SubscriptionStatus,
    FeatureOverride,
    UsageSummary,
    MetricType,
    PlanTier,
    PlanFeatures,
)
from app.billing.feature_gate import (
    FeatureCheckResult,
    UsageLimitResult,
    FeatureGateService,
    FeatureGate,
    UsageGate,
    require_feature,
    check_usage_limit,
)


class MockBillingStore:
    """Mock billing store for testing."""

    def __init__(self):
        self.subscriptions: dict = {}
        self.plans: dict = {}
        self.feature_overrides: dict = {}
        self.usage_summaries: dict = {}

    async def get_subscription(self, org_id):
        return self.subscriptions.get(org_id)

    async def get_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def get_plan_by_name(self, name):
        for plan in self.plans.values():
            if plan.name == name:
                return plan
        return None

    async def get_feature_overrides(self, org_id):
        return self.feature_overrides.get(org_id, [])

    async def get_usage_summary(self, org_id, year_month):
        key = f"{org_id}:{year_month}"
        return self.usage_summaries.get(key)


class TestFeatureCheckResult:
    """Tests for FeatureCheckResult dataclass."""

    def test_default_values(self):
        """FeatureCheckResult should have correct default values."""
        result = FeatureCheckResult(allowed=True)

        assert result.allowed is True
        assert result.reason is None
        assert result.required_plan is None
        assert result.upgrade_url is None
        assert result.current_usage is None
        assert result.limit is None
        assert result.remaining is None

    def test_with_all_fields(self):
        """FeatureCheckResult should store all fields."""
        result = FeatureCheckResult(
            allowed=False,
            reason="Upgrade required",
            required_plan="team",
            upgrade_url="/upgrade",
            current_usage=10,
            limit=5,
            remaining=0,
        )

        assert result.allowed is False
        assert result.reason == "Upgrade required"
        assert result.required_plan == "team"


class TestUsageLimitResult:
    """Tests for UsageLimitResult dataclass."""

    def test_default_values(self):
        """UsageLimitResult should have correct default values."""
        result = UsageLimitResult(within_limit=True)

        assert result.within_limit is True
        assert result.reason is None
        assert result.current_usage == 0
        assert result.limit is None
        assert result.remaining is None


class TestFeatureGateService:
    """Tests for FeatureGateService class."""

    @pytest.fixture
    def store(self) -> MockBillingStore:
        """Create a mock store."""
        return MockBillingStore()

    @pytest.fixture
    def service(self, store: MockBillingStore) -> FeatureGateService:
        """Create a FeatureGateService instance."""
        return FeatureGateService(store)

    @pytest.fixture
    def team_plan(self) -> PricingPlan:
        """Create a team plan."""
        return PricingPlan(
            plan_id=uuid4(),
            name="team",
            display_name="Team",
            tier=PlanTier.TEAM,
            max_users=10,
            max_ai_investigations_monthly=500,
            max_alerts_monthly=5000,
            features=PlanFeatures(
                ai_investigation=True,
                multi_llm=True,
                auto_fix_pr=True,
                api_access=False,
            ),
        )

    @pytest.fixture
    def active_subscription(self, team_plan: PricingPlan) -> Subscription:
        """Create an active subscription."""
        return Subscription(
            subscription_id=uuid4(),
            org_id=uuid4(),
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )

    @pytest.mark.asyncio
    async def test_get_organization_plan_no_subscription(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """get_organization_plan should return community plan for no subscription."""
        org_id = uuid4()

        plan = await service.get_organization_plan(org_id)

        assert plan is not None
        assert plan.name == "starter"

    @pytest.mark.asyncio
    async def test_get_organization_plan_inactive_subscription(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """get_organization_plan should return community plan for inactive subscription."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.CANCELED,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        plan = await service.get_organization_plan(org_id)

        assert plan.name == "starter"

    @pytest.mark.asyncio
    async def test_get_organization_plan_active_subscription(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """get_organization_plan should return plan for active subscription."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        plan = await service.get_organization_plan(org_id)

        assert plan.name == "team"

    @pytest.mark.asyncio
    async def test_can_access_feature_with_override_enabled(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """can_access_feature should check overrides first."""
        org_id = uuid4()
        override = FeatureOverride(
            org_id=org_id,
            feature_key="api_access",
            enabled=True,
        )
        store.feature_overrides[org_id] = [override]

        result = await service.can_access_feature(org_id, "api_access")

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_can_access_feature_with_override_disabled(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """can_access_feature should respect disabled overrides."""
        org_id = uuid4()
        override = FeatureOverride(
            org_id=org_id,
            feature_key="ai_investigation",
            enabled=False,
        )
        store.feature_overrides[org_id] = [override]

        result = await service.can_access_feature(org_id, "ai_investigation")

        assert result.allowed is False
        assert "disabled by administrator" in result.reason

    @pytest.mark.asyncio
    async def test_can_access_feature_with_expired_override(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """can_access_feature should ignore expired overrides."""
        org_id = uuid4()
        override = FeatureOverride(
            org_id=org_id,
            feature_key="api_access",
            enabled=True,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        store.feature_overrides[org_id] = [override]

        result = await service.can_access_feature(org_id, "api_access")

        # Should fall back to plan check, not override
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_can_access_feature_plan_has_feature(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """can_access_feature should allow if plan has feature."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        result = await service.can_access_feature(org_id, "multi_llm")

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_can_access_feature_plan_missing_feature(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """can_access_feature should deny if plan lacks feature."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        result = await service.can_access_feature(org_id, "sso_saml")

        assert result.allowed is False
        assert "Upgrade" in result.reason
        assert result.required_plan is not None
        assert result.upgrade_url is not None

    @pytest.mark.asyncio
    async def test_can_access_feature_unknown_feature(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """can_access_feature should deny unknown features."""
        org_id = uuid4()

        result = await service.can_access_feature(org_id, "unknown_feature")

        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_check_usage_limit_unlimited(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
    ):
        """check_usage_limit should allow unlimited metrics."""
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="growth",
            tier=PlanTier.GROWTH,
            max_ai_investigations_monthly=None,  # Unlimited
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        result = await service.check_usage_limit(org_id, MetricType.AI_INVESTIGATION)

        assert result.within_limit is True
        assert result.limit is None

    @pytest.mark.asyncio
    async def test_check_usage_limit_within_limit(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """check_usage_limit should allow within limit usage."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=100,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        result = await service.check_usage_limit(org_id, MetricType.AI_INVESTIGATION)

        assert result.within_limit is True
        assert result.current_usage == 100
        assert result.limit == 500
        assert result.remaining == 399  # 500 - 100 - 1 (increment)

    @pytest.mark.asyncio
    async def test_check_usage_limit_exceeded(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """check_usage_limit should deny when limit exceeded."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=500,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        result = await service.check_usage_limit(org_id, MetricType.AI_INVESTIGATION)

        assert result.within_limit is False
        assert "limit reached" in result.reason
        assert result.remaining == 0

    @pytest.mark.asyncio
    async def test_check_usage_limit_with_increment(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """check_usage_limit should consider increment."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=495,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        # Request 10, would exceed limit
        result = await service.check_usage_limit(
            org_id, MetricType.AI_INVESTIGATION, increment=10
        )

        assert result.within_limit is False

    @pytest.mark.asyncio
    async def test_check_usage_limit_alert_processed(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """check_usage_limit should work for alert_processed metric."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            alerts_processed_count=1000,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        result = await service.check_usage_limit(org_id, MetricType.ALERT_PROCESSED)

        assert result.within_limit is True
        assert result.current_usage == 1000
        assert result.limit == 5000

    @pytest.mark.asyncio
    async def test_get_organization_limits(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """get_organization_limits should return all limits."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=50,
            alerts_processed_count=100,
            active_users_count=5,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        limits = await service.get_organization_limits(org_id)

        assert "plan" in limits
        assert limits["plan"]["name"] == "team"
        assert "limits" in limits
        assert limits["limits"]["users"]["used"] == 5
        assert limits["limits"]["ai_investigations"]["used"] == 50
        assert "features" in limits

    @pytest.mark.asyncio
    async def test_get_upgrade_prompt(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """get_upgrade_prompt should return upgrade info."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        prompt = await service.get_upgrade_prompt(org_id, "sso_saml")

        assert prompt["feature"] == "sso_saml"
        assert prompt["current_plan"] == "team"
        assert prompt["required_plan"] is not None
        assert "upgrade_url" in prompt

    @pytest.mark.asyncio
    async def test_get_upgrade_prompt_unknown_feature(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """get_upgrade_prompt should handle unknown feature."""
        org_id = uuid4()

        prompt = await service.get_upgrade_prompt(org_id, "unknown_feature")

        assert "error" in prompt

    @pytest.mark.asyncio
    async def test_get_feature_status(
        self,
        service: FeatureGateService,
        store: MockBillingStore,
        team_plan: PricingPlan,
    ):
        """get_feature_status should return status of features."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[team_plan.plan_id] = team_plan

        status = await service.get_feature_status(
            org_id, ["multi_llm", "sso_saml"]
        )

        assert "multi_llm" in status
        assert status["multi_llm"]["enabled"] is True
        assert status["multi_llm"]["source"] == "plan"

        assert "sso_saml" in status
        assert status["sso_saml"]["enabled"] is False
        assert status["sso_saml"]["required_plan"] is not None

    @pytest.mark.asyncio
    async def test_get_feature_status_with_override(
        self, service: FeatureGateService, store: MockBillingStore
    ):
        """get_feature_status should show override status."""
        org_id = uuid4()
        override = FeatureOverride(
            org_id=org_id,
            feature_key="api_access",
            enabled=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        store.feature_overrides[org_id] = [override]

        status = await service.get_feature_status(org_id, ["api_access"])

        assert status["api_access"]["enabled"] is True
        assert status["api_access"]["source"] == "override"
        assert status["api_access"]["expires_at"] is not None


class TestRequireFeatureDecorator:
    """Tests for require_feature decorator."""

    @pytest.mark.asyncio
    async def test_require_feature_missing_dependencies(self):
        """require_feature should raise for missing dependencies."""

        @require_feature("api_access")
        async def test_func():
            return "success"

        with pytest.raises(PermissionError, match="Missing dependencies"):
            await test_func()

    @pytest.mark.asyncio
    async def test_require_feature_allowed(self):
        """require_feature should allow access when feature is available."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="team",
            tier=PlanTier.TEAM,
            features=PlanFeatures(api_access=True),
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        mock_org = MagicMock()
        mock_org.org_id = org_id

        @require_feature("api_access")
        async def test_func(db=None, current_org=None):
            return "success"

        result = await test_func(db=store, current_org=mock_org)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_require_feature_denied(self):
        """require_feature should deny access when feature is not available."""
        store = MockBillingStore()
        org_id = uuid4()

        mock_org = MagicMock()
        mock_org.org_id = org_id

        @require_feature("api_access")
        async def test_func(db=None, current_org=None):
            return "success"

        with pytest.raises(PermissionError, match="Feature not available"):
            await test_func(db=store, current_org=mock_org)


class TestCheckUsageLimitDecorator:
    """Tests for check_usage_limit decorator."""

    @pytest.mark.asyncio
    async def test_check_usage_limit_missing_dependencies(self):
        """check_usage_limit should raise for missing dependencies."""

        @check_usage_limit(MetricType.AI_INVESTIGATION)
        async def test_func():
            return "success"

        with pytest.raises(PermissionError, match="Missing dependencies"):
            await test_func()

    @pytest.mark.asyncio
    async def test_check_usage_limit_within_limit(self):
        """check_usage_limit should allow when within limit."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="team",
            tier=PlanTier.TEAM,
            max_ai_investigations_monthly=500,
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        mock_org = MagicMock()
        mock_org.org_id = org_id

        @check_usage_limit(MetricType.AI_INVESTIGATION)
        async def test_func(db=None, current_org=None):
            return "success"

        result = await test_func(db=store, current_org=mock_org)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_check_usage_limit_exceeded(self):
        """check_usage_limit should deny when limit exceeded."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="starter",
            tier=PlanTier.STARTER,
            max_ai_investigations_monthly=25,
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=25,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        mock_org = MagicMock()
        mock_org.org_id = org_id

        @check_usage_limit(MetricType.AI_INVESTIGATION)
        async def test_func(db=None, current_org=None):
            return "success"

        with pytest.raises(PermissionError, match="Usage limit exceeded"):
            await test_func(db=store, current_org=mock_org)


class TestFeatureGateContextManager:
    """Tests for FeatureGate context manager."""

    @pytest.mark.asyncio
    async def test_feature_gate_allowed(self):
        """FeatureGate should allow when feature is available."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="team",
            tier=PlanTier.TEAM,
            features=PlanFeatures(api_access=True),
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        async with FeatureGate(store, org_id, "api_access") as result:
            assert result.allowed is True

    @pytest.mark.asyncio
    async def test_feature_gate_denied(self):
        """FeatureGate should raise when feature is not available."""
        store = MockBillingStore()
        org_id = uuid4()

        with pytest.raises(PermissionError, match="Feature not available"):
            async with FeatureGate(store, org_id, "api_access"):
                pass


class TestUsageGateContextManager:
    """Tests for UsageGate context manager."""

    @pytest.mark.asyncio
    async def test_usage_gate_within_limit(self):
        """UsageGate should allow when within limit."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="team",
            tier=PlanTier.TEAM,
            max_ai_investigations_monthly=500,
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        async with UsageGate(store, org_id, MetricType.AI_INVESTIGATION) as result:
            assert result.within_limit is True

    @pytest.mark.asyncio
    async def test_usage_gate_exceeded(self):
        """UsageGate should raise when limit exceeded."""
        store = MockBillingStore()
        org_id = uuid4()
        plan = PricingPlan(
            plan_id=uuid4(),
            name="starter",
            tier=PlanTier.STARTER,
            max_ai_investigations_monthly=25,
        )
        sub = Subscription(
            org_id=org_id,
            plan_id=plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        store.subscriptions[org_id] = sub
        store.plans[plan.plan_id] = plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=25,
        )
        store.usage_summaries[f"{org_id}:{year_month}"] = summary

        with pytest.raises(PermissionError, match="Usage limit exceeded"):
            async with UsageGate(store, org_id, MetricType.AI_INVESTIGATION):
                pass
