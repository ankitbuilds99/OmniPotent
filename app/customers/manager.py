"""Customer and trial management for conversion tracking.

Tracks the full customer journey from trial to paid conversion,
with usage metering and engagement analytics.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.customers")


class CustomerTier(str, Enum):
    """Customer pricing tier."""

    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class CustomerStatus(str, Enum):
    """Customer lifecycle status."""

    TRIAL = "trial"
    ACTIVE = "active"
    CHURNED = "churned"
    PAUSED = "paused"


class TrialStatus(str, Enum):
    """Trial status."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CONVERTED = "converted"
    CANCELLED = "cancelled"


@dataclass
class UsageRecord:
    """Record of customer usage."""

    timestamp: datetime
    event_type: str
    investigation_id: str = ""
    tokens_used: int = 0
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trial:
    """Customer trial tracking."""

    id: str
    customer_id: str
    started_at: datetime
    expires_at: datetime
    status: TrialStatus = TrialStatus.ACTIVE

    investigations_allowed: int = 10
    investigations_used: int = 0
    tokens_allowed: int = 100000
    tokens_used: int = 0

    converted_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str = ""

    @property
    def is_active(self) -> bool:
        """Check if trial is still active."""
        if self.status != TrialStatus.ACTIVE:
            return False
        return datetime.now(timezone.utc) < self.expires_at

    @property
    def days_remaining(self) -> int:
        """Days remaining in trial."""
        if not self.is_active:
            return 0
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)

    @property
    def usage_percent(self) -> float:
        """Percentage of trial usage consumed."""
        inv_percent = self.investigations_used / self.investigations_allowed * 100
        token_percent = self.tokens_used / self.tokens_allowed * 100
        return max(inv_percent, token_percent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "days_remaining": self.days_remaining,
            "investigations_allowed": self.investigations_allowed,
            "investigations_used": self.investigations_used,
            "tokens_allowed": self.tokens_allowed,
            "tokens_used": self.tokens_used,
            "usage_percent": round(self.usage_percent, 1),
            "is_active": self.is_active,
        }


@dataclass
class Customer:
    """Customer record."""

    id: str
    email: str
    company: str
    created_at: datetime
    status: CustomerStatus = CustomerStatus.TRIAL
    tier: CustomerTier = CustomerTier.FREE

    name: str = ""
    role: str = ""
    team_size: str = ""
    industry: str = ""
    use_case: str = ""

    mrr_cents: int = 0
    lifetime_value_cents: int = 0

    total_investigations: int = 0
    total_tokens_used: int = 0
    last_active_at: datetime | None = None

    trial_id: str = ""
    stripe_customer_id: str = ""

    onboarding_completed: bool = False
    onboarding_step: int = 0

    health_score: float = 0.0
    nps_score: int | None = None
    feedback: str = ""

    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mrr_usd(self) -> float:
        """Monthly recurring revenue in USD."""
        return self.mrr_cents / 100

    @property
    def is_paying(self) -> bool:
        """Check if customer is paying."""
        return self.mrr_cents > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self._mask_email(),
            "company": self.company,
            "name": self.name,
            "status": self.status.value,
            "tier": self.tier.value,
            "created_at": self.created_at.isoformat(),
            "mrr_usd": self.mrr_usd,
            "total_investigations": self.total_investigations,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "onboarding_completed": self.onboarding_completed,
            "health_score": round(self.health_score, 1),
            "is_paying": self.is_paying,
            "tags": self.tags,
        }

    def _mask_email(self) -> str:
        """Mask email for privacy."""
        if "@" not in self.email:
            return "***"
        local, domain = self.email.split("@", 1)
        if len(local) <= 2:
            masked = "*" * len(local)
        else:
            masked = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked}@{domain}"


_customer_store: dict[str, Customer] = {}
_email_index: dict[str, str] = {}
_trial_store: dict[str, Trial] = {}
_usage_store: dict[str, list[UsageRecord]] = {}


def _generate_id(prefix: str) -> str:
    """Generate unique ID with prefix."""
    return f"{prefix}_{secrets.token_hex(8)}"


