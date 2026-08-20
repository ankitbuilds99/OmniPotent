"""Customer and trial management for conversion tracking.

Tracks the full customer journey from trial to paid conversion,
with usage metering and engagement analytics.

Persistence
-----------
Every function here is backed by Postgres (or SQLite in single-node/dev setups)
through :mod:`app.database`. It used to be four module-level dicts —
``_customer_store``, ``_email_index``, ``_trial_store``, ``_usage_store`` — which
made this the most damaging instance of in-process state in the app: a customer
created on one replica did not exist on the next request, usage records were
split across pods, and every restart, deploy or scale-down silently destroyed the
data invoices are computed from. Nothing here has a cache; each call reads the
database, because two processes disagreeing about a quota is the bug being fixed.

The dataclasses below are read models, not ORM rows: callers get a detached
snapshot with the same fields and ``to_dict()`` shape as before, so mutating one
does not write anything back. Writes go through the functions.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import CRMCustomer, CRMTrial, CRMUsageEvent

log = logging.getLogger("omnipotent.customers")


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


# --------------------------------------------------------------------------- #
# Persistence plumbing
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session with the schema guaranteed to exist.

    ``ensure_schema()`` is a no-op after the first call (and after the API
    lifespan, which runs it at startup). It is here so that a caller outside a
    request — a test, a worker, a script — isn't the one that discovers the
    tables were never created.
    """
    await ensure_schema()
    async with get_session() as session:
        yield session


