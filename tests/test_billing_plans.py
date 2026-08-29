"""Tests for billing plans module."""
from __future__ import annotations
import pytest
from uuid import uuid4
from app.billing.models import PlanTier, PricingPlan
from app.billing.plans import (
    PlanManager, PRICING_PLANS, PLAN_TIER_HIERARCHY, FEATURE_TO_MIN_TIER,
    FEATURE_DISPLAY_NAMES, CATALOG_TO_QUOTA_PLAN, QUOTA_PLAN_TO_CATALOG,
    catalog_plan_for, quota_plan_for,
    _create_starter_features, _create_team_features,
    _create_growth_features, _create_business_features, _create_scale_features,
    _create_enterprise_features,
)
from app.tenancy.models import PLAN_QUOTAS, normalize_plan, quota

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


class TestPlanVocabularies:
    """The catalog sells six tiers; the quota layer enforces five of them.

    Nothing reconciled them, so a paid tier the enforcement layer had never heard
    of was normalised to `free` on its way into `orgs.plan` — a downgrade applied
    on the strength of a successful payment. Then Growth and Business were
    translated to `team` instead, which is the same loss dressed up: `orgs.plan`
    is the *only* record of the tier on a deployment that upgrades by webhook, so
    once the name is gone the entitlements are gone with it.
    """

    def test_every_catalog_tier_maps_to_a_plan_that_is_enforced(self):
        assert set(CATALOG_TO_QUOTA_PLAN) == set(PRICING_PLANS)
        assert set(CATALOG_TO_QUOTA_PLAN.values()) <= set(PLAN_QUOTAS)

    def test_every_enforced_plan_has_a_tier_to_display(self):
        assert set(QUOTA_PLAN_TO_CATALOG) == set(PLAN_QUOTAS)
        assert set(QUOTA_PLAN_TO_CATALOG.values()) <= set(PRICING_PLANS)

    @pytest.mark.parametrize(
        "tier,expected",
        [("starter", "free"), ("team", "team"), ("growth", "growth"),
         ("business", "business"), ("scale", "enterprise"), ("enterprise", "enterprise")],
    )
    def test_quota_plan_for_known_tiers(self, tier, expected):
        """Each tier maps to the bracket the customer bought, not to a lesser one.

        This table used to assert `("growth", "team")` and `("business", "team")`,
        which made the audited bug the specification: this map's output is what
        `POST /webhooks/stripe` hands to `set_plan`, so mapping Growth to `team`
        did not approximate a quota, it deleted the tier from the only record of
        it and every Growth feature with it.

        Scale is the one tier still allowed to collapse, and only upwards, onto
        Enterprise's unlimited bracket: that grants more than was bought rather
        than less, and giving Scale a bracket of its own would start refusing
        seats that customers hold today.
        """
        assert quota_plan_for(tier) == expected

    @pytest.mark.parametrize("tier", ["growth", "business"])
    def test_a_paid_tier_survives_the_write_to_orgs_plan(self, tier):
        """The round trip that actually happens on a paid upgrade.

        `quota_plan_for` feeds `set_plan`, which stores `normalize_plan(...)`, and
        `FeatureGateService._plan_from_tenant_context` reads that column back
        through `catalog_plan_for`. Every step preserved the value except the
        vocabulary itself, which had no Growth or Business to keep — so the tier
        was destroyed in the database, not misread afterwards.
        """
        stored = normalize_plan(quota_plan_for(tier))
        assert stored == tier
        assert catalog_plan_for(stored).name == tier

    @pytest.mark.parametrize("tier", ["growth", "business"])
    def test_an_unlimited_tier_is_not_metered_at_teams_ceiling(self, tier):
        """Both tiers sell unlimited AI investigations. Metered as `team` they
        were cut off at 1000 a month, and the customer had no way to see why."""
        assert PRICING_PLANS[tier].max_ai_investigations_monthly is None
        assert quota(quota_plan_for(tier), "investigations_per_month") == -1
        assert quota(quota_plan_for(tier), "seats") >= PRICING_PLANS[tier].max_users

    @pytest.mark.parametrize("tier", ["  Growth ", "TEAM"])
    def test_quota_plan_for_is_forgiving_about_case_and_space(self, tier):
        assert quota_plan_for(tier) in PLAN_QUOTAS

    @pytest.mark.parametrize("tier", ["pro_monthly", "platinum", "", "  ", None])
    def test_an_unknown_tier_returns_none_rather_than_defaulting(self, tier):
        """None is the signal to reject. Defaulting is what caused the downgrade."""
        assert quota_plan_for(tier) is None

    def test_a_paid_tier_never_resolves_to_the_free_plan(self):
        for name, plan in PRICING_PLANS.items():
            if plan.monthly_price_cents > 0:
                assert quota_plan_for(name) != "free", f"{name} is priced but maps to free"

    @pytest.mark.parametrize(
        "plan,expected",
        [("free", "starter"), ("team", "team"), ("enterprise", "enterprise"),
         ("starter", "starter"), ("growth", "growth"), ("business", "business"),
         ("scale", "scale")],
    )
    def test_catalog_plan_for_names_the_tier_the_customer_actually_has(self, plan, expected):
        """Either vocabulary in, the tier the customer holds out.

        The catalog names — the last four — used to go through the quota→display
        map as well, and its default is Starter, so asking this function about a
        Growth or Business subscription answered "starter" and every feature
        above the free tier was withdrawn from someone paying $699 or $1,499 a
        month for exactly those features.
        """
        assert catalog_plan_for(plan).name == expected

    @pytest.mark.parametrize("tier", ["growth", "business", "scale"])
    def test_a_paid_tier_keeps_the_features_it_was_sold(self, tier):
        """`catalog_plan_for` is the entitlement path, not a display detail:
        `FeatureGateService._plan_from_tenant_context` calls it, and what it
        returns is what `/dependency-graph/*`, `/billing/features` and every
        `require_feature` gate enforce. Resolving a paid tier to a lesser one
        does not merely mislabel the Billing page, it turns the features off."""
        resolved = catalog_plan_for(tier).features
        for key, min_tier in FEATURE_TO_MIN_TIER.items():
            if PRICING_PLANS[tier].features.has_feature(key):
                assert resolved.has_feature(key), f"{tier} lost {key} ({min_tier})"

    def test_catalog_plan_for_falls_back_to_starter_for_junk(self):
        """The least generous tier, never the most — but the value that was not
        understood is logged, because on the entitlement path a plan nobody
        recognises is indistinguishable from a customer who never paid."""
        assert catalog_plan_for("nonsense").name == "starter"

    def test_the_round_trip_is_stable_for_representative_tiers(self):
        for quota_plan, tier in QUOTA_PLAN_TO_CATALOG.items():
            assert quota_plan_for(tier) == quota_plan
