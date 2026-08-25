"""Customer and trial management for conversion tracking.

Tracks the full customer journey from trial to paid conversion,
with usage metering and engagement analytics.

Three things were wrong with the version this replaces, and all three were
reachable from the API:

* It was four module-level dicts. Every customer, trial and metered usage event
  was lost on restart, on deploy, and between worker processes — while the
  endpoints kept returning 200.
* Those dicts were global, so on a multi-tenant deployment every tenant's
  `GET /customers/dashboard` returned every other tenant's customers, contacts,
  health scores and MRR. Rows carry `org_id` now and every statement filters on
  it; the routes are additionally operator-only, since this is the deployment's
  own back-office rather than a per-tenant feature.
* `convert_trial` wrote whatever `mrr_cents` the request body carried straight
  into the revenue total, so `POST /customers/convert` was an API for inventing
  MRR. The amount now comes from the pricing catalog for the plan being
  converted to; only Enterprise, which has no list price, takes a contracted
  figure from the caller.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update

from app.database.orgs import ensure_org_row, org_uuid

log = logging.getLogger("omnipotent.customers")


class CustomerTier(str, Enum):
    """Customer pricing tier.

    These are the names in `app.billing.plans.PRICING_PLANS`, deliberately: the
    tier is what conversion reads a price off, so a tier the catalog does not
    know is a tier with no defensible MRR. The previous set — free / starter /
    pro / enterprise — matched no catalog entry, which is how "pro" could be
    converted at any MRR the caller felt like sending.
    """

    STARTER = "starter"
    TEAM = "team"
    GROWTH = "growth"
    BUSINESS = "business"
    SCALE = "scale"
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
    tier: CustomerTier = CustomerTier.STARTER

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_id(prefix: str) -> str:
    """Generate unique ID with prefix."""
    return f"{prefix}_{secrets.token_hex(8)}"


def _utc(value: datetime | None) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite, the default database, has no timezone type, so a
    `DateTime(timezone=True)` column round trips naive. Everything written here
    is UTC; without this, `Trial.is_active` compares naive against aware and
    raises TypeError.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _enum(cls: Any, value: str, fallback: Any) -> Any:
    """Read a stored enum value back, tolerating one written by an older build."""
    try:
        return cls(value)
    except ValueError:
        return fallback


def _to_customer(row: Any) -> Customer:
    return Customer(
        id=row.id,
        email=row.email,
        company=row.company,
        created_at=_utc(row.created_at),
        status=_enum(CustomerStatus, row.status, CustomerStatus.TRIAL),
        tier=_enum(CustomerTier, row.tier, CustomerTier.STARTER),
        name=row.name,
        role=row.role,
        team_size=row.team_size,
        industry=row.industry,
        use_case=row.use_case,
        mrr_cents=row.mrr_cents,
        lifetime_value_cents=row.lifetime_value_cents,
        total_investigations=row.total_investigations,
        total_tokens_used=row.total_tokens_used,
        last_active_at=_utc(row.last_active_at),
        trial_id=row.trial_id,
        stripe_customer_id=row.stripe_customer_id,
        onboarding_completed=row.onboarding_completed,
        onboarding_step=row.onboarding_step,
        health_score=row.health_score,
        nps_score=row.nps_score,
        feedback=row.feedback,
        tags=list(row.tags or []),
        metadata=dict(row.meta or {}),
    )


def _to_trial(row: Any) -> Trial:
    return Trial(
        id=row.id,
        customer_id=row.customer_id,
        started_at=_utc(row.started_at),
        expires_at=_utc(row.expires_at),
        status=_enum(TrialStatus, row.status, TrialStatus.ACTIVE),
        investigations_allowed=row.investigations_allowed,
        investigations_used=row.investigations_used,
        tokens_allowed=row.tokens_allowed,
        tokens_used=row.tokens_used,
        converted_at=_utc(row.converted_at),
        cancelled_at=_utc(row.cancelled_at),
        cancel_reason=row.cancel_reason,
    )


async def _customer_row(session: Any, org: UUID, customer_id: str = "", email: str = "") -> Any:
    from app.database.growth_models import CustomerDB

    stmt = select(CustomerDB).where(CustomerDB.org_id == org)
    if customer_id:
        stmt = stmt.where(CustomerDB.id == customer_id)
    elif email:
        stmt = stmt.where(CustomerDB.email == email.strip().lower())
    else:
        return None
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_customer(
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
    from app.database.engine import get_session
    from app.database.growth_models import CustomerDB

    email = email.strip().lower()

    if not email or "@" not in email:
        return None, "Valid email required"

    async with get_session() as session:
        org = await ensure_org_row(session)

        existing = await _customer_row(session, org, email=email)
        if existing is not None:
            return _to_customer(existing), "Customer already exists"

        row = CustomerDB(
            id=_generate_id("cust"),
            org_id=org,
            email=email,
            company=company,
            name=name,
            role=role,
            team_size=team_size,
            industry=industry,
            use_case=use_case,
            status=CustomerStatus.TRIAL.value,
            tier=CustomerTier.STARTER.value,
            health_score=50.0,
            tags=[f"source:{source}"] if source else [],
            meta={},
        )
        session.add(row)
        await session.commit()

        log.info("Customer created: company=%s tier=%s", company, row.tier)
        return _to_customer(row), "Customer created successfully"


async def create_trial(
    customer_id: str,
    duration_days: int = 14,
    investigations_allowed: int = 10,
    tokens_allowed: int = 100000,
) -> tuple[Trial | None, str]:
    """Create a trial for a customer.

    Returns:
        Tuple of (trial, message).
    """
    from app.database.engine import get_session
    from app.database.growth_models import TrialDB

    async with get_session() as session:
        org = org_uuid()
        customer = await _customer_row(session, org, customer_id=customer_id)
        if customer is None:
            return None, "Customer not found"

        if customer.trial_id:
            existing = (
                await session.execute(select(TrialDB).where(TrialDB.id == customer.trial_id))
            ).scalar_one_or_none()
            if existing is not None and _to_trial(existing).is_active:
                return _to_trial(existing), "Trial already active"

        now = _now()
        trial = TrialDB(
            id=_generate_id("trial"),
            org_id=org,
            customer_id=customer_id,
            status=TrialStatus.ACTIVE.value,
            started_at=now,
            expires_at=now + timedelta(days=duration_days),
            investigations_allowed=investigations_allowed,
            tokens_allowed=tokens_allowed,
        )
        session.add(trial)
        customer.trial_id = trial.id
        customer.status = CustomerStatus.TRIAL.value
        await session.commit()

        log.info("Trial created: customer=%s duration=%d days", customer.company, duration_days)
        return _to_trial(trial), "Trial started successfully"


async def get_customer(customer_id: str = "", email: str = "") -> Customer | None:
    """Get customer by ID or email, within the calling org."""
    from app.database.engine import get_session

    async with get_session() as session:
        row = await _customer_row(session, org_uuid(), customer_id=customer_id, email=email)
        return _to_customer(row) if row else None


async def get_trial(trial_id: str) -> Trial | None:
    """Get trial by ID, within the calling org."""
    from app.database.engine import get_session
    from app.database.growth_models import TrialDB

    async with get_session() as session:
        row = (
            await session.execute(
                select(TrialDB).where(TrialDB.id == trial_id, TrialDB.org_id == org_uuid())
            )
        ).scalar_one_or_none()
        return _to_trial(row) if row else None


async def record_usage(
    customer_id: str,
    event_type: str,
    investigation_id: str = "",
    tokens_used: int = 0,
    duration_seconds: float = 0.0,
    **metadata: Any,
) -> bool:
    """Record customer usage event."""
    from app.database.engine import get_session
    from app.database.growth_models import CustomerUsageRecordDB, TrialDB

    async with get_session() as session:
        org = org_uuid()
        customer = await _customer_row(session, org, customer_id=customer_id)
        if customer is None:
            return False

        recorded_at = _now()
        session.add(
            CustomerUsageRecordDB(
                org_id=org,
                customer_id=customer_id,
                event_type=event_type,
                investigation_id=investigation_id,
                tokens_used=tokens_used,
                duration_seconds=duration_seconds,
                meta=dict(metadata),
                recorded_at=recorded_at,
            )
        )

        customer.last_active_at = recorded_at
        if event_type == "investigation":
            customer.total_investigations += 1
        if tokens_used > 0:
            customer.total_tokens_used += tokens_used

        if customer.trial_id:
            trial = (
                await session.execute(select(TrialDB).where(TrialDB.id == customer.trial_id))
            ).scalar_one_or_none()
            if trial is not None and _to_trial(trial).is_active:
                if event_type == "investigation":
                    trial.investigations_used += 1
                trial.tokens_used += tokens_used

        customer.health_score = _health_score(customer)
        await session.commit()
        return True


def _health_score(customer: Any) -> float:
    """Engagement score for a customer row, 0-100.

    Takes the row (or anything with the same attributes) and returns the score
    rather than mutating in place, so the caller decides when it is written.
    """
    score = 50.0

    if customer.total_investigations > 0:
        score += min(20, customer.total_investigations * 2)

    last_active = _utc(customer.last_active_at)
    if last_active:
        days_inactive = (_now() - last_active).days
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

    if customer.mrr_cents > 0:
        score += 15

    if customer.nps_score is not None:
        if customer.nps_score >= 9:
            score += 10
        elif customer.nps_score >= 7:
            score += 5
        elif customer.nps_score <= 6:
            score -= 10

    return max(0.0, min(100.0, score))


def listed_mrr_cents(tier: CustomerTier, annual: bool = False) -> int | None:
    """The catalog's monthly price for a tier, or None when it has no list price.

    Enterprise is quoted per contract and Starter is free, so both return None
    and the caller has to decide what that means rather than being handed a
    number the price list does not support.
    """
    from app.billing.plans import PRICING_PLANS

    plan = PRICING_PLANS.get(tier.value)
    if plan is None:
        return None
    cents = plan.annual_price_cents // 12 if annual else plan.monthly_price_cents
    return cents or None


async def convert_trial(
    customer_id: str,
    tier: CustomerTier = CustomerTier.TEAM,
    annual: bool = False,
    stripe_customer_id: str = "",
    contracted_mrr_cents: int | None = None,
) -> tuple[bool, str]:
    """Convert a trial customer to paid.

    The MRR written here is the catalog price for `tier`, not a number from the
    request body: the previous version stored whatever `mrr_cents` the caller
    sent, so `POST /customers/convert` could set the dashboard's revenue total
    to anything. `contracted_mrr_cents` is accepted only for Enterprise, which
    is quoted per deal and therefore has no list price to read.
    """
    from app.database.engine import get_session
    from app.database.growth_models import TrialDB

    listed = listed_mrr_cents(tier, annual)
    if listed is None:
        if tier is not CustomerTier.ENTERPRISE:
            return False, f"The {tier.value} plan has no recurring price to convert to."
        if contracted_mrr_cents is None or contracted_mrr_cents < 0:
            return (
                False,
                "Enterprise is quoted per contract: pass contracted_mrr_cents.",
            )
        mrr_cents = contracted_mrr_cents
    else:
        mrr_cents = listed

    async with get_session() as session:
        org = org_uuid()
        customer = await _customer_row(session, org, customer_id=customer_id)
        if customer is None:
            return False, "Customer not found"

        if customer.trial_id:
            trial = (
                await session.execute(select(TrialDB).where(TrialDB.id == customer.trial_id))
            ).scalar_one_or_none()
            if trial is not None:
                trial.status = TrialStatus.CONVERTED.value
                trial.converted_at = _now()

        customer.status = CustomerStatus.ACTIVE.value
        customer.tier = tier.value
        customer.mrr_cents = mrr_cents
        customer.stripe_customer_id = stripe_customer_id
        customer.tags = [*(customer.tags or []), "converted", f"tier:{tier.value}"]
        customer.health_score = _health_score(customer)
        await session.commit()

        log.info(
            "Trial converted: customer=%s tier=%s mrr=$%.2f",
            customer.company,
            tier.value,
            mrr_cents / 100,
        )
        return True, "Trial converted successfully"


async def get_active_trials() -> list[Trial]:
    """Get all active trials for the calling org, expiring any that are due.

    Expiry used to happen as a side effect of somebody calling this function, so
    a trial nobody listed stayed `active` forever. It is a single UPDATE now,
    which also means the status in the table is right for anyone reading it
    directly.
    """
    from app.database.engine import get_session
    from app.database.growth_models import TrialDB

    org = org_uuid()
    async with get_session() as session:
        await session.execute(
            update(TrialDB)
            .where(
                TrialDB.org_id == org,
                TrialDB.status == TrialStatus.ACTIVE.value,
                TrialDB.expires_at <= _now(),
            )
            .values(status=TrialStatus.EXPIRED.value)
        )
        await session.commit()

        rows = (
            await session.execute(
                select(TrialDB)
                .where(TrialDB.org_id == org, TrialDB.status == TrialStatus.ACTIVE.value)
                .order_by(TrialDB.expires_at)
            )
        ).scalars().all()
        return [_to_trial(r) for r in rows]


async def get_customer_stats() -> dict[str, Any]:
    """Get customer statistics for dashboard, scoped to the calling org."""
    from app.database.engine import get_session
    from app.database.growth_models import CustomerDB, TrialDB

    org = org_uuid()
    active_trials = await get_active_trials()

    async with get_session() as session:
        totals = (
            await session.execute(
                select(
                    func.count(CustomerDB.id),
                    func.coalesce(func.sum(CustomerDB.mrr_cents), 0),
                    func.coalesce(func.avg(CustomerDB.health_score), 0.0),
                    func.coalesce(func.sum(CustomerDB.total_investigations), 0),
                ).where(CustomerDB.org_id == org)
            )
        ).one()
        total_customers, total_mrr, avg_health, total_investigations = totals

        if not total_customers:
            return {
                "total_customers": 0,
                "total_trials": 0,
                "active_trials": 0,
                "paying_customers": 0,
                "mrr_usd": 0.0,
                "conversion_rate": 0.0,
                "avg_health_score": 0.0,
            }

        paying = (
            await session.execute(
                select(func.count(CustomerDB.id)).where(
                    CustomerDB.org_id == org, CustomerDB.mrr_cents > 0
                )
            )
        ).scalar() or 0

        by_status = {
            status: count
            for status, count in (
                await session.execute(
                    select(CustomerDB.status, func.count(CustomerDB.id))
                    .where(CustomerDB.org_id == org)
                    .group_by(CustomerDB.status)
                )
            ).all()
        }
        by_tier = {
            tier: count
            for tier, count in (
                await session.execute(
                    select(CustomerDB.tier, func.count(CustomerDB.id))
                    .where(CustomerDB.org_id == org)
                    .group_by(CustomerDB.tier)
                )
            ).all()
        }

        total_trials = (
            await session.execute(
                select(func.count(TrialDB.id)).where(TrialDB.org_id == org)
            )
        ).scalar() or 0
        converted_trials = (
            await session.execute(
                select(func.count(TrialDB.id)).where(
                    TrialDB.org_id == org, TrialDB.status == TrialStatus.CONVERTED.value
                )
            )
        ).scalar() or 0

    return {
        "total_customers": total_customers,
        "total_trials": total_trials,
        "active_trials": len(active_trials),
        "paying_customers": paying,
        "mrr_usd": total_mrr / 100,
        "conversion_rate": converted_trials / total_trials if total_trials else 0.0,
        "avg_health_score": float(avg_health),
        "by_status": by_status,
        "by_tier": by_tier,
        "total_investigations": total_investigations,
    }


class CustomerManager:
    """High-level customer management for API integration."""

    async def create(
        self,
        email: str,
        company: str,
        start_trial: bool = True,
        trial_days: int = 14,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create customer and optionally start trial."""
        customer, msg = await create_customer(email, company, **kwargs)

        if not customer:
            return {"success": False, "error": msg}

        result: dict[str, Any] = {
            "success": True,
            "message": msg,
            "customer": customer.to_dict(),
        }

        if start_trial:
            trial, trial_msg = await create_trial(customer.id, duration_days=trial_days)
            if trial:
                result["trial"] = trial.to_dict()
                result["message"] = f"{msg}. {trial_msg}"

        return result

    async def get_dashboard(self) -> dict[str, Any]:
        """Get customer dashboard data."""
        from app.database.engine import get_session
        from app.database.growth_models import CustomerDB

        stats = await get_customer_stats()
        trials = await get_active_trials()

        async with get_session() as session:
            recent = (
                await session.execute(
                    select(CustomerDB)
                    .where(CustomerDB.org_id == org_uuid())
                    .order_by(CustomerDB.created_at.desc())
                    .limit(10)
                )
            ).scalars().all()

        return {
            "stats": stats,
            "active_trials": [t.to_dict() for t in trials[:10]],
            "recent_customers": [_to_customer(c).to_dict() for c in recent],
        }


async def clear_customers() -> None:
    """Clear all customer data (for testing)."""
    from app.database.engine import get_session
    from app.database.growth_models import CustomerDB, CustomerUsageRecordDB, TrialDB

    async with get_session() as session:
        await session.execute(delete(CustomerUsageRecordDB))
        await session.execute(delete(TrialDB))
        await session.execute(delete(CustomerDB))
        await session.commit()
