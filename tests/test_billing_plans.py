"""Tests for billing plans module."""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.billing.models import PlanTier, PricingPlan
from app.billing.plans import (
    PlanManager,
    PRICING_PLANS,
    PLAN_TIER_HIERARCHY,
    FEATURE_TO_MIN_TIER,
    FEATURE_DISPLAY_NAMES,
    _create_starter_features,
    _create_team_features,
    _create_growth_features,
    _create_business_features,
    _create_scale_features,
    _create_enterprise_features,
)


class TestPlanFeatureCreation:
    """Tests for plan feature factory functions."""

    def test_starter_features_has_basic_features(self):
        """Starter features should include basic features only."""
        features = _create_starter_features()

        assert features.ai_investigation is True
        assert features.slack_integration is True
        assert features.basic_deduplication is True
        assert features.github_integration is False
        assert features.multi_llm is False
        assert features.auto_fix_pr is False
        assert features.limit_history_days == 14
        assert features.limit_llm_providers == 1
        assert features.limit_integrations == 1

    def test_team_features_has_team_level_features(self):
        """Team features should include team level features."""
        features = _create_team_features()

        assert features.github_integration is True
        assert features.multi_llm is True
        assert features.auto_fix_pr is True
        assert features.ai_postmortem is True
        assert features.similar_incident_search is True
        assert features.advanced_deduplication is False
        assert features.mtta_mttr_dashboard is False
        assert features.limit_history_days == 365

    def test_growth_features_has_growth_level_features(self):
        """Growth features should include growth level features."""
        features = _create_growth_features()

        assert features.advanced_deduplication is True
        assert features.mtta_mttr_dashboard is True
        assert features.api_access is True
        assert features.cascade_detection is True
        assert features.service_dependency_map is True
        assert features.sso_saml is False
        assert features.limit_integrations is None

    def test_business_features_has_business_level_features(self):
        """Business features should include business level features."""
        features = _create_business_features()

        assert features.sso_saml is True
        assert features.rbac is True
        assert features.audit_logs is True
        assert features.soc2_compliance is True
        assert features.self_hosted is False
        assert features.limit_support_response_hours == 4

    def test_scale_features_has_scale_level_features(self):
        """Scale features should include scale level features."""
        features = _create_scale_features()

        assert features.self_hosted is True
        assert features.dedicated_support_manager is True
        assert features.quarterly_business_reviews is True
        assert features.dedicated_infrastructure is False
        assert features.limit_support_response_hours == 2

    def test_enterprise_features_has_all_features(self):
        """Enterprise features should include all features."""
        features = _create_enterprise_features()

        assert features.dedicated_infrastructure is True
        assert features.hipaa_compliance is True
        assert features.air_gapped_deployment is True
        assert features.custom_data_residency is True
        assert features.bring_your_own_llm is True
        assert features.custom_sla is True
        assert features.phone_support_24_7 is True
        assert features.onsite_training is True
        assert features.limit_support_response_hours == 1


class TestPricingPlansDefinition:
    """Tests for PRICING_PLANS dictionary."""

    def test_all_required_plans_exist(self):
        """All required plans should be defined."""
        required_plans = ["starter", "team", "growth", "business", "scale", "enterprise"]
        for plan_name in required_plans:
            assert plan_name in PRICING_PLANS

    def test_starter_plan_is_free(self):
        """Starter plan should be free."""
        plan = PRICING_PLANS["starter"]
        assert plan.monthly_price_cents == 0
        assert plan.annual_price_cents == 0
        assert plan.trial_days == 0

    def test_paid_plans_have_pricing(self):
        """Paid plans should have pricing set."""
        paid_plans = ["team", "growth", "business", "scale"]
        for plan_name in paid_plans:
            plan = PRICING_PLANS[plan_name]
            assert plan.monthly_price_cents > 0
            assert plan.annual_price_cents > 0
            assert plan.trial_days > 0

    def test_enterprise_plan_is_not_public(self):
        """Enterprise plan should not be public."""
        plan = PRICING_PLANS["enterprise"]
        assert plan.is_public is False

    def test_plans_have_increasing_user_limits(self):
        """Plans should have increasing user limits."""
        user_limits = []
        for plan_name in ["starter", "team", "growth", "business", "scale"]:
            user_limits.append(PRICING_PLANS[plan_name].max_users)

        for i in range(len(user_limits) - 1):
            assert user_limits[i] <= user_limits[i + 1]


