"""Tests for billing API module."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

from app import config as cfg
from app.billing.models import (
    PricingPlan,
    Subscription,
    UsageSummary,
    SubscriptionStatus,
    BillingCycle,
    PlanTier,
    PlanFeatures,
)
from app.billing.api import (
    PlanResponse,
    SubscriptionResponse,
    UsageResponse,
    BillingAPI,
)
from app.billing.store import InMemoryBillingStore, FakeStripeClient
from app.billing.stripe_integration import StripeBillingService


@pytest.fixture
def demo_mode(monkeypatch):
    """Opt into the simulated-billing path that is off by default."""
    monkeypatch.setenv("COPILOT_DEMO_MODE", "true")
    cfg.get_settings.cache_clear()
    yield
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    cfg.get_settings.cache_clear()


class TestPlanResponse:
    """Tests for PlanResponse dataclass."""

    def test_to_dict(self):
        """to_dict should return all fields."""
        response = PlanResponse(
            name="team",
            display_name="Team",
            description="For teams",
            tier="team",
            monthly_price=299.0,
            annual_price=3048.0,
            annual_monthly_price=254.0,
            annual_savings_percent=15.0,
            max_users=10,
            max_ai_investigations_monthly=500,
            max_alerts_monthly=5000,
            features={"api_access": True},
            is_current=True,
            is_popular=True,
        )

        result = response.to_dict()

        assert result["name"] == "team"
        assert result["display_name"] == "Team"
        assert result["monthly_price"] == 299.0
        assert result["is_current"] is True
        assert result["is_popular"] is True
        assert result["features"] == {"api_access": True}


class TestSubscriptionResponse:
    """Tests for SubscriptionResponse dataclass."""

    def test_to_dict(self):
        """to_dict should return all fields including nested plan."""
        plan = PlanResponse(
            name="team",
            display_name="Team",
            description="For teams",
            tier="team",
            monthly_price=299.0,
            annual_price=3048.0,
            annual_monthly_price=254.0,
            annual_savings_percent=15.0,
            max_users=10,
            max_ai_investigations_monthly=500,
            max_alerts_monthly=5000,
            features={},
            is_current=True,
        )

        response = SubscriptionResponse(
            plan=plan,
            status="active",
            billing_cycle="monthly",
            is_trialing=False,
            days_remaining_trial=None,
            current_period_end="2024-02-01T00:00:00",
            days_until_renewal=15,
            cancel_at_period_end=False,
        )

        result = response.to_dict()

        assert "plan" in result
        assert result["plan"]["name"] == "team"
        assert result["status"] == "active"
        assert result["billing_cycle"] == "monthly"
        assert result["days_until_renewal"] == 15


class TestUsageResponse:
    """Tests for UsageResponse dataclass."""

    def test_to_dict(self):
        """to_dict should return all fields."""
        response = UsageResponse(
            plan={"name": "team", "tier": "team"},
            limits={"users": {"limit": 10, "used": 5}},
            features={"api_access": True},
        )

        result = response.to_dict()

        assert result["plan"]["name"] == "team"
        assert result["limits"]["users"]["limit"] == 10
        assert result["features"]["api_access"] is True


class TestBillingAPI:
    """Tests for BillingAPI class."""

    @pytest.fixture
    def store(self) -> InMemoryBillingStore:
        """Create a fresh store."""
        return InMemoryBillingStore()

    @pytest.fixture
    def stripe_client(self) -> FakeStripeClient:
        """Create a fake Stripe client."""
        return FakeStripeClient()

    @pytest.fixture
    def stripe_service(
        self, store: InMemoryBillingStore, stripe_client: FakeStripeClient
    ) -> StripeBillingService:
        """Create a StripeBillingService."""
        return StripeBillingService(
            store=store,
            stripe_client=stripe_client,
            webhook_secret="whsec_test",
        )

    @pytest.fixture
    def api(
        self, store: InMemoryBillingStore, stripe_service: StripeBillingService
    ) -> BillingAPI:
        """Create a BillingAPI instance."""
        return BillingAPI(store=store, stripe_service=stripe_service)

    @pytest.fixture
    def api_no_stripe(self, store: InMemoryBillingStore) -> BillingAPI:
        """Create a BillingAPI instance without Stripe."""
        return BillingAPI(store=store, stripe_service=None)

    @pytest.fixture
    def org_id(self) -> uuid4:
        """Create an org ID."""
        return uuid4()

    @pytest.fixture
    def team_plan(self) -> PricingPlan:
        """Create a team plan."""
        return PricingPlan(
            plan_id=uuid4(),
            name="team",
            display_name="Team",
            description="For teams",
            tier=PlanTier.TEAM,
            monthly_price_cents=29900,
            annual_price_cents=304800,
            max_users=10,
            max_ai_investigations_monthly=500,
            max_alerts_monthly=5000,
            features=PlanFeatures(multi_llm=True, auto_fix_pr=True),
        )

    # List plans tests

    @pytest.mark.asyncio
    async def test_list_plans(self, api: BillingAPI):
        """list_plans should return public plans."""
        plans = await api.list_plans()

        assert len(plans) > 0
        assert all("name" in p for p in plans)
        assert all("features" in p for p in plans)
        # Enterprise should not be included by default
        plan_names = [p["name"] for p in plans]
        assert "enterprise" not in plan_names

    @pytest.mark.asyncio
    async def test_list_plans_include_enterprise(self, api: BillingAPI):
        """list_plans should include enterprise when requested."""
        plans = await api.list_plans(include_enterprise=True)

        plan_names = [p["name"] for p in plans]
        assert "enterprise" in plan_names

    @pytest.mark.asyncio
    async def test_list_plans_with_org(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan
    ):
        """list_plans should mark current plan for org."""
        org_id = uuid4()
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        plans = await api.list_plans(org_id=org_id)

        team_plan_resp = next((p for p in plans if p["name"] == "team"), None)
        assert team_plan_resp is not None
        assert team_plan_resp["is_current"] is True

    @pytest.mark.asyncio
    async def test_list_plans_marks_popular(self, api: BillingAPI):
        """list_plans should mark growth plan as popular."""
        plans = await api.list_plans()

        growth_plan = next((p for p in plans if p["name"] == "growth"), None)
        if growth_plan:
            assert growth_plan["is_popular"] is True

    # Get plan comparison tests

    @pytest.mark.asyncio
    async def test_get_plan_comparison(self, api: BillingAPI):
        """get_plan_comparison should return comparison data."""
        comparison = await api.get_plan_comparison()

        assert len(comparison) > 0
        assert all("feature" in row for row in comparison)
        assert all("plans" in row for row in comparison)

    # Get subscription tests

    @pytest.mark.asyncio
    async def test_get_subscription_not_found(self, api: BillingAPI, org_id):
        """get_subscription should return None for no subscription."""
        result = await api.get_subscription(org_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_subscription(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """get_subscription should return subscription details."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=BillingCycle.MONTHLY,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.get_subscription(org_id)

        assert result is not None
        assert result["status"] == "active"
        assert result["billing_cycle"] == "monthly"
        assert result["plan"]["name"] == "team"

    @pytest.mark.asyncio
    async def test_get_subscription_trialing(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """get_subscription should show trial status."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.TRIALING,
            trial_end=datetime.now(timezone.utc) + timedelta(days=7),
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.get_subscription(org_id)

        assert result["is_trialing"] is True
        assert result["days_remaining_trial"] is not None

    # Get usage tests

    @pytest.mark.asyncio
    async def test_get_usage(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """get_usage should return usage and limits."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=50,
        )
        await store.upsert_usage_summary(summary)

        result = await api.get_usage(org_id)

        assert "plan" in result
        assert "limits" in result
        assert "features" in result

    # Get usage stats tests

    @pytest.mark.asyncio
    async def test_get_usage_stats(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """get_usage_stats should return stats."""
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=25,
            alerts_processed_count=100,
        )
        await store.upsert_usage_summary(summary)

        result = await api.get_usage_stats(org_id)

        assert result["ai_investigations"] == 25
        assert result["alerts_processed"] == 100

    # Get usage history tests

    @pytest.mark.asyncio
    async def test_get_usage_history(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """get_usage_history should return monthly history."""
        for month in ["2024-01", "2024-02"]:
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=10,
            )
            await store.upsert_usage_summary(summary)

        result = await api.get_usage_history(org_id, months=12)

        assert len(result) == 2
        assert all("year_month" in h for h in result)

    # Get usage report tests

    @pytest.mark.asyncio
    async def test_get_usage_report(self, api: BillingAPI, org_id):
        """get_usage_report should generate monthly report."""
        result = await api.get_usage_report(org_id)

        assert "period" in result
        assert "metrics" in result

    # Get usage forecast tests

    @pytest.mark.asyncio
    async def test_get_usage_forecast(self, api: BillingAPI, org_id):
        """get_usage_forecast should return forecast."""
        result = await api.get_usage_forecast(org_id)

        assert "forecast_available" in result

    # Create checkout tests

    @pytest.mark.asyncio
    async def test_create_checkout_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """Without Stripe, checkout must refuse — not hand out the paid tier free."""
        with pytest.raises(HTTPException) as exc:
            await api_no_stripe.create_checkout(org_id, "team")
        assert exc.value.status_code == 503

        # ...and no subscription was created as a side effect.
        assert await api_no_stripe._store.get_subscription(org_id) is None

    @pytest.mark.asyncio
    async def test_create_checkout_no_stripe_demo_mode(
        self, api_no_stripe: BillingAPI, org_id, demo_mode
    ):
        """COPILOT_DEMO_MODE=true restores the simulated checkout, clearly labelled."""
        result = await api_no_stripe.create_checkout(org_id, "team")

        assert "demo_session" in result["session_id"]
        assert result["simulated"] is True

    # Create portal tests

    @pytest.mark.asyncio
    async def test_create_portal_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """Without Stripe the "portal" manages nothing, so it must not be offered."""
        with pytest.raises(HTTPException) as exc:
            await api_no_stripe.create_portal(org_id)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_create_portal_no_stripe_demo_mode(
        self, api_no_stripe: BillingAPI, org_id, demo_mode
    ):
        """COPILOT_DEMO_MODE=true restores the simulated portal, clearly labelled."""
        result = await api_no_stripe.create_portal(org_id)

        assert "portal_url" in result
        assert result["simulated"] is True

    # Change plan tests

    @pytest.mark.asyncio
    async def test_change_plan_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """change_plan should raise without Stripe."""
        with pytest.raises(ValueError, match="Stripe not configured"):
            await api_no_stripe.change_plan(org_id, "growth")

    # Calculate proration tests

    @pytest.mark.asyncio
    async def test_calculate_proration_no_subscription(
        self, api: BillingAPI, org_id
    ):
        """calculate_proration should return error for no subscription."""
        result = await api.calculate_proration(org_id, "growth")

        assert "error" in result
        assert "No active subscription" in result["error"]

    @pytest.mark.asyncio
    async def test_calculate_proration(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """calculate_proration should return proration details."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
            current_period_end=datetime.now(timezone.utc) + timedelta(days=15),
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.calculate_proration(org_id, "growth")

        assert "proration_cents" in result or "error" in result

    # Cancel subscription tests

    @pytest.mark.asyncio
    async def test_cancel_subscription_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """cancel_subscription should raise without Stripe."""
        with pytest.raises(ValueError, match="Stripe not configured"):
            await api_no_stripe.cancel_subscription(org_id)

    # Reactivate subscription tests

    @pytest.mark.asyncio
    async def test_reactivate_subscription_no_stripe(
        self, api_no_stripe: BillingAPI, org_id
    ):
        """reactivate_subscription should raise without Stripe."""
        with pytest.raises(ValueError, match="Stripe not configured"):
            await api_no_stripe.reactivate_subscription(org_id)

    # Get invoices tests

    @pytest.mark.asyncio
    async def test_get_invoices_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """get_invoices should return empty without Stripe."""
        result = await api_no_stripe.get_invoices(org_id)
        assert result == []

    # Get payment methods tests

    @pytest.mark.asyncio
    async def test_get_payment_methods_no_stripe(self, api_no_stripe: BillingAPI, org_id):
        """get_payment_methods should return empty without Stripe."""
        result = await api_no_stripe.get_payment_methods(org_id)
        assert result == []

    # Check feature tests

    @pytest.mark.asyncio
    async def test_check_feature_available(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """check_feature should return available for included features."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.check_feature(org_id, "multi_llm")

        assert result["available"] is True
        assert result["feature"] == "multi_llm"

    @pytest.mark.asyncio
    async def test_check_feature_not_available(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """check_feature should return upgrade info for unavailable features."""
        result = await api.check_feature(org_id, "sso_saml")

        assert result["available"] is False
        assert "upgrade" in result

    # Check usage limit tests

    @pytest.mark.asyncio
    async def test_check_usage_limit_within(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """check_usage_limit should return within_limit True."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.check_usage_limit(org_id, "ai_investigation")

        assert result["within_limit"] is True

    @pytest.mark.asyncio
    async def test_check_usage_limit_exceeded(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """check_usage_limit should return within_limit False when exceeded."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=500,
        )
        await store.upsert_usage_summary(summary)

        result = await api.check_usage_limit(org_id, "ai_investigation")

        assert result["within_limit"] is False

    # Get feature status tests

    @pytest.mark.asyncio
    async def test_get_feature_status(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """get_feature_status should return status of features."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.get_feature_status(org_id, ["multi_llm", "sso_saml"])

        assert "multi_llm" in result
        assert "sso_saml" in result

    # Get upgrade options tests

    @pytest.mark.asyncio
    async def test_get_upgrade_options(
        self, api: BillingAPI, store: InMemoryBillingStore, team_plan: PricingPlan, org_id
    ):
        """get_upgrade_options should return higher tier plans."""
        sub = Subscription(
            org_id=org_id,
            plan_id=team_plan.plan_id,
            status=SubscriptionStatus.ACTIVE,
        )
        await store.save_subscription(sub)
        store._plans["team"] = team_plan

        result = await api.get_upgrade_options(org_id)

        assert len(result) > 0
        plan_names = [p["name"] for p in result]
        assert "starter" not in plan_names

    # Handle webhook tests

    @pytest.mark.asyncio
    async def test_handle_webhook_no_stripe(self, api_no_stripe: BillingAPI):
        """handle_webhook should raise without Stripe."""
        with pytest.raises(ValueError, match="Stripe not configured"):
            await api_no_stripe.handle_webhook(b'{}', "sig")

    # Track usage tests

    @pytest.mark.asyncio
    async def test_track_usage_ai_investigation(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track AI investigation."""
        result = await api.track_usage(
            org_id,
            "ai_investigation",
            metadata={"incident_id": "inc_123"},
        )

        assert result["tracked"] is True
        assert result["metric_type"] == "ai_investigation"

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.ai_investigations_count == 1

    @pytest.mark.asyncio
    async def test_track_usage_alert_processed(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track alert processed."""
        result = await api.track_usage(
            org_id,
            "alert_processed",
            metadata={"count": 5},
        )

        assert result["tracked"] is True

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.alerts_processed_count == 5

    @pytest.mark.asyncio
    async def test_track_usage_api_call(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track API call."""
        result = await api.track_usage(
            org_id,
            "api_call",
            metadata={"endpoint": "/api/test", "method": "POST"},
        )

        assert result["tracked"] is True

    @pytest.mark.asyncio
    async def test_track_usage_postmortem(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track postmortem generated."""
        result = await api.track_usage(
            org_id,
            "postmortem_generated",
            metadata={"incident_id": "inc_123"},
        )

        assert result["tracked"] is True

    @pytest.mark.asyncio
    async def test_track_usage_auto_fix_pr(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track auto fix PR."""
        result = await api.track_usage(
            org_id,
            "auto_fix_pr",
            metadata={"incident_id": "inc_123", "pr_url": "https://github.com/..."},
        )

        assert result["tracked"] is True

    @pytest.mark.asyncio
    async def test_track_usage_similar_search(
        self, api: BillingAPI, store: InMemoryBillingStore, org_id
    ):
        """track_usage should track similar search."""
        result = await api.track_usage(
            org_id,
            "similar_search",
            metadata={"query": "database error", "results_count": 5},
        )

        assert result["tracked"] is True