def create_customer(
    email: str,
    company: str,
    name: str = "",
    role: str = "",
    team_size: str = "",
    industry: str = "",
    use_case: str = "",
    source: str = "",
) -> tuple[Customer | None, str]:
    """Create a new customer.

    Returns:
        Tuple of (customer, message).
    """
    email = email.strip().lower()

    if not email or "@" not in email:
        return None, "Valid email required"

    if email in _email_index:
        existing_id = _email_index[email]
        existing = _customer_store.get(existing_id)
        if existing:
            return existing, "Customer already exists"

    customer = Customer(
        id=_generate_id("cust"),
        email=email,
        company=company,
        name=name,
        role=role,
        team_size=team_size,
        industry=industry,
        use_case=use_case,
        created_at=datetime.now(timezone.utc),
    )

    if source:
        customer.tags.append(f"source:{source}")

    customer.health_score = 50.0

    _customer_store[customer.id] = customer
    _email_index[email] = customer.id
    _usage_store[customer.id] = []

    log.info(
        "Customer created: company=%s tier=%s",
        company,
        customer.tier.value,
    )

    return customer, "Customer created successfully"


def create_trial(
    customer_id: str,
    duration_days: int = 14,
    investigations_allowed: int = 10,
    tokens_allowed: int = 100000,
) -> tuple[Trial | None, str]:
    """Create a trial for a customer.

    Returns:
        Tuple of (trial, message).
    """
    customer = _customer_store.get(customer_id)
    if not customer:
        return None, "Customer not found"

    if customer.trial_id:
        existing_trial = _trial_store.get(customer.trial_id)
        if existing_trial and existing_trial.is_active:
            return existing_trial, "Trial already active"

    now = datetime.now(timezone.utc)

    trial = Trial(
        id=_generate_id("trial"),
        customer_id=customer_id,
        started_at=now,
        expires_at=now + timedelta(days=duration_days),
        investigations_allowed=investigations_allowed,
        tokens_allowed=tokens_allowed,
    )

    _trial_store[trial.id] = trial
    customer.trial_id = trial.id
    customer.status = CustomerStatus.TRIAL

    log.info(
        "Trial created: customer=%s duration=%d days",
        customer.company,
        duration_days,
    )

    return trial, "Trial started successfully"


def get_customer(customer_id: str = "", email: str = "") -> Customer | None:
    """Get customer by ID or email."""
    if customer_id:
        return _customer_store.get(customer_id)

    if email:
        email = email.strip().lower()
        cust_id = _email_index.get(email)
        if cust_id:
            return _customer_store.get(cust_id)

    return None


def get_trial(trial_id: str) -> Trial | None:
    """Get trial by ID."""
    return _trial_store.get(trial_id)


def record_usage(
    customer_id: str,
    event_type: str,
    investigation_id: str = "",
    tokens_used: int = 0,
    duration_seconds: float = 0.0,
    **metadata: Any,
) -> bool:
    """Record customer usage event."""
    customer = _customer_store.get(customer_id)
    if not customer:
        return False

    record = UsageRecord(
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        investigation_id=investigation_id,
        tokens_used=tokens_used,
        duration_seconds=duration_seconds,
        metadata=metadata,
    )

    if customer_id not in _usage_store:
        _usage_store[customer_id] = []
    _usage_store[customer_id].append(record)

    customer.last_active_at = record.timestamp

    if event_type == "investigation":
        customer.total_investigations += 1

    if tokens_used > 0:
        customer.total_tokens_used += tokens_used

    if customer.trial_id:
        trial = _trial_store.get(customer.trial_id)
        if trial and trial.is_active:
            if event_type == "investigation":
                trial.investigations_used += 1
            trial.tokens_used += tokens_used

    _update_health_score(customer)

    return True


def _update_health_score(customer: Customer) -> None:
    """Update customer health score based on engagement."""
    score = 50.0

    if customer.total_investigations > 0:
        score += min(20, customer.total_investigations * 2)

    if customer.last_active_at:
        days_inactive = (datetime.now(timezone.utc) - customer.last_active_at).days
        if days_inactive == 0:
            score += 15
        elif days_inactive <= 3:
            score += 10
        elif days_inactive <= 7:
            score += 5
        else:
            score -= min(20, days_inactive)

    if customer.onboarding_completed:
        score += 10

    if customer.is_paying:
        score += 15

    if customer.nps_score is not None:
        if customer.nps_score >= 9:
            score += 10
        elif customer.nps_score >= 7:
            score += 5
        elif customer.nps_score <= 6:
            score -= 10

    customer.health_score = max(0, min(100, score))