class TestPlanTierHierarchy:
    """Tests for plan tier hierarchy."""

    def test_tier_hierarchy_order(self):
        """Tier hierarchy should be in correct order."""
        expected_order = [
            PlanTier.STARTER,
            PlanTier.TEAM,
            PlanTier.GROWTH,
            PlanTier.BUSINESS,
            PlanTier.SCALE,
            PlanTier.ENTERPRISE,
        ]
        assert PLAN_TIER_HIERARCHY == expected_order


class TestFeatureToMinTier:
    """Tests for feature to minimum tier mapping."""

    def test_basic_features_have_starter_tier(self):
        """Basic features should require starter tier."""
        starter_features = ["ai_investigation", "slack_integration", "basic_deduplication"]
        for feature in starter_features:
            assert FEATURE_TO_MIN_TIER[feature] == PlanTier.STARTER

    def test_advanced_features_require_higher_tiers(self):
        """Advanced features should require higher tiers."""
        assert FEATURE_TO_MIN_TIER["multi_llm"] == PlanTier.TEAM
        assert FEATURE_TO_MIN_TIER["advanced_deduplication"] == PlanTier.GROWTH
        assert FEATURE_TO_MIN_TIER["sso_saml"] == PlanTier.BUSINESS
        assert FEATURE_TO_MIN_TIER["self_hosted"] == PlanTier.SCALE
        assert FEATURE_TO_MIN_TIER["dedicated_infrastructure"] == PlanTier.ENTERPRISE


class TestFeatureDisplayNames:
    """Tests for feature display names."""

    def test_all_features_have_display_names(self):
        """All features should have display names."""
        for feature_key in FEATURE_TO_MIN_TIER.keys():
            assert feature_key in FEATURE_DISPLAY_NAMES

    def test_display_names_are_human_readable(self):
        """Display names should be human readable."""
        assert FEATURE_DISPLAY_NAMES["ai_investigation"] == "AI Investigations"
        assert FEATURE_DISPLAY_NAMES["sso_saml"] == "SSO/SAML"
        assert FEATURE_DISPLAY_NAMES["mtta_mttr_dashboard"] == "MTTA/MTTR Dashboards"