def _aware(dt: datetime) -> datetime:
    """Attach UTC to a naive timestamp read back from the database.

    SQLite has no timezone type: SQLAlchemy writes the UTC wall clock and returns
    it naive. Everything in this module stores UTC, so re-attaching it is exact —
    and necessary, because ``Trial.is_active`` compares against an aware ``now()``
    and would raise TypeError on a naive value. Postgres TIMESTAMPTZ already
    returns aware values, which this leaves untouched.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _aware_opt(dt: datetime | None) -> datetime | None:
    return _aware(dt) if dt is not None else None


def _to_customer(row: CRMCustomer) -> Customer:
    """Map a stored row to the detached read model callers expect."""
    return Customer(
        id=row.id,
        email=row.email,
        company=row.company,
        created_at=_aware(row.created_at),
        status=CustomerStatus(row.status),
        tier=CustomerTier(row.tier),
        name=row.name,
        role=row.role,
        team_size=row.team_size,
        industry=row.industry,
        use_case=row.use_case,
        mrr_cents=row.mrr_cents,
        lifetime_value_cents=row.lifetime_value_cents,
        total_investigations=row.total_investigations,
        total_tokens_used=row.total_tokens_used,
        last_active_at=_aware_opt(row.last_active_at),
        trial_id=row.trial_id,
        stripe_customer_id=row.stripe_customer_id,
        onboarding_completed=row.onboarding_completed,
        onboarding_step=row.onboarding_step,
        health_score=row.health_score,
        nps_score=row.nps_score,
        feedback=row.feedback,
        tags=list(row.tags or []),
        metadata=dict(row.extra or {}),
    )


def _to_trial(row: CRMTrial) -> Trial:
    return Trial(
        id=row.id,
        customer_id=row.customer_id,
        started_at=_aware(row.started_at),
        expires_at=_aware(row.expires_at),
        status=TrialStatus(row.status),
        investigations_allowed=row.investigations_allowed,
        investigations_used=row.investigations_used,
        tokens_allowed=row.tokens_allowed,
        tokens_used=row.tokens_used,
        converted_at=_aware_opt(row.converted_at),
        cancelled_at=_aware_opt(row.cancelled_at),
        cancel_reason=row.cancel_reason,
    )


def _generate_id(prefix: str) -> str:
    """Generate unique ID with prefix."""
    return f"{prefix}_{secrets.token_hex(8)}"


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #


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
    email = email.strip().lower()

    if not email or "@" not in email:
        return None, "Valid email required"

    tags = [f"source:{source}"] if source else []

    async with _session() as session:
        existing = await session.scalar(
            select(CRMCustomer).where(CRMCustomer.email == email)
        )
        if existing is not None:
            return _to_customer(existing), "Customer already exists"

        row = CRMCustomer(
            id=_generate_id("cust"),
            email=email,
            company=company,
            name=name,
            role=role,
            team_size=team_size,
            industry=industry,
            use_case=use_case,
            status=CustomerStatus.TRIAL.value,
            tier=CustomerTier.FREE.value,
            health_score=50.0,
            tags=tags,
            extra={},
            created_at=datetime.now(timezone.utc),
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            # Another process inserted the same email between the SELECT above
            # and this INSERT. The unique constraint is what makes "one customer
            # per email" true across replicas; the pre-check is only an
            # optimisation. Return theirs, exactly as the pre-check would have.
            await session.rollback()
            existing = await session.scalar(
                select(CRMCustomer).where(CRMCustomer.email == email)
            )
            if existing is None:
                raise
            return _to_customer(existing), "Customer already exists"

        log.info(
            "Customer created: company=%s tier=%s",
            company,
            row.tier,
        )
        return _to_customer(row), "Customer created successfully"


async def get_customer(customer_id: str = "", email: str = "") -> Customer | None:
    """Get customer by ID or email."""
    if not customer_id and not email:
        return None

    async with _session() as session:
        if customer_id:
            row = await session.get(CRMCustomer, customer_id)
        else:
            row = await session.scalar(
                select(CRMCustomer).where(CRMCustomer.email == email.strip().lower())
            )
        return _to_customer(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Trials
# --------------------------------------------------------------------------- #


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
    async with _session() as session:
        customer = await session.get(CRMCustomer, customer_id)
        if customer is None:
            return None, "Customer not found"

        if customer.trial_id:
            existing = await session.get(CRMTrial, customer.trial_id)
            if existing is not None and _to_trial(existing).is_active:
                return _to_trial(existing), "Trial already active"

        now = datetime.now(timezone.utc)
        trial = CRMTrial(
            id=_generate_id("trial"),
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

        log.info(
            "Trial created: customer=%s duration=%d days",
            customer.company,
            duration_days,
        )
        return _to_trial(trial), "Trial started successfully"


async def get_trial(trial_id: str) -> Trial | None:
    """Get trial by ID."""
    async with _session() as session:
        row = await session.get(CRMTrial, trial_id)
        return _to_trial(row) if row is not None else None


async def get_active_trials() -> list[Trial]:
    """Get all active trials, expiring any whose window has closed.

    The expiry sweep is a write, and it is why this is not a pure read: a trial
    past ``expires_at`` is transitioned to EXPIRED here rather than being
    recomputed by every reader.
    """
    now = datetime.now(timezone.utc)

    async with _session() as session:
        await session.execute(
            update(CRMTrial)
            .where(CRMTrial.status == TrialStatus.ACTIVE.value, CRMTrial.expires_at <= now)
            .values(status=TrialStatus.EXPIRED.value)
        )
        await session.commit()

        rows = await session.scalars(
            select(CRMTrial)
            .where(CRMTrial.status == TrialStatus.ACTIVE.value)
            .order_by(CRMTrial.expires_at)
        )
        return [_to_trial(r) for r in rows]


async def convert_trial(
    customer_id: str,
    tier: CustomerTier = CustomerTier.STARTER,
    mrr_cents: int = 0,
    stripe_customer_id: str = "",
) -> tuple[bool, str]:
    """Convert a trial customer to paid."""
    async with _session() as session:
        customer = await session.get(CRMCustomer, customer_id)
        if customer is None:
            return False, "Customer not found"

        if customer.trial_id:
            trial = await session.get(CRMTrial, customer.trial_id)
            if trial is not None:
                trial.status = TrialStatus.CONVERTED.value
                trial.converted_at = datetime.now(timezone.utc)

        customer.status = CustomerStatus.ACTIVE.value
        customer.tier = tier.value
        customer.mrr_cents = mrr_cents
        customer.stripe_customer_id = stripe_customer_id
        # Reassigned rather than appended to: a JSON column is only marked dirty
        # on assignment, so `customer.tags.append(...)` would be silently dropped.
        customer.tags = [*(customer.tags or []), "converted", f"tier:{tier.value}"]
        await session.commit()

        log.info(
            "Trial converted: customer=%s tier=%s mrr=$%.2f",
            customer.company,
            tier.value,
            mrr_cents / 100,
        )
        return True, "Trial converted successfully"


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


async def record_usage(
    customer_id: str,
    event_type: str,
    investigation_id: str = "",
    tokens_used: int = 0,
    duration_seconds: float = 0.0,
    **metadata: Any,
) -> bool:
    """Record customer usage event.

    Counters are incremented in SQL (``col = col + n``) rather than read-modify-
    written in Python: these are the numbers a customer is billed from and a
    concurrent investigation on another replica must not overwrite them.
    """
    timestamp = datetime.now(timezone.utc)
    investigations_delta = 1 if event_type == "investigation" else 0

    async with _session() as session:
        customer = await session.get(CRMCustomer, customer_id)
        if customer is None:
            return False

        session.add(
            CRMUsageEvent(
                customer_id=customer_id,
                timestamp=timestamp,
                event_type=event_type,
                investigation_id=investigation_id,
                tokens_used=tokens_used,
                duration_seconds=duration_seconds,
                extra=metadata,
            )
        )

        await session.execute(
            update(CRMCustomer)
            .where(CRMCustomer.id == customer_id)
            .values(
                total_investigations=CRMCustomer.total_investigations + investigations_delta,
                total_tokens_used=CRMCustomer.total_tokens_used + tokens_used,
                last_active_at=timestamp,
            )
        )

        if customer.trial_id:
            trial = await session.get(CRMTrial, customer.trial_id)
            if trial is not None and _to_trial(trial).is_active:
                await session.execute(
                    update(CRMTrial)
                    .where(CRMTrial.id == trial.id)
                    .values(
                        investigations_used=CRMTrial.investigations_used + investigations_delta,
                        tokens_used=CRMTrial.tokens_used + tokens_used,
                    )
                )

        # Re-read so the health score is computed from the post-increment values.
        await session.refresh(customer)
        customer.health_score = _health_score(customer)
        await session.commit()

    return True


def _health_score(row: CRMCustomer) -> float:
    """Customer health score from engagement signals, 0-100."""
    score = 50.0

    if row.total_investigations > 0:
        score += min(20, row.total_investigations * 2)

    if row.last_active_at:
        days_inactive = (datetime.now(timezone.utc) - _aware(row.last_active_at)).days
        if days_inactive == 0:
            score += 15
        elif days_inactive <= 3:
            score += 10
        elif days_inactive <= 7:
            score += 5
        else:
            score -= min(20, days_inactive)

    if row.onboarding_completed:
        score += 10

    if row.mrr_cents > 0:
        score += 15

    if row.nps_score is not None:
        if row.nps_score >= 9:
            score += 10
        elif row.nps_score >= 7:
            score += 5
        elif row.nps_score <= 6:
            score -= 10

    return max(0.0, min(100.0, score))


async def get_customer_usage(customer_id: str, limit: int = 100) -> list[UsageRecord]:
    """Most recent usage events for a customer, newest first.

    The per-event log behind the aggregate counters on the customer row — the
    detail an invoice dispute is settled with.
    """
    async with _session() as session:
        rows = await session.scalars(
            select(CRMUsageEvent)
            .where(CRMUsageEvent.customer_id == customer_id)
            .order_by(CRMUsageEvent.timestamp.desc(), CRMUsageEvent.id.desc())
            .limit(limit)
        )
        return [
            UsageRecord(
                timestamp=_aware(r.timestamp),
                event_type=r.event_type,
                investigation_id=r.investigation_id,
                tokens_used=r.tokens_used,
                duration_seconds=r.duration_seconds,
                metadata=dict(r.extra or {}),
            )
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


async def get_customer_stats() -> dict[str, Any]:
    """Get customer statistics for dashboard.

    Aggregated in SQL rather than by loading every customer into memory — this
    is on the dashboard's hot path and the table only grows.
    """
    active_trials = await get_active_trials()

    async with _session() as session:
        total_customers = await session.scalar(select(func.count()).select_from(CRMCustomer)) or 0

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

        paying_customers = await session.scalar(
            select(func.count()).select_from(CRMCustomer).where(CRMCustomer.mrr_cents > 0)
        ) or 0
        total_mrr = await session.scalar(
            select(func.coalesce(func.sum(CRMCustomer.mrr_cents), 0)).where(
                CRMCustomer.mrr_cents > 0
            )
        ) or 0
        avg_health = await session.scalar(select(func.avg(CRMCustomer.health_score))) or 0.0
        total_investigations = await session.scalar(
            select(func.coalesce(func.sum(CRMCustomer.total_investigations), 0))
        ) or 0

        by_status = {
            status: count
            for status, count in (
                await session.execute(
                    select(CRMCustomer.status, func.count()).group_by(CRMCustomer.status)
                )
            ).all()
        }
        by_tier = {
            tier: count
            for tier, count in (
                await session.execute(
                    select(CRMCustomer.tier, func.count()).group_by(CRMCustomer.tier)
                )
            ).all()
        }

        total_trials = await session.scalar(select(func.count()).select_from(CRMTrial)) or 0
        converted_trials = await session.scalar(
            select(func.count())
            .select_from(CRMTrial)
            .where(CRMTrial.status == TrialStatus.CONVERTED.value)
        ) or 0

    return {
        "total_customers": total_customers,
        "total_trials": total_trials,
        "active_trials": len(active_trials),
        "paying_customers": paying_customers,
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

        result = {
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
        stats = await get_customer_stats()
        active_trials = await get_active_trials()

        async with _session() as session:
            recent = await session.scalars(
                select(CRMCustomer).order_by(CRMCustomer.created_at.desc()).limit(10)
            )
            recent_customers = [_to_customer(r).to_dict() for r in recent]

        return {
            "stats": stats,
            "active_trials": [t.to_dict() for t in active_trials[:10]],
            "recent_customers": recent_customers,
        }


async def clear_customers() -> None:
    """Delete all customer data (for testing)."""
    async with _session() as session:
        # Explicit child-first deletes: ON DELETE CASCADE is declared, but SQLite
        # does not enforce foreign keys unless PRAGMA foreign_keys is on.
        await session.execute(delete(CRMUsageEvent))
        await session.execute(delete(CRMTrial))
        await session.execute(delete(CRMCustomer))
        await session.commit()
