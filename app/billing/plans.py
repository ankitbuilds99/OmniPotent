"""Pricing plans definitions and management."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.billing.models import (
    PricingPlan,
    PlanTier,
    PlanFeatures,
)

log = logging.getLogger("devcopilot.billing.plans")


def _create_community_features() -> PlanFeatures:
    """Create Community tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=False,
        multi_llm=False,
        auto_fix_pr=False,
        ai_postmortem=False,
        similar_incident_search=False,
        advanced_deduplication=False,
        mtta_mttr_dashboard=False,
        api_access=False,
        limit_history_days=14,
        limit_llm_providers=1,
        limit_integrations=2,
    )


def _create_starter_features() -> PlanFeatures:
    """Create Starter tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=False,
        auto_fix_pr=False,
        ai_postmortem=False,
        similar_incident_search=False,
        advanced_deduplication=False,
        mtta_mttr_dashboard=False,
        api_access=False,
        limit_history_days=30,
        limit_llm_providers=1,
        limit_integrations=5,
    )


def _create_team_features() -> PlanFeatures:
    """Create Team tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=True,
        auto_fix_pr=True,
        ai_postmortem=True,
        similar_incident_search=True,
        advanced_deduplication=False,
        mtta_mttr_dashboard=False,
        api_access=False,
        limit_history_days=365,
        limit_llm_providers=5,
        limit_integrations=15,
    )


def _create_growth_features() -> PlanFeatures:
    """Create Growth tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=True,
        auto_fix_pr=True,
        ai_postmortem=True,
        similar_incident_search=True,
        advanced_deduplication=True,
        mtta_mttr_dashboard=True,
        api_access=True,
        cascade_detection=True,
        service_dependency_map=True,
        pattern_detection=True,
        scheduled_reports=True,
        limit_history_days=730,
        limit_llm_providers=5,
        limit_integrations=None,
    )


def _create_business_features() -> PlanFeatures:
    """Create Business tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=True,
        auto_fix_pr=True,
        ai_postmortem=True,
        similar_incident_search=True,
        advanced_deduplication=True,
        mtta_mttr_dashboard=True,
        api_access=True,
        cascade_detection=True,
        service_dependency_map=True,
        pattern_detection=True,
        scheduled_reports=True,
        sso_saml=True,
        rbac=True,
        audit_logs=True,
        soc2_compliance=True,
        custom_dashboards=True,
        servicenow_integration=True,
        priority_support=True,
        limit_history_days=None,
        limit_llm_providers=5,
        limit_integrations=None,
        limit_support_response_hours=4,
    )