class TestPlanManager:
    """Tests for PlanManager class."""

    @pytest.fixture
    def plan_manager(self) -> PlanManager:
        """Create a PlanManager instance."""
        return PlanManager()

    def test_get_plan_returns_existing_plan(self, plan_manager: PlanManager):
        """get_plan should return existing plan."""
        plan = plan_manager.get_plan("team")
        assert plan is not None
        assert plan.name == "team"
        assert plan.tier == PlanTier.TEAM

    def test_get_plan_returns_none_for_nonexistent(self, plan_manager: PlanManager):
        """get_plan should return None for nonexistent plan."""
        assert plan_manager.get_plan("nonexistent") is None

    def test_get_plan_by_tier(self, plan_manager: PlanManager):
        """get_plan_by_tier should return correct plan."""
        plan = plan_manager.get_plan_by_tier(PlanTier.GROWTH)
        assert plan is not None
        assert plan.tier == PlanTier.GROWTH
        assert plan.name == "growth"

    def test_get_plan_by_tier_nonexistent(self, plan_manager: PlanManager):
        """get_plan_by_tier should return None for unmapped tier."""
        plan = plan_manager.get_plan_by_tier(PlanTier.COMMUNITY)
        assert plan is None

    def test_get_all_plans(self, plan_manager: PlanManager):
        """get_all_plans should return all active plans."""
        plans = plan_manager.get_all_plans()
        assert len(plans) >= 5
        for plan in plans:
            assert plan.is_active is True

    def test_get_all_plans_sorted_by_order(self, plan_manager: PlanManager):
        """get_all_plans should return plans sorted by order."""
        plans = plan_manager.get_all_plans()
        for i in range(len(plans) - 1):
            assert plans[i].sort_order <= plans[i + 1].sort_order

    def test_get_public_plans_excludes_enterprise(self, plan_manager: PlanManager):
        """get_public_plans should exclude enterprise plan."""
        plans = plan_manager.get_public_plans()
        plan_names = [p.name for p in plans]
        assert "enterprise" not in plan_names
        assert "team" in plan_names

    def test_get_community_plan(self, plan_manager: PlanManager):
        """get_community_plan should return starter plan."""
        plan = plan_manager.get_community_plan()
        assert plan.name == "starter"

    def test_get_free_plan_is_alias(self, plan_manager: PlanManager):
        """get_free_plan should be alias for get_community_plan."""
        free_plan = plan_manager.get_free_plan()
        community_plan = plan_manager.get_community_plan()
        assert free_plan.plan_id == community_plan.plan_id

    def test_compare_tiers_less_than(self, plan_manager: PlanManager):
        """compare_tiers should return -1 when tier1 < tier2."""
        result = plan_manager.compare_tiers(PlanTier.STARTER, PlanTier.TEAM)
        assert result == -1

    def test_compare_tiers_greater_than(self, plan_manager: PlanManager):
        """compare_tiers should return 1 when tier1 > tier2."""
        result = plan_manager.compare_tiers(PlanTier.ENTERPRISE, PlanTier.TEAM)
        assert result == 1

    def test_compare_tiers_equal(self, plan_manager: PlanManager):
        """compare_tiers should return 0 when tiers are equal."""
        result = plan_manager.compare_tiers(PlanTier.TEAM, PlanTier.TEAM)
        assert result == 0

    def test_is_upgrade_returns_true(self, plan_manager: PlanManager):
        """is_upgrade should return True for upgrade."""
        assert plan_manager.is_upgrade("starter", "team") is True
        assert plan_manager.is_upgrade("team", "growth") is True

    def test_is_upgrade_returns_false_for_downgrade(self, plan_manager: PlanManager):
        """is_upgrade should return False for downgrade."""
        assert plan_manager.is_upgrade("team", "starter") is False

    def test_is_upgrade_returns_false_for_same_plan(self, plan_manager: PlanManager):
        """is_upgrade should return False for same plan."""
        assert plan_manager.is_upgrade("team", "team") is False

    def test_is_upgrade_returns_false_for_invalid_plan(self, plan_manager: PlanManager):
        """is_upgrade should return False for invalid plan."""
        assert plan_manager.is_upgrade("invalid", "team") is False
        assert plan_manager.is_upgrade("team", "invalid") is False

    def test_is_downgrade_returns_true(self, plan_manager: PlanManager):
        """is_downgrade should return True for downgrade."""
        assert plan_manager.is_downgrade("team", "starter") is True

    def test_is_downgrade_returns_false_for_upgrade(self, plan_manager: PlanManager):
        """is_downgrade should return False for upgrade."""
        assert plan_manager.is_downgrade("starter", "team") is False

    def test_get_feature_min_tier(self, plan_manager: PlanManager):
        """get_feature_min_tier should return correct tier."""
        assert plan_manager.get_feature_min_tier("ai_investigation") == PlanTier.STARTER
        assert plan_manager.get_feature_min_tier("sso_saml") == PlanTier.BUSINESS

    def test_get_feature_min_tier_nonexistent(self, plan_manager: PlanManager):
        """get_feature_min_tier should return None for unknown feature."""
        assert plan_manager.get_feature_min_tier("nonexistent_feature") is None

    def test_get_feature_display_name_existing(self, plan_manager: PlanManager):
        """get_feature_display_name should return display name."""
        assert plan_manager.get_feature_display_name("ai_investigation") == "AI Investigations"

    def test_get_feature_display_name_fallback(self, plan_manager: PlanManager):
        """get_feature_display_name should fallback to formatted key."""
        name = plan_manager.get_feature_display_name("unknown_feature")
        assert name == "Unknown Feature"

    def test_get_upgrade_options(self, plan_manager: PlanManager):
        """get_upgrade_options should return higher tier plans."""
        options = plan_manager.get_upgrade_options("starter")
        option_names = [p.name for p in options]

        assert "team" in option_names
        assert "growth" in option_names
        assert "starter" not in option_names

    def test_get_upgrade_options_from_highest_tier(self, plan_manager: PlanManager):
        """get_upgrade_options should return empty for highest tier."""
        options = plan_manager.get_upgrade_options("enterprise")
        assert len(options) == 0

    def test_get_upgrade_options_invalid_plan(self, plan_manager: PlanManager):
        """get_upgrade_options should return all public plans for invalid plan."""
        options = plan_manager.get_upgrade_options("invalid")
        assert len(options) > 0

    def test_calculate_proration_upgrade(self, plan_manager: PlanManager):
        """calculate_proration should calculate upgrade proration."""
        result = plan_manager.calculate_proration(
            from_plan="team",
            to_plan="growth",
            days_remaining=15,
            billing_cycle="monthly",
        )

        assert "proration_cents" in result
        assert "is_upgrade" in result
        assert result["is_upgrade"] is True
        assert result["from_plan"] == "team"
        assert result["to_plan"] == "growth"

    def test_calculate_proration_downgrade(self, plan_manager: PlanManager):
        """calculate_proration should calculate downgrade proration."""
        result = plan_manager.calculate_proration(
            from_plan="growth",
            to_plan="team",
            days_remaining=15,
            billing_cycle="monthly",
        )

        assert result["is_upgrade"] is False

    def test_calculate_proration_annual(self, plan_manager: PlanManager):
        """calculate_proration should handle annual billing."""
        result = plan_manager.calculate_proration(
            from_plan="team",
            to_plan="growth",
            days_remaining=180,
            billing_cycle="annual",
        )

        assert "proration_cents" in result
        assert result["days_remaining"] == 180

    def test_calculate_proration_invalid_plan(self, plan_manager: PlanManager):
        """calculate_proration should return error for invalid plan."""
        result = plan_manager.calculate_proration(
            from_plan="invalid",
            to_plan="team",
            days_remaining=15,
        )

        assert "error" in result

    def test_get_plan_comparison(self, plan_manager: PlanManager):
        """get_plan_comparison should return comparison data."""
        comparison = plan_manager.get_plan_comparison()

        assert len(comparison) > 0
        for row in comparison:
            assert "feature" in row
            assert "display_name" in row
            assert "plans" in row
            assert isinstance(row["plans"], dict)


class TestPlanManagerWithCustomPlans:
    """Tests for PlanManager with custom plans."""

    def test_custom_plans(self):
        """PlanManager should work with custom plans."""
        custom_plan = PricingPlan(
            plan_id=uuid4(),
            name="custom",
            display_name="Custom",
            tier=PlanTier.TEAM,
            monthly_price_cents=5000,
        )

        manager = PlanManager(plans={"custom": custom_plan})

        assert manager.get_plan("custom") is not None
        assert manager.get_plan("team") is None

    def test_missing_starter_plan_raises(self):
        """get_community_plan should raise if starter is missing."""
        manager = PlanManager(plans={})

        with pytest.raises(ValueError, match="Starter plan not found"):
            manager.get_community_plan()
