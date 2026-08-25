"""SQLAlchemy models for the growth surfaces: waitlist and customer/trial CRM.

`app/waitlist/manager.py` and `app/customers/manager.py` each kept their records
in module-level dicts. Everything they held — every early-access signup with its
position and referral code, every customer, trial and usage record — was lost on
restart, on deploy, and on every additional worker process (two uvicorn workers
answered from two different waitlists). These are the tables behind them.

Two scoping decisions:

* `waitlist_entries` has no `org_id`. A visitor joining the waitlist has no
  account yet — that is the point of a waitlist — so the list is global to the
  deployment, and `position` is a global sequence with a unique constraint so two
  concurrent signups cannot be handed the same number.
* `customers`, `trials` and `customer_usage_records` do carry `org_id`. The
  dicts they replace were global, so on a multi-tenant deployment every tenant's
  `GET /customers/dashboard` listed every other tenant's customers, contacts and
  revenue.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base
from app.database.types import JSONB, UUID


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WaitlistEntryDB(Base):
    """One early-access signup."""

    __tablename__ = "waitlist_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="landing_page")

    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    team_size: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    use_case: Mapped[str] = mapped_column(Text, nullable=False, default="")

    referral_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    referred_by: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    referral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    utm_source: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    utm_medium: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    utm_campaign: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    # A truncated SHA-256 of the client IP, never the address itself: enough to
    # spot one host signing up repeatedly, not enough to identify the visitor.
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("email", name="uq_waitlist_email"),
        # Two signups landing at once must not be told they are both #42.
        UniqueConstraint("position", name="uq_waitlist_position"),
        Index("ix_waitlist_referral_code", "referral_code"),
    )


class CustomerDB(Base):
    """A customer record in the conversion-tracking CRM."""

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    team_size: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    industry: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    use_case: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="trial")
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")

    mrr_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_value_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    total_investigations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trial_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    stripe_customer_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    onboarding_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    health_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    nps_score: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")

    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_customer_org_email"),
        Index("ix_customers_org_created", "org_id", "created_at"),
    )


class TrialDB(Base):
    """A trial attached to one customer."""

    __tablename__ = "trials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigations_allowed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    investigations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_allowed: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        Index("ix_trials_org_status_expires", "org_id", "status", "expires_at"),
    )


class CustomerUsageRecordDB(Base):
    """One metered event against a customer (and, while it runs, their trial)."""

    __tablename__ = "customer_usage_records"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    investigation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_customer_usage_customer_time", "customer_id", "recorded_at"),
    )