def _create_scale_features() -> PlanFeatures:
    """Create Scale tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=True,
        auto_fix_pr=True,
        ai_postmortem=True,
        similar_incident_search=True,
        advanced_deduplication=True,
        mtta_mttr_dashboard=True,
        api_access=True,
        cascade_detection=True,
        service_dependency_map=True,
        pattern_detection=True,
        scheduled_reports=True,
        sso_saml=True,
        rbac=True,
        audit_logs=True,
        soc2_compliance=True,
        custom_dashboards=True,
        servicenow_integration=True,
        priority_support=True,
        self_hosted=True,
        dedicated_support_manager=True,
        quarterly_business_reviews=True,
        limit_history_days=None,
        limit_llm_providers=None,
        limit_integrations=None,
        limit_support_response_hours=2,
    )


def _create_enterprise_features() -> PlanFeatures:
    """Create Enterprise tier features."""
    return PlanFeatures(
        ai_investigation=True,
        slack_integration=True,
        basic_deduplication=True,
        github_integration=True,
        multi_llm=True,
        auto_fix_pr=True,
        ai_postmortem=True,
        similar_incident_search=True,
        advanced_deduplication=True,
        mtta_mttr_dashboard=True,
        api_access=True,
        cascade_detection=True,
        service_dependency_map=True,
        pattern_detection=True,
        scheduled_reports=True,
        sso_saml=True,
        rbac=True,
        audit_logs=True,
        soc2_compliance=True,
        custom_dashboards=True,
        servicenow_integration=True,
        priority_support=True,
        self_hosted=True,
        dedicated_support_manager=True,
        quarterly_business_reviews=True,
        dedicated_infrastructure=True,
        hipaa_compliance=True,
        air_gapped_deployment=True,
        custom_data_residency=True,
        bring_your_own_llm=True,
        custom_sla=True,
        phone_support_24_7=True,
        onsite_training=True,
        limit_history_days=None,
        limit_llm_providers=None,
        limit_integrations=None,
        limit_support_response_hours=1,
    )


PRICING_PLANS: dict[str, PricingPlan] = {
    "community": PricingPlan(
        plan_id=uuid4(),
        name="community",
        display_name="Community",
        description="Perfect for trying out AI-powered incident response",
        tier=PlanTier.COMMUNITY,
        monthly_price_cents=0,
        annual_price_cents=0,
        max_users=2,
        max_ai_investigations_monthly=25,
        max_alerts_monthly=250,
        features=_create_community_features(),
        sort_order=0,
        trial_days=0,
    ),
    "starter": PricingPlan(
        plan_id=uuid4(),
        name="starter",
        display_name="Starter",
        description="For small teams getting started with AI SRE",
        tier=PlanTier.STARTER,
        monthly_price_cents=9900,
        annual_price_cents=100800,
        max_users=3,
        max_ai_investigations_monthly=100,
        max_alerts_monthly=1000,
        features=_create_starter_features(),
        sort_order=1,
        trial_days=14,
    ),
    "team": PricingPlan(
        plan_id=uuid4(),
        name="team",
        display_name="Team",
        description="For growing teams that need full AI capabilities",
        tier=PlanTier.TEAM,
        monthly_price_cents=49900,
        annual_price_cents=508800,
        max_users=10,
        max_ai_investigations_monthly=None,
        max_alerts_monthly=None,
        features=_create_team_features(),
        sort_order=2,
        trial_days=14,
    ),
    "growth": PricingPlan(
        plan_id=uuid4(),
        name="growth",
        display_name="Growth",
        description="For scaling organizations with advanced needs",
        tier=PlanTier.GROWTH,
        monthly_price_cents=99900,
        annual_price_cents=1018800,
        max_users=25,
        max_ai_investigations_monthly=None,
        max_alerts_monthly=None,
        features=_create_growth_features(),
        sort_order=3,
        trial_days=14,
    ),
    "business": PricingPlan(
        plan_id=uuid4(),
        name="business",
        display_name="Business",
        description="For mid-size organizations with compliance needs",
        tier=PlanTier.BUSINESS,
        monthly_price_cents=149900,
        annual_price_cents=1528800,
        max_users=50,
        max_ai_investigations_monthly=None,
        max_alerts_monthly=None,
        features=_create_business_features(),
        sort_order=4,
        trial_days=14,
    ),
    "scale": PricingPlan(
        plan_id=uuid4(),
        name="scale",
        display_name="Scale",
        description="For larger organizations scaling their AI operations",
        tier=PlanTier.SCALE,
        monthly_price_cents=249900,
        annual_price_cents=2548800,
        max_users=100,
        max_ai_investigations_monthly=None,
        max_alerts_monthly=None,
        features=_create_scale_features(),
        sort_order=5,
        trial_days=14,
    ),
    "enterprise": PricingPlan(
        plan_id=uuid4(),
        name="enterprise",
        display_name="Enterprise",
        description="Custom solutions for large organizations",
        tier=PlanTier.ENTERPRISE,
        monthly_price_cents=0,
        annual_price_cents=0,
        max_users=999999,
        max_ai_investigations_monthly=None,
        max_alerts_monthly=None,
        features=_create_enterprise_features(),
        is_public=False,
        sort_order=6,
        trial_days=30,
    ),
}


PLAN_TIER_HIERARCHY = [
    PlanTier.COMMUNITY,
    PlanTier.STARTER,
    PlanTier.TEAM,
    PlanTier.GROWTH,
    PlanTier.BUSINESS,
    PlanTier.SCALE,
    PlanTier.ENTERPRISE,
]


FEATURE_TO_MIN_TIER: dict[str, PlanTier] = {
    "ai_investigation": PlanTier.COMMUNITY,
    "slack_integration": PlanTier.COMMUNITY,
    "basic_deduplication": PlanTier.COMMUNITY,
    "github_integration": PlanTier.STARTER,
    "multi_llm": PlanTier.TEAM,
    "auto_fix_pr": PlanTier.TEAM,
    "ai_postmortem": PlanTier.TEAM,
    "similar_incident_search": PlanTier.TEAM,
    "advanced_deduplication": PlanTier.GROWTH,
    "mtta_mttr_dashboard": PlanTier.GROWTH,
    "api_access": PlanTier.GROWTH,
    "cascade_detection": PlanTier.GROWTH,
    "service_dependency_map": PlanTier.GROWTH,
    "pattern_detection": PlanTier.GROWTH,
    "scheduled_reports": PlanTier.GROWTH,
    "sso_saml": PlanTier.BUSINESS,
    "rbac": PlanTier.BUSINESS,
    "audit_logs": PlanTier.BUSINESS,
    "soc2_compliance": PlanTier.BUSINESS,
    "custom_dashboards": PlanTier.BUSINESS,
    "servicenow_integration": PlanTier.BUSINESS,
    "priority_support": PlanTier.BUSINESS,
    "self_hosted": PlanTier.SCALE,
    "dedicated_support_manager": PlanTier.SCALE,
    "quarterly_business_reviews": PlanTier.SCALE,
    "dedicated_infrastructure": PlanTier.ENTERPRISE,
    "hipaa_compliance": PlanTier.ENTERPRISE,
    "air_gapped_deployment": PlanTier.ENTERPRISE,
    "custom_data_residency": PlanTier.ENTERPRISE,
    "bring_your_own_llm": PlanTier.ENTERPRISE,
    "custom_sla": PlanTier.ENTERPRISE,
    "phone_support_24_7": PlanTier.ENTERPRISE,
    "onsite_training": PlanTier.ENTERPRISE,
}


FEATURE_DISPLAY_NAMES: dict[str, str] = {
    "ai_investigation": "AI Investigations",
    "slack_integration": "Slack Integration",
    "basic_deduplication": "Alert Deduplication",
    "github_integration": "GitHub Integration",
    "multi_llm": "Multi-LLM Support",
    "auto_fix_pr": "Auto-Fix PR Generation",
    "ai_postmortem": "AI Postmortems",
    "similar_incident_search": "Similar Incident Search",
    "advanced_deduplication": "Advanced Noise Reduction (95%)",
    "mtta_mttr_dashboard": "MTTA/MTTR Dashboards",
    "api_access": "API Access",
    "cascade_detection": "Cascade Detection",
    "service_dependency_map": "Service Dependency Map",
    "pattern_detection": "Pattern Detection",
    "scheduled_reports": "Scheduled Reports",
    "sso_saml": "SSO/SAML",
    "rbac": "Role-Based Access Control",
    "audit_logs": "Audit Logs",
    "soc2_compliance": "SOC2 Compliance",
    "custom_dashboards": "Custom Dashboards",
    "servicenow_integration": "ServiceNow Integration",
    "priority_support": "Priority Support",
    "self_hosted": "Self-Hosted Deployment",
    "dedicated_support_manager": "Dedicated Support Manager",
    "quarterly_business_reviews": "Quarterly Business Reviews",
    "dedicated_infrastructure": "Dedicated Infrastructure",
    "hipaa_compliance": "HIPAA Compliance",
    "air_gapped_deployment": "Air-Gapped Deployment",
    "custom_data_residency": "Custom Data Residency",
    "bring_your_own_llm": "Bring Your Own LLM",
    "custom_sla": "Custom SLA",
    "phone_support_24_7": "24/7 Phone Support",
    "onsite_training": "Onsite Training",
}


class PlanManager:
    """Manages pricing plans."""

    def __init__(self, plans: dict[str, PricingPlan] | None = None):
        self._plans = plans or PRICING_PLANS

    def get_plan(self, plan_name: str) -> PricingPlan | None:
        """Get a plan by name."""
        return self._plans.get(plan_name)

    def get_plan_by_tier(self, tier: PlanTier) -> PricingPlan | None:
        """Get a plan by tier."""
        for plan in self._plans.values():
            if plan.tier == tier:
                return plan
        return None

    def get_all_plans(self, include_inactive: bool = False) -> list[PricingPlan]:
        """Get all plans sorted by order."""
        plans = [
            p for p in self._plans.values()
            if include_inactive or p.is_active
        ]
        return sorted(plans, key=lambda p: p.sort_order)

    def get_public_plans(self) -> list[PricingPlan]:
        """Get all public plans for pricing page."""
        return [
            p for p in self.get_all_plans()
            if p.is_public
        ]

    def get_community_plan(self) -> PricingPlan:
        """Get the free community plan."""
        plan = self._plans.get("community")
        if not plan:
            raise ValueError("Community plan not found")
        return plan

    def compare_tiers(self, tier1: PlanTier, tier2: PlanTier) -> int:
        """Compare two tiers. Returns -1 if tier1 < tier2, 0 if equal, 1 if tier1 > tier2."""
        idx1 = PLAN_TIER_HIERARCHY.index(tier1)
        idx2 = PLAN_TIER_HIERARCHY.index(tier2)
        if idx1 < idx2:
            return -1
        elif idx1 > idx2:
            return 1
        return 0

    def is_upgrade(self, from_plan: str, to_plan: str) -> bool:
        """Check if changing plans is an upgrade."""
        from_p = self.get_plan(from_plan)
        to_p = self.get_plan(to_plan)
        if not from_p or not to_p:
            return False
        return self.compare_tiers(from_p.tier, to_p.tier) < 0

    def is_downgrade(self, from_plan: str, to_plan: str) -> bool:
        """Check if changing plans is a downgrade."""
        from_p = self.get_plan(from_plan)
        to_p = self.get_plan(to_plan)
        if not from_p or not to_p:
            return False
        return self.compare_tiers(from_p.tier, to_p.tier) > 0

    def get_feature_min_tier(self, feature_key: str) -> PlanTier | None:
        """Get minimum tier required for a feature."""
        return FEATURE_TO_MIN_TIER.get(feature_key)

    def get_feature_display_name(self, feature_key: str) -> str:
        """Get display name for a feature."""
        return FEATURE_DISPLAY_NAMES.get(feature_key, feature_key.replace("_", " ").title())

    def get_upgrade_options(self, current_plan: str) -> list[PricingPlan]:
        """Get available upgrade options from current plan."""
        current = self.get_plan(current_plan)
        if not current:
            return self.get_public_plans()

        return [
            p for p in self.get_public_plans()
            if self.compare_tiers(current.tier, p.tier) < 0
        ]

    def calculate_proration(
        self,
        from_plan: str,
        to_plan: str,
        days_remaining: int,
        billing_cycle: str = "monthly",
    ) -> dict[str, Any]:
        """Calculate proration amount for plan change."""
        from_p = self.get_plan(from_plan)
        to_p = self.get_plan(to_plan)

        if not from_p or not to_p:
            return {"error": "Invalid plan"}

        if billing_cycle == "annual":
            from_price = from_p.annual_price_cents
            to_price = to_p.annual_price_cents
            total_days = 365
        else:
            from_price = from_p.monthly_price_cents
            to_price = to_p.monthly_price_cents
            total_days = 30

        from_daily = from_price / total_days
        to_daily = to_price / total_days

        credit = from_daily * days_remaining
        charge = to_daily * days_remaining
        proration = charge - credit

        return {
            "from_plan": from_plan,
            "to_plan": to_plan,
            "days_remaining": days_remaining,
            "credit_cents": int(credit),
            "charge_cents": int(charge),
            "proration_cents": int(proration),
            "proration_amount": proration / 100,
            "is_upgrade": self.is_upgrade(from_plan, to_plan),
        }

    def get_plan_comparison(self) -> list[dict[str, Any]]:
        """Get plan comparison data for pricing page."""
        plans = self.get_public_plans()
        features = list(FEATURE_DISPLAY_NAMES.keys())

        comparison = []
        for feature in features:
            plans_dict: dict[str, bool] = {}
            for plan in plans:
                plans_dict[plan.name] = plan.features.has_feature(feature)
            row = {
                "feature": feature,
                "display_name": self.get_feature_display_name(feature),
                "plans": plans_dict,
            }
            comparison.append(row)

        return comparison
