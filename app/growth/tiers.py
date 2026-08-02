"""Pricing tiers and feature comparison.

Manages tier definitions, feature gating, and comparison display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.growth.tiers")


class FeatureAccess(str, Enum):
    FULL = "full"
    PREVIEW = "preview"
    LIMITED = "limited"
    VIEW_ONLY = "view_only"
    LOCKED = "locked"


@dataclass
class TierFeature:
    """A feature with tier-based access."""

    feature_id: str
    name: str
    description: str
    category: str
    free_access: FeatureAccess = FeatureAccess.LOCKED
    pro_access: FeatureAccess = FeatureAccess.FULL
    enterprise_access: FeatureAccess = FeatureAccess.FULL
    preview_description: str = ""
    upgrade_cta: str = "Upgrade to unlock"

    def get_access(self, tier: str) -> FeatureAccess:
        """Get access level for a tier."""
        if tier == "enterprise":
            return self.enterprise_access
        elif tier == "pro":
            return self.pro_access
        return self.free_access

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.feature_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "free": self.free_access.value,
            "pro": self.pro_access.value,
            "enterprise": self.enterprise_access.value,
        }


@dataclass
class PricingTier:
    """A pricing tier configuration."""

    tier_id: str
    name: str
    price_monthly: int
    price_annual: int
    description: str
    highlights: list[str]
    limits: dict[str, int | str]
    is_popular: bool = False
    cta_text: str = "Get Started"
    cta_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.tier_id,
            "name": self.name,
            "price_monthly": self.price_monthly,
            "price_annual": self.price_annual,
            "description": self.description,
            "highlights": self.highlights,
            "limits": self.limits,
            "is_popular": self.is_popular,
            "cta_text": self.cta_text,
        }


@dataclass
class TierComparison:
    """Comparison between tiers for a specific user."""

    current_tier: str
    recommended_tier: str
    reason: str
    features_unlocked: list[TierFeature]
    additional_value: float
    roi_estimate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_tier": self.current_tier,
            "recommended_tier": self.recommended_tier,
            "reason": self.reason,
            "features_unlocked": [f.to_dict() for f in self.features_unlocked],
            "additional_value": self.additional_value,
            "roi_estimate": self.roi_estimate,
        }


class TierManager:
    """Manages pricing tiers and feature access."""

    def __init__(self) -> None:
        self._tiers: dict[str, PricingTier] = {}
        self._features: dict[str, TierFeature] = {}
        self._init_default_tiers()
        self._init_default_features()

    def _init_default_tiers(self) -> None:
        """Initialize default pricing tiers."""
        self._tiers["free"] = PricingTier(
            tier_id="free",
            name="Free",
            price_monthly=0,
            price_annual=0,
            description="For individuals and small teams getting started",
            highlights=[
                "10 AI investigations/month",
                "5 team members",
                "5 integrations",
                "Basic postmortems",
                "Community support",
            ],
            limits={
                "investigations": 10,
                "team_members": 5,
                "integrations": 5,
                "postmortems": "Basic",
                "runbooks": "View only",
                "sso": False,
                "sla": "Community",
            },
            cta_text="Get Started Free",
        )

        self._tiers["pro"] = PricingTier(
            tier_id="pro",
            name="Pro",
            price_monthly=99,
            price_annual=990,
            description="For growing teams that need full capabilities",
            highlights=[
                "Unlimited AI investigations",
                "25 team members",
                "15 integrations",
                "Full AI postmortems",
                "Runbook automation",
                "24hr support SLA",
            ],
            limits={
                "investigations": "Unlimited",
                "team_members": 25,
                "integrations": 15,
                "postmortems": "Full AI",
                "runbooks": "Execute",
                "sso": False,
                "sla": "24hr",
            },
            is_popular=True,
            cta_text="Upgrade to Pro",
        )

        self._tiers["enterprise"] = PricingTier(
            tier_id="enterprise",
            name="Enterprise",
            price_monthly=0,
            price_annual=0,
            description="For large organizations with advanced needs",
            highlights=[
                "Everything in Pro",
                "Unlimited team members",
                "Unlimited integrations",
                "SSO/SAML",
                "SOC2 report",
                "4hr support SLA",
                "Dedicated support",
            ],
            limits={
                "investigations": "Unlimited",
                "team_members": "Unlimited",
                "integrations": "Unlimited",
                "postmortems": "Full AI",
                "runbooks": "Execute + Edit",
                "sso": True,
                "sla": "4hr + Dedicated",
            },
            cta_text="Contact Sales",
        )

    def _init_default_features(self) -> None:
        """Initialize default features."""
        features = [
            TierFeature(
                "ai_investigation",
                "AI Investigation",
                "AI-powered root cause analysis",
                "core",
                FeatureAccess.LIMITED,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
                preview_description="10 investigations/month on Free",
            ),
            TierFeature(
                "ai_fix_generation",
                "AI Fix Generation",
                "Auto-generate code fixes for issues",
                "ai",
                FeatureAccess.PREVIEW,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
                preview_description="See suggested fixes, copy manually",
                upgrade_cta="Upgrade to apply fixes automatically",
            ),
            TierFeature(
                "predictive_incidents",
                "Predictive Incidents",
                "AI predicts incidents before they happen",
                "ai",
                FeatureAccess.PREVIEW,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
                preview_description="View predictions, no alerts",
            ),
            TierFeature(
                "postmortem_full",
                "Full AI Postmortems",
                "Complete AI-generated postmortems",
                "postmortem",
                FeatureAccess.LIMITED,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
            ),
            TierFeature(
                "runbook_execute",
                "Runbook Execution",
                "Execute automated runbooks",
                "automation",
                FeatureAccess.LOCKED,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
            ),
            TierFeature(
                "oncall_scheduling",
                "On-Call Scheduling",
                "Smart on-call schedule management",
                "oncall",
                FeatureAccess.LOCKED,
                FeatureAccess.FULL,
                FeatureAccess.FULL,
            ),
            TierFeature(
                "sso_saml",
                "SSO/SAML",
                "Single sign-on integration",
                "enterprise",
                FeatureAccess.LOCKED,
                FeatureAccess.LOCKED,
                FeatureAccess.FULL,
            ),
            TierFeature(
                "soc2_report",
                "SOC2 Report",
                "Access to SOC2 compliance report",
                "enterprise",
                FeatureAccess.LOCKED,
                FeatureAccess.LOCKED,
                FeatureAccess.FULL,
            ),
            TierFeature(
                "custom_integrations",
                "Custom Integrations",
                "Build custom integrations via API",
                "enterprise",
                FeatureAccess.LOCKED,
                FeatureAccess.LIMITED,
                FeatureAccess.FULL,
            ),
        ]

        for feature in features:
            self._features[feature.feature_id] = feature

    def get_tier(self, tier_id: str) -> PricingTier | None:
        """Get a pricing tier by ID."""
        return self._tiers.get(tier_id)

    def get_all_tiers(self) -> list[PricingTier]:
        """Get all pricing tiers."""
        return list(self._tiers.values())

    def get_feature(self, feature_id: str) -> TierFeature | None:
        """Get a feature by ID."""
        return self._features.get(feature_id)

    def get_all_features(self) -> list[TierFeature]:
        """Get all features."""
        return list(self._features.values())

    def check_access(
        self,
        tier: str,
        feature_id: str,
    ) -> tuple[FeatureAccess, dict[str, Any]]:
        """Check feature access for a tier."""
        feature = self._features.get(feature_id)
        if not feature:
            return FeatureAccess.FULL, {}

        access = feature.get_access(tier)

        response = {
            "feature": feature.name,
            "access": access.value,
            "tier": tier,
        }

        if access == FeatureAccess.LOCKED:
            response["message"] = f"Upgrade to unlock {feature.name}"
            response["upgrade_cta"] = feature.upgrade_cta
        elif access == FeatureAccess.PREVIEW:
            response["message"] = feature.preview_description
            response["upgrade_cta"] = feature.upgrade_cta
        elif access == FeatureAccess.LIMITED:
            response["message"] = feature.preview_description or "Limited access"

        return access, response

    def get_feature_teaser(
        self,
        tier: str,
        feature_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get a feature teaser for upselling."""
        feature = self._features.get(feature_id)
        if not feature:
            return {}

        access = feature.get_access(tier)

        if access == FeatureAccess.FULL:
            return {"show_teaser": False}

        teaser = {
            "show_teaser": True,
            "feature": feature.name,
            "description": feature.description,
            "current_access": access.value,
            "preview_text": feature.preview_description,
            "upgrade_cta": feature.upgrade_cta,
        }

        if feature_id == "ai_fix_generation" and context:
            teaser["preview_content"] = self._generate_fix_preview(context)

        return teaser

    def _generate_fix_preview(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate a preview of AI fix for teaser."""
        root_cause = context.get("root_cause", "")

        if "null" in root_cause.lower() or "none" in root_cause.lower():
            return {
                "type": "code_fix",
                "language": "python",
                "preview": "if payment_method is None:\n    raise PaymentMethodRequired(...)\npayment_method.charge(amount)",
                "message": "AI Fix Generation would add null check here",
            }

        return {
            "type": "suggestion",
            "message": "AI Fix Generation available on Pro",
        }

    def compare_tiers(
        self,
        current_tier: str,
        target_tier: str,
        current_usage: dict[str, Any] | None = None,
    ) -> TierComparison:
        """Compare two tiers for upgrade decision."""
        current = self._tiers.get(current_tier)
        target = self._tiers.get(target_tier)

        if not current or not target:
            return TierComparison(
                current_tier=current_tier,
                recommended_tier=target_tier,
                reason="Invalid tier",
                features_unlocked=[],
                additional_value=0,
                roi_estimate=0,
            )

        unlocked = []
        for feature in self._features.values():
            current_access = feature.get_access(current_tier)
            target_access = feature.get_access(target_tier)

            if target_access.value < current_access.value:
                unlocked.append(feature)

        additional_value = 0.0
        if current_usage:
            current_savings = current_usage.get("dollars_saved", 0)
            if current_tier == "free" and target_tier == "pro":
                additional_value = current_savings * 3

        roi = 0.0
        if target.price_monthly > 0 and current_usage:
            roi = (additional_value + current_usage.get("dollars_saved", 0)) / target.price_monthly

        if current_tier == "free" and target_tier == "pro":
            reason = "Unlimited investigations and full AI features"
        elif target_tier == "enterprise":
            reason = "SSO, compliance, and dedicated support"
        else:
            reason = f"Upgrade from {current.name} to {target.name}"

        return TierComparison(
            current_tier=current_tier,
            recommended_tier=target_tier,
            reason=reason,
            features_unlocked=unlocked,
            additional_value=additional_value,
            roi_estimate=roi,
        )

    def get_pricing_table(self) -> dict[str, Any]:
        """Get the full pricing table for display."""
        tiers = [t.to_dict() for t in self._tiers.values()]

        feature_matrix: list[dict[str, Any]] = []
        for feature in self._features.values():
            row: dict[str, Any] = {
                "name": feature.name,
                "category": feature.category,
            }
            for tier_id in self._tiers:
                access = feature.get_access(tier_id)
                if access == FeatureAccess.FULL:
                    row[tier_id] = True
                elif access == FeatureAccess.LOCKED:
                    row[tier_id] = False
                else:
                    row[tier_id] = access.value
            feature_matrix.append(row)

        return {
            "tiers": tiers,
            "features": feature_matrix,
        }


_manager: TierManager | None = None


def get_tier_manager() -> TierManager:
    """Get the default tier manager."""
    global _manager
    if _manager is None:
        _manager = TierManager()
    return _manager
