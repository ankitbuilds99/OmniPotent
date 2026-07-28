"""Smart usage tracking and limits.

Implements graceful degradation, soft limits, and value-based usage tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.growth.usage")


class UsageLimitType(str, Enum):
    INVESTIGATIONS = "investigations"
    TEAM_MEMBERS = "team_members"
    INTEGRATIONS = "integrations"
    POSTMORTEMS = "postmortems"
    RUNBOOKS = "runbooks"
    API_CALLS = "api_calls"


class LimitBehavior(str, Enum):
    HARD_BLOCK = "hard_block"
    SOFT_LIMIT = "soft_limit"
    GRACEFUL_DEGRADE = "graceful_degrade"
    UNLIMITED = "unlimited"


@dataclass
class UsageLimit:
    """A usage limit configuration."""

    limit_type: UsageLimitType
    limit_value: int
    behavior: LimitBehavior = LimitBehavior.SOFT_LIMIT
    reset_period_days: int = 30
    bonus_available: int = 0
    overage_allowed_percent: int = 20

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.limit_type.value,
            "limit": self.limit_value,
            "behavior": self.behavior.value,
            "reset_days": self.reset_period_days,
            "bonus": self.bonus_available,
            "overage_percent": self.overage_allowed_percent,
        }


@dataclass
class UsageRecord:
    """Record of usage for a specific type."""

    limit_type: UsageLimitType
    used: int = 0
    bonus_used: int = 0
    period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.limit_type.value,
            "used": self.used,
            "bonus_used": self.bonus_used,
            "period_start": self.period_start.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
        }


@dataclass
class ValueMetrics:
    """Value delivered to user."""

    hours_saved: float = 0.0
    dollars_saved: float = 0.0
    incidents_resolved: int = 0
    mttr_improvement_percent: float = 0.0
    root_causes_found: int = 0
    postmortems_generated: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours_saved": round(self.hours_saved, 1),
            "dollars_saved": round(self.dollars_saved, 2),
            "incidents_resolved": self.incidents_resolved,
            "mttr_improvement_percent": round(self.mttr_improvement_percent, 1),
            "root_causes_found": self.root_causes_found,
            "postmortems_generated": self.postmortems_generated,
        }


@dataclass
class UsageSummary:
    """Summary of usage for a user/org."""

    org_id: str
    tier: str
    limits: dict[UsageLimitType, UsageLimit]
    usage: dict[UsageLimitType, UsageRecord]
    value: ValueMetrics
    bonus_investigations: int = 0
    period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    period_end: datetime | None = None

    def get_usage_percent(self, limit_type: UsageLimitType) -> float:
        """Get usage as percentage of limit."""
        limit = self.limits.get(limit_type)
        record = self.usage.get(limit_type)
        if not limit or not record:
            return 0.0
        total_limit = limit.limit_value + limit.bonus_available
        if total_limit == 0:
            return 0.0
        return (record.used + record.bonus_used) / total_limit * 100

    def is_at_limit(self, limit_type: UsageLimitType) -> bool:
        """Check if at or over limit."""
        return self.get_usage_percent(limit_type) >= 100

    def can_use(self, limit_type: UsageLimitType) -> tuple[bool, str]:
        """Check if can use more of a limit type."""
        limit = self.limits.get(limit_type)
        record = self.usage.get(limit_type)

        if not limit:
            return True, "No limit configured"

        if limit.behavior == LimitBehavior.UNLIMITED:
            return True, "Unlimited"

        if not record:
            return True, "No usage recorded"

        total_used = record.used + record.bonus_used
        total_limit = limit.limit_value + limit.bonus_available

        if total_used < total_limit:
            remaining = total_limit - total_used
            return True, f"{remaining} remaining"

        if limit.behavior == LimitBehavior.GRACEFUL_DEGRADE:
            overage_limit = total_limit * (1 + limit.overage_allowed_percent / 100)
            if total_used < overage_limit:
                return True, "In overage period (degraded features)"

        if limit.behavior == LimitBehavior.SOFT_LIMIT:
            return True, "Soft limit reached (upgrade recommended)"

        return False, "Limit reached"

    def to_dict(self) -> dict[str, Any]:
        usage_bars = {}
        for lt in UsageLimitType:
            limit = self.limits.get(lt)
            record = self.usage.get(lt)
            if limit and record:
                total_limit = limit.limit_value + limit.bonus_available
                usage_bars[lt.value] = {
                    "used": record.used + record.bonus_used,
                    "limit": total_limit,
                    "percent": self.get_usage_percent(lt),
                    "behavior": limit.behavior.value,
                    "unlimited": limit.behavior == LimitBehavior.UNLIMITED,
                }

        return {
            "org_id": self.org_id,
            "tier": self.tier,
            "usage": usage_bars,
            "value": self.value.to_dict(),
            "bonus_investigations": self.bonus_investigations,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat() if self.period_end else None,
        }


class UsageTracker:
    """Tracks usage and enforces limits with graceful degradation."""

    def __init__(self) -> None:
        self._usage: dict[str, dict[UsageLimitType, UsageRecord]] = {}
        self._limits: dict[str, dict[UsageLimitType, UsageLimit]] = {}
        self._value: dict[str, ValueMetrics] = {}
        self._bonus: dict[str, int] = {}
        self._tiers: dict[str, str] = {}

        self._tier_limits = {
            "free": {
                UsageLimitType.INVESTIGATIONS: UsageLimit(
                    UsageLimitType.INVESTIGATIONS, 10, LimitBehavior.GRACEFUL_DEGRADE
                ),
                UsageLimitType.TEAM_MEMBERS: UsageLimit(
                    UsageLimitType.TEAM_MEMBERS, 5, LimitBehavior.SOFT_LIMIT
                ),
                UsageLimitType.INTEGRATIONS: UsageLimit(
                    UsageLimitType.INTEGRATIONS, 5, LimitBehavior.SOFT_LIMIT
                ),
                UsageLimitType.POSTMORTEMS: UsageLimit(
                    UsageLimitType.POSTMORTEMS, 3, LimitBehavior.GRACEFUL_DEGRADE
                ),
                UsageLimitType.RUNBOOKS: UsageLimit(
                    UsageLimitType.RUNBOOKS, 0, LimitBehavior.HARD_BLOCK
                ),
            },
            "pro": {
                UsageLimitType.INVESTIGATIONS: UsageLimit(
                    UsageLimitType.INVESTIGATIONS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.TEAM_MEMBERS: UsageLimit(
                    UsageLimitType.TEAM_MEMBERS, 25, LimitBehavior.SOFT_LIMIT
                ),
                UsageLimitType.INTEGRATIONS: UsageLimit(
                    UsageLimitType.INTEGRATIONS, 15, LimitBehavior.SOFT_LIMIT
                ),
                UsageLimitType.POSTMORTEMS: UsageLimit(
                    UsageLimitType.POSTMORTEMS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.RUNBOOKS: UsageLimit(
                    UsageLimitType.RUNBOOKS, 0, LimitBehavior.UNLIMITED
                ),
            },
            "enterprise": {
                UsageLimitType.INVESTIGATIONS: UsageLimit(
                    UsageLimitType.INVESTIGATIONS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.TEAM_MEMBERS: UsageLimit(
                    UsageLimitType.TEAM_MEMBERS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.INTEGRATIONS: UsageLimit(
                    UsageLimitType.INTEGRATIONS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.POSTMORTEMS: UsageLimit(
                    UsageLimitType.POSTMORTEMS, 0, LimitBehavior.UNLIMITED
                ),
                UsageLimitType.RUNBOOKS: UsageLimit(
                    UsageLimitType.RUNBOOKS, 0, LimitBehavior.UNLIMITED
                ),
            },
        }

        self._value_per_investigation = {
            "hours_saved": 1.5,
            "dollars_saved": 225.0,
        }

    def set_tier(self, org_id: str, tier: str) -> None:
        """Set the tier for an organization."""
        self._tiers[org_id] = tier
        self._limits[org_id] = self._tier_limits.get(tier, self._tier_limits["free"]).copy()

    def get_tier(self, org_id: str) -> str:
        """Get the tier for an organization."""
        return self._tiers.get(org_id, "free")

    def add_bonus(self, org_id: str, amount: int, reason: str) -> int:
        """Add bonus investigations to an org."""
        current = self._bonus.get(org_id, 0)
        self._bonus[org_id] = current + amount

        if org_id in self._limits:
            inv_limit = self._limits[org_id].get(UsageLimitType.INVESTIGATIONS)
            if inv_limit:
                inv_limit.bonus_available += amount

        log.info("Added %d bonus investigations to %s: %s", amount, org_id, reason)
        return self._bonus[org_id]

    def record_usage(
        self,
        org_id: str,
        limit_type: UsageLimitType,
        amount: int = 1,
    ) -> tuple[bool, str]:
        """Record usage of a limit type."""
        if org_id not in self._usage:
            self._usage[org_id] = {}

        if limit_type not in self._usage[org_id]:
            self._usage[org_id][limit_type] = UsageRecord(limit_type=limit_type)

        record = self._usage[org_id][limit_type]

        summary = self.get_summary(org_id)
        can_use, reason = summary.can_use(limit_type)

        if not can_use:
            return False, reason

        record.used += amount
        record.last_used = datetime.now(timezone.utc)

        if limit_type == UsageLimitType.INVESTIGATIONS:
            self._update_value_metrics(org_id, amount)

        log.info("Recorded usage: org=%s type=%s amount=%d", org_id, limit_type.value, amount)
        return True, reason

    def _update_value_metrics(self, org_id: str, investigations: int) -> None:
        """Update value metrics based on investigations."""
        if org_id not in self._value:
            self._value[org_id] = ValueMetrics()

        metrics = self._value[org_id]
        metrics.hours_saved += investigations * self._value_per_investigation["hours_saved"]
        metrics.dollars_saved += investigations * self._value_per_investigation["dollars_saved"]
        metrics.incidents_resolved += investigations
        metrics.root_causes_found += investigations

    def record_postmortem(self, org_id: str) -> None:
        """Record a postmortem generation."""
        self.record_usage(org_id, UsageLimitType.POSTMORTEMS)
        if org_id in self._value:
            self._value[org_id].postmortems_generated += 1
            self._value[org_id].hours_saved += 2.0
            self._value[org_id].dollars_saved += 300.0

    def get_summary(self, org_id: str) -> UsageSummary:
        """Get usage summary for an organization."""
        tier = self.get_tier(org_id)
        limits = self._limits.get(org_id, self._tier_limits.get(tier, {}))
        usage = self._usage.get(org_id, {})
        value = self._value.get(org_id, ValueMetrics())
        bonus = self._bonus.get(org_id, 0)

        now = datetime.now(timezone.utc)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        next_month = period_start.month % 12 + 1
        next_year = period_start.year + (1 if next_month == 1 else 0)
        period_end = period_start.replace(year=next_year, month=next_month)

        return UsageSummary(
            org_id=org_id,
            tier=tier,
            limits=limits,
            usage=usage,
            value=value,
            bonus_investigations=bonus,
            period_start=period_start,
            period_end=period_end,
        )

    def get_value_summary(self, org_id: str) -> dict[str, Any]:
        """Get value delivered summary with Pro comparison."""
        summary = self.get_summary(org_id)
        value = summary.value

        pro_multiplier = 4.0 if summary.tier == "free" else 1.0

        return {
            "current": value.to_dict(),
            "pro_projection": {
                "hours_saved": round(value.hours_saved * pro_multiplier, 1),
                "dollars_saved": round(value.dollars_saved * pro_multiplier, 2),
                "additional_value": round(value.dollars_saved * (pro_multiplier - 1), 2),
            },
            "message": self._generate_value_message(value, summary.tier),
        }

    def _generate_value_message(self, value: ValueMetrics, tier: str) -> str:
        """Generate a value message for the user."""
        if value.incidents_resolved == 0:
            return "Run your first investigation to start seeing value!"

        hours = round(value.hours_saved, 1)
        dollars = round(value.dollars_saved, 2)

        if tier == "free":
            pro_value = dollars * 4
            return (
                f"You've saved approximately {hours} hours and ${dollars:,.0f} this month. "
                f"Pro would have saved ${pro_value:,.0f} (4x more investigations)."
            )

        return f"You've saved approximately {hours} hours and ${dollars:,.0f} this month."

    def get_upgrade_incentive(self, org_id: str) -> dict[str, Any]:
        """Get upgrade incentive based on current usage."""
        summary = self.get_summary(org_id)

        if summary.tier != "free":
            return {"show_incentive": False}

        inv_percent = summary.get_usage_percent(UsageLimitType.INVESTIGATIONS)
        value = summary.value

        if inv_percent >= 80:
            urgency = "high"
            message = "You're almost at your limit! Upgrade now to continue investigating."
        elif value.dollars_saved >= 1000:
            urgency = "medium"
            message = f"You've saved ${value.dollars_saved:,.0f}. Pro pays for itself in one investigation."
        else:
            urgency = "low"
            message = "Upgrade to Pro for unlimited investigations and advanced features."

        return {
            "show_incentive": True,
            "urgency": urgency,
            "message": message,
            "usage_percent": inv_percent,
            "value_delivered": value.dollars_saved,
            "pro_price": 99,
            "roi_multiple": round(value.dollars_saved * 4 / 99, 1) if value.dollars_saved > 0 else 0,
        }


_tracker: UsageTracker | None = None


def get_usage_tracker() -> UsageTracker:
    """Get the default usage tracker."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
