"""Feature gating service for controlling access to features based on plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Protocol
from uuid import UUID

from app.billing.models import (
    PricingPlan,
    Subscription,
    FeatureOverride,
    UsageSummary,
    MetricType,
)
from app.billing.plans import (
    PlanManager,
    FEATURE_TO_MIN_TIER,
)

log = logging.getLogger("devcopilot.billing.feature_gate")


class BillingStore(Protocol):
    """Protocol for billing data storage."""

    async def get_subscription(self, org_id: UUID) -> Subscription | None: ...
    async def get_plan(self, plan_id: UUID) -> PricingPlan | None: ...
    async def get_plan_by_name(self, name: str) -> PricingPlan | None: ...
    async def get_feature_overrides(self, org_id: UUID) -> list[FeatureOverride]: ...
    async def get_usage_summary(
        self, org_id: UUID, year_month: str
    ) -> UsageSummary | None: ...


@dataclass
class FeatureCheckResult:
    """Result of a feature access check."""

    allowed: bool
    reason: str | None = None
    required_plan: str | None = None
    upgrade_url: str | None = None
    current_usage: int | None = None
    limit: int | None = None
    remaining: int | None = None


@dataclass
class UsageLimitResult:
    """Result of a usage limit check."""

    within_limit: bool
    reason: str | None = None
    current_usage: int = 0
    limit: int | None = None
    remaining: int | None = None


class FeatureGateService:
    """Service to check feature access and usage limits."""

    def __init__(self, store: BillingStore):
        self._store = store
        self._plan_manager = PlanManager()
        self._cache: dict[str, Any] = {}

    async def get_organization_plan(self, org_id: UUID) -> PricingPlan | None:
        """Get the active plan for an organization."""
        subscription = await self._store.get_subscription(org_id)

        if not subscription or not subscription.is_active:
            return self._plan_manager.get_community_plan()

        plan = await self._store.get_plan(subscription.plan_id)
        if not plan:
            return self._plan_manager.get_community_plan()

        return plan

    async def can_access_feature(
        self,
        org_id: UUID,
        feature_key: str,
    ) -> FeatureCheckResult:
        """Check if organization can access a feature."""
        overrides = await self._store.get_feature_overrides(org_id)
        for override in overrides:
            if override.feature_key == feature_key and override.is_valid:
                if override.enabled:
                    return FeatureCheckResult(allowed=True)
                return FeatureCheckResult(
                    allowed=False,
                    reason="Feature disabled by administrator",
                )

        plan = await self.get_organization_plan(org_id)
        if not plan:
            plan = self._plan_manager.get_community_plan()

        if plan.features.has_feature(feature_key):
            return FeatureCheckResult(allowed=True)

        required_tier = FEATURE_TO_MIN_TIER.get(feature_key)
        if required_tier:
            required_plan = self._plan_manager.get_plan_by_tier(required_tier)
            plan_name = required_plan.display_name if required_plan else required_tier.value

            return FeatureCheckResult(
                allowed=False,
                reason=f"Upgrade to {plan_name} plan to access this feature",
                required_plan=required_tier.value,
                upgrade_url=f"/settings/billing/upgrade?plan={required_tier.value}",
            )

        return FeatureCheckResult(
            allowed=False,
            reason="Feature not available on your current plan",
            upgrade_url="/settings/billing/upgrade",
        )

    async def check_usage_limit(
        self,
        org_id: UUID,
        metric_type: MetricType,
        increment: int = 1,
    ) -> UsageLimitResult:
        """Check if organization is within usage limits."""
        plan = await self.get_organization_plan(org_id)
        if not plan:
            plan = self._plan_manager.get_community_plan()

        limit = self._get_plan_limit(plan, metric_type)

        if limit is None:
            return UsageLimitResult(
                within_limit=True,
                current_usage=0,
                limit=None,
                remaining=None,
            )

        current_usage = await self._get_current_usage(org_id, metric_type)
        remaining = limit - current_usage

        if current_usage + increment > limit:
            return UsageLimitResult(
                within_limit=False,
                reason=f"Monthly {metric_type.value} limit reached ({current_usage}/{limit}). Upgrade for more.",
                current_usage=current_usage,
                limit=limit,
                remaining=max(0, remaining),
            )

        return UsageLimitResult(
            within_limit=True,
            current_usage=current_usage,
            limit=limit,
            remaining=remaining - increment,
        )

    def _get_plan_limit(
        self,
        plan: PricingPlan,
        metric_type: MetricType,
    ) -> int | None:
        """Get the limit for a metric type from a plan."""
        if metric_type == MetricType.AI_INVESTIGATION:
            return plan.max_ai_investigations_monthly
        elif metric_type == MetricType.ALERT_PROCESSED:
            return plan.max_alerts_monthly
        elif metric_type == MetricType.API_CALL:
            return plan.features.get_limit("api_calls")
        return None

    async def _get_current_usage(
        self,
        org_id: UUID,
        metric_type: MetricType,
    ) -> int:
        """Get current month's usage for a metric."""
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await self._store.get_usage_summary(org_id, year_month)

        if not summary:
            return 0

        if metric_type == MetricType.AI_INVESTIGATION:
            return summary.ai_investigations_count
        elif metric_type == MetricType.ALERT_PROCESSED:
            return summary.alerts_processed_count
        elif metric_type == MetricType.API_CALL:
            return summary.api_calls_count
        elif metric_type == MetricType.POSTMORTEM_GENERATED:
            return summary.postmortems_generated_count
        elif metric_type == MetricType.AUTO_FIX_PR:
            return summary.auto_fix_prs_count
        elif metric_type == MetricType.SIMILAR_SEARCH:
            return summary.similar_searches_count

        return 0

    async def get_organization_limits(self, org_id: UUID) -> dict[str, Any]:
        """Get all limits and current usage for an organization."""
        plan = await self.get_organization_plan(org_id)
        if not plan:
            plan = self._plan_manager.get_community_plan()

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        usage = await self._store.get_usage_summary(org_id, year_month)

        return {
            "plan": {
                "name": plan.name,
                "display_name": plan.display_name,
                "tier": plan.tier.value,
            },
            "limits": {
                "users": {
                    "limit": plan.max_users,
                    "used": usage.active_users_count if usage else 0,
                    "unlimited": plan.is_unlimited_users,
                },
                "ai_investigations": {
                    "limit": plan.max_ai_investigations_monthly,
                    "used": usage.ai_investigations_count if usage else 0,
                    "unlimited": plan.is_unlimited_investigations,
                },
                "alerts": {
                    "limit": plan.max_alerts_monthly,
                    "used": usage.alerts_processed_count if usage else 0,
                    "unlimited": plan.is_unlimited_alerts,
                },
                "history_days": plan.features.limit_history_days,
                "llm_providers": plan.features.limit_llm_providers,
                "integrations": plan.features.limit_integrations,
            },
            "features": plan.features.to_dict(),
        }

    async def get_upgrade_prompt(
        self,
        org_id: UUID,
        feature_key: str,
    ) -> dict[str, Any]:
        """Get upgrade prompt information for a feature."""
        current_plan = await self.get_organization_plan(org_id)
        required_tier = FEATURE_TO_MIN_TIER.get(feature_key)

        if not required_tier:
            return {
                "feature": feature_key,
                "error": "Unknown feature",
            }

        required_plan = self._plan_manager.get_plan_by_tier(required_tier)

        return {
            "feature": feature_key,
            "current_plan": current_plan.name if current_plan else "community",
            "required_plan": required_tier.value,
            "required_plan_display": required_plan.display_name if required_plan else required_tier.value,
            "upgrade_url": f"/settings/billing/upgrade?plan={required_tier.value}",
            "price": {
                "monthly": required_plan.monthly_price if required_plan else None,
                "annual_monthly": required_plan.annual_monthly_price if required_plan else None,
            } if required_plan else None,
        }

    async def get_feature_status(
        self,
        org_id: UUID,
        feature_keys: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get status of multiple features."""
        plan = await self.get_organization_plan(org_id)
        if not plan:
            plan = self._plan_manager.get_community_plan()

        overrides = await self._store.get_feature_overrides(org_id)
        override_map = {o.feature_key: o for o in overrides if o.is_valid}

        if feature_keys is None:
            feature_keys = list(FEATURE_TO_MIN_TIER.keys())

        result = {}
        for feature in feature_keys:
            if feature in override_map:
                override = override_map[feature]
                result[feature] = {
                    "enabled": override.enabled,
                    "source": "override",
                    "expires_at": override.expires_at.isoformat() if override.expires_at else None,
                }
            else:
                enabled = plan.features.has_feature(feature)
                required_tier = FEATURE_TO_MIN_TIER.get(feature)
                required_plan_value = required_tier.value if required_tier else None
                result[feature] = {
                    "enabled": enabled,
                    "source": "plan",
                    "required_plan": None if enabled else required_plan_value,
                }

        return result


def require_feature(feature_key: str) -> Callable:
    """Decorator to require a feature for an endpoint."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            db = kwargs.get("db")
            org = kwargs.get("current_org")

            if not db or not org:
                raise PermissionError("Missing dependencies")

            gate = FeatureGateService(db)
            result = await gate.can_access_feature(org.org_id, feature_key)

            if not result.allowed:
                raise PermissionError(
                    f"Feature not available: {result.reason}",
                    {
                        "error": "feature_not_available",
                        "message": result.reason,
                        "feature": feature_key,
                        "required_plan": result.required_plan,
                        "upgrade_url": result.upgrade_url,
                    },
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def check_usage_limit(metric_type: MetricType, increment: int = 1) -> Callable:
    """Decorator to check usage limits."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            db = kwargs.get("db")
            org = kwargs.get("current_org")

            if not db or not org:
                raise PermissionError("Missing dependencies")

            gate = FeatureGateService(db)
            result = await gate.check_usage_limit(org.org_id, metric_type, increment)

            if not result.within_limit:
                raise PermissionError(
                    f"Usage limit exceeded: {result.reason}",
                    {
                        "error": "usage_limit_exceeded",
                        "message": result.reason,
                        "metric": metric_type.value,
                        "current_usage": result.current_usage,
                        "limit": result.limit,
                        "upgrade_url": "/settings/billing/upgrade",
                    },
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


class FeatureGate:
    """Context manager for feature gating."""

    def __init__(
        self,
        store: BillingStore,
        org_id: UUID,
        feature_key: str,
    ):
        self._store = store
        self._org_id = org_id
        self._feature_key = feature_key
        self._gate = FeatureGateService(store)
        self._result: FeatureCheckResult | None = None

    async def __aenter__(self) -> FeatureCheckResult:
        self._result = await self._gate.can_access_feature(
            self._org_id, self._feature_key
        )
        if not self._result.allowed:
            raise PermissionError(
                f"Feature not available: {self._result.reason}"
            )
        return self._result

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class UsageGate:
    """Context manager for usage limit checking."""

    def __init__(
        self,
        store: BillingStore,
        org_id: UUID,
        metric_type: MetricType,
        increment: int = 1,
    ):
        self._store = store
        self._org_id = org_id
        self._metric_type = metric_type
        self._increment = increment
        self._gate = FeatureGateService(store)
        self._result: UsageLimitResult | None = None

    async def __aenter__(self) -> UsageLimitResult:
        self._result = await self._gate.check_usage_limit(
            self._org_id, self._metric_type, self._increment
        )
        if not self._result.within_limit:
            raise PermissionError(
                f"Usage limit exceeded: {self._result.reason}"
            )
        return self._result

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass
