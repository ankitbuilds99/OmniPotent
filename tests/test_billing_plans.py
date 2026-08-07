"""Tests for billing plans module."""
from __future__ import annotations
import pytest
from uuid import uuid4
from app.billing.models import PlanTier, PricingPlan
from app.billing.plans import (
    PlanManager, PRICING_PLANS, PLAN_TIER_HIERARCHY, FEATURE_TO_MIN_TIER,
    FEATURE_DISPLAY_NAMES, _create_starter_features, _create_team_features,
    _create_growth_features, _create_business_features, _create_scale_features,
    _create_enterprise_features,
)

class TestPlanFeatureCreation:
    def test_starter_features_has_basic_features(self):
        features = _create_starter_features()
        assert features.ai_investigation is True
        assert features.github_integration is False
        assert features.limit_history_days == 14

    def test_team_features_has_team_level_features(self):
        features = _create_team_features()
        assert features.github_integration is True
        assert features.multi_llm is True

    def test_growth_features_has_growth_level_features(self):
        features = _create_growth_features()
        assert features.advanced_deduplication is True
        assert features.api_access is True

    def test_business_features_has_business_level_features(self):
        features = _create_business_features()
        assert features.sso_saml is True
        assert features.rbac is True

    def test_scale_features_has_scale_level_features(self):
        features = _create_scale_features()
        assert features.self_hosted is True

    def test_enterprise_features_has_all_features(self):
        features = _create_enterprise_features()
        assert features.dedicated_infrastructure is True
        assert features.hipaa_compliance is True

class TestPricingPlansDefinition:
    def test_all_required_plans_exist(self):
        required_plans = ["starter", "team", "growth", "business", "scale", "enterprise"]
        for plan_name in required_plans:
            assert plan_name in PRICING_PLANS

    def test_starter_plan_is_free(self):
        plan = PRICING_PLANS["starter"]
        assert plan.monthly_price_cents == 0

    def test_paid_plans_have_pricing(self):
        for plan_name in ["team", "growth", "business", "scale"]:
            plan = PRICING_PLANS[plan_name]
            assert plan.monthly_price_cents > 0

    def test_enterprise_plan_is_not_public(self):
        assert PRICING_PLANS["enterprise"].is_public is False

class TestPlanTierHierarchy:
    def test_tier_hierarchy_order(self):
        expected = [PlanTier.STARTER, PlanTier.TEAM, PlanTier.GROWTH, PlanTier.BUSINESS, PlanTier.SCALE, PlanTier.ENTERPRISE]
        assert PLAN_TIER_HIERARCHY == expected

class TestFeatureToMinTier:
    def test_basic_features_have_starter_tier(self):
        assert FEATURE_TO_MIN_TIER["ai_investigation"] == PlanTier.STARTER

    def test_advanced_features_require_higher_tiers(self):
        assert FEATURE_TO_MIN_TIER["multi_llm"] == PlanTier.TEAM
        assert FEATURE_TO_MIN_TIER["sso_saml"] == PlanTier.BUSINESS

class TestFeatureDisplayNames:
    def test_all_features_have_display_names(self):
        for feature_key in FEATURE_TO_MIN_TIER.keys():
            assert feature_key in FEATURE_DISPLAY_NAMES

class TestPlanManager:
    @pytest.fixture
    def plan_manager(self) -> PlanManager:
        return PlanManager()

    def test_get_plan_returns_existing_plan(self, plan_manager):
        plan = plan_manager.get_plan("team")
        assert plan is not None
        assert plan.name == "team"

    def test_get_plan_returns_none_for_nonexistent(self, plan_manager):
        assert plan_manager.get_plan("nonexistent") is None

    def test_get_plan_by_tier(self, plan_manager):
        plan = plan_manager.get_plan_by_tier(PlanTier.GROWTH)
        assert plan is not None
        assert plan.name == "growth"

    def test_get_all_plans(self, plan_manager):
        plans = plan_manager.get_all_plans()
        assert len(plans) >= 5

    def test_get_public_plans_excludes_enterprise(self, plan_manager):
        plans = plan_manager.get_public_plans()
        names = [p.name for p in plans]
        assert "enterprise" not in names

    def test_get_community_plan(self, plan_manager):
        assert plan_manager.get_community_plan().name == "starter"

    def test_compare_tiers(self, plan_manager):
        assert plan_manager.compare_tiers(PlanTier.STARTER, PlanTier.TEAM) == -1
        assert plan_manager.compare_tiers(PlanTier.TEAM, PlanTier.TEAM) == 0
        assert plan_manager.compare_tiers(PlanTier.TEAM, PlanTier.STARTER) == 1

    def test_is_upgrade(self, plan_manager):
        assert plan_manager.is_upgrade("starter", "team") is True
        assert plan_manager.is_upgrade("team", "starter") is False

    def test_is_downgrade(self, plan_manager):
        assert plan_manager.is_downgrade("team", "starter") is True

    def test_get_feature_min_tier(self, plan_manager):
        assert plan_manager.get_feature_min_tier("ai_investigation") == PlanTier.STARTER

    def test_get_feature_display_name(self, plan_manager):
        assert plan_manager.get_feature_display_name("ai_investigation") == "AI Investigations"
        assert plan_manager.get_feature_display_name("unknown_feature") == "Unknown Feature"

    def test_get_upgrade_options(self, plan_manager):
        options = plan_manager.get_upgrade_options("starter")
        names = [p.name for p in options]
        assert "team" in names
        assert "starter" not in names

    def test_calculate_proration_upgrade(self, plan_manager):
        result = plan_manager.calculate_proration("team", "growth", 15, "monthly")
        assert "proration_cents" in result
        assert result["is_upgrade"] is True

    def test_get_plan_comparison(self, plan_manager):
        comparison = plan_manager.get_plan_comparison()
        assert len(comparison) > 0

class TestPlanManagerWithCustomPlans:
    def test_custom_plans(self):
        custom_plan = PricingPlan(plan_id=uuid4(), name="custom", tier=PlanTier.TEAM)
        manager = PlanManager(plans={"custom": custom_plan})
        assert manager.get_plan("custom") is not None
        assert manager.get_plan("team") is None

    def test_missing_starter_plan_raises(self):
        custom_plan = PricingPlan(plan_id=uuid4(), name="custom", tier=PlanTier.TEAM)
        manager = PlanManager(plans={"custom": custom_plan})
        with pytest.raises(ValueError, match="Starter plan not found"):
            manager.get_community_plan()