def convert_trial(
    customer_id: str,
    tier: CustomerTier = CustomerTier.STARTER,
    mrr_cents: int = 0,
    stripe_customer_id: str = "",
) -> tuple[bool, str]:
    """Convert a trial customer to paid."""
    customer = _customer_store.get(customer_id)
    if not customer:
        return False, "Customer not found"

    if customer.trial_id:
        trial = _trial_store.get(customer.trial_id)
        if trial:
            trial.status = TrialStatus.CONVERTED
            trial.converted_at = datetime.now(timezone.utc)

    customer.status = CustomerStatus.ACTIVE
    customer.tier = tier
    customer.mrr_cents = mrr_cents
    customer.stripe_customer_id = stripe_customer_id
    customer.tags.append("converted")
    customer.tags.append(f"tier:{tier.value}")

    log.info(
        "Trial converted: customer=%s tier=%s mrr=$%.2f",
        customer.company,
        tier.value,
        mrr_cents / 100,
    )

    return True, "Trial converted successfully"


def get_active_trials() -> list[Trial]:
    """Get all active trials."""
    now = datetime.now(timezone.utc)
    active = []

    for trial in _trial_store.values():
        if trial.status == TrialStatus.ACTIVE:
            if now >= trial.expires_at:
                trial.status = TrialStatus.EXPIRED
            else:
                active.append(trial)

    return sorted(active, key=lambda t: t.expires_at)


def get_customer_stats() -> dict[str, Any]:
    """Get customer statistics for dashboard."""
    customers = list(_customer_store.values())
    trials = list(_trial_store.values())

    if not customers:
        return {
            "total_customers": 0,
            "total_trials": 0,
            "active_trials": 0,
            "paying_customers": 0,
            "mrr_usd": 0.0,
            "conversion_rate": 0.0,
            "avg_health_score": 0.0,
        }

    paying = [c for c in customers if c.is_paying]
    active_trials = get_active_trials()
    converted_trials = [t for t in trials if t.status == TrialStatus.CONVERTED]

    total_mrr = sum(c.mrr_cents for c in paying)

    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}

    for c in customers:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
        by_tier[c.tier.value] = by_tier.get(c.tier.value, 0) + 1

    return {
        "total_customers": len(customers),
        "total_trials": len(trials),
        "active_trials": len(active_trials),
        "paying_customers": len(paying),
        "mrr_usd": total_mrr / 100,
        "conversion_rate": len(converted_trials) / len(trials) if trials else 0.0,
        "avg_health_score": sum(c.health_score for c in customers) / len(customers),
        "by_status": by_status,
        "by_tier": by_tier,
        "total_investigations": sum(c.total_investigations for c in customers),
    }


class CustomerManager:
    """High-level customer management for API integration."""

    def create(
        self,
        email: str,
        company: str,
        start_trial: bool = True,
        trial_days: int = 14,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create customer and optionally start trial."""
        customer, msg = create_customer(email, company, **kwargs)

        if not customer:
            return {"success": False, "error": msg}

        result = {
            "success": True,
            "message": msg,
            "customer": customer.to_dict(),
        }

        if start_trial:
            trial, trial_msg = create_trial(customer.id, duration_days=trial_days)
            if trial:
                result["trial"] = trial.to_dict()
                result["message"] = f"{msg}. {trial_msg}"

        return result

    def get_dashboard(self) -> dict[str, Any]:
        """Get customer dashboard data."""
        stats = get_customer_stats()

        return {
            "stats": stats,
            "active_trials": [t.to_dict() for t in get_active_trials()[:10]],
            "recent_customers": [
                c.to_dict()
                for c in sorted(
                    _customer_store.values(),
                    key=lambda x: x.created_at,
                    reverse=True,
                )[:10]
            ],
        }


def clear_customers() -> None:
    """Clear all customer data (for testing)."""
    _customer_store.clear()
    _email_index.clear()
    _trial_store.clear()
    _usage_store.clear()
