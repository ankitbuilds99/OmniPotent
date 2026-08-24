"""SQLAlchemy ORM models for production persistence.

All models inherit from Base and use UUID primary keys for multi-tenant safety.
Timestamps are auto-managed. Indexes are tuned for common query patterns.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.types import JSONB, UUID


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


class Organization(Base):
    """Multi-tenant organization."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    api_keys: Mapped[list["APIKey"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    members: Mapped[list["Member"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    investigations: Mapped[list["Investigation"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class APIKey(Base):
    """Tenant API keys for authentication."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="responder", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="api_keys")

    __table_args__ = (
        Index("ix_api_keys_org_active", "org_id", "is_active"),
    )


class Member(Base):
    """Organization members (users)."""

    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="viewer", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="members")

    __table_args__ = (
        Index("ix_members_org_email", "org_id", "email", unique=True),
    )


class Investigation(Base):
    """Investigation thread with RCA results."""

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    feedback_rating: Mapped[str | None] = mapped_column(String(16))
    feedback_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship(back_populates="investigations")
    events: Mapped[list["InvestigationEvent"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_investigations_org_created", "org_id", "created_at"),
        Index("ix_investigations_org_thread", "org_id", "thread_id", unique=True),
    )


class InvestigationEvent(Base):
    """Investigation step events (trace, approval, tool call)."""

    __tablename__ = "investigation_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    investigation: Mapped["Investigation"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_events_investigation_created", "investigation_id", "created_at"),
    )


class AuditLog(Base):
    """Immutable audit log with hash chain for tamper evidence."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor: Mapped[str | None] = mapped_column(String(256))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_org_created", "org_id", "created_at"),
    )


class AlertRule(Base):
    """Configurable alert rules."""

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    service: Mapped[str] = mapped_column(String(128), default="*", nullable=False)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notification_channels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AlertEvent(Base):
    """Fired alert instances."""

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="firing", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_alert_events_rule_status", "rule_id", "status"),
        Index("ix_alert_events_org_started", "org_id", "started_at"),
    )


class Runbook(Base):
    """A remediation runbook owned by one org.

    `runbook_id` is the id the API exposes and the built-ins reuse across orgs
    ("restart-service"), so the primary key is a surrogate and the identity is
    (org_id, runbook_id).
    """

    __tablename__ = "runbooks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    runbook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    steps: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_runbooks_org_runbook", "org_id", "runbook_id", unique=True),
    )


class RunbookExecution(Base):
    """Runbook execution history."""

    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    runbook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runbook_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(256))
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_runbook_exec_org_started", "org_id", "started_at"),
    )


class Incident(Base):
    """A declared incident.

    The nested documents — timeline, agent states, safety gates — are stored as
    JSON rather than child tables: they are appended to and read back whole, and
    never queried across incidents. The columns that *are* filtered on (org,
    status, severity, service, time) are real columns.
    """

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="triggered", nullable=False)
    service: Mapped[str] = mapped_column(String(256), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(256))
    resolved_by: Mapped[str | None] = mapped_column(String(256))
    assignee: Mapped[str | None] = mapped_column(String(256))
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    timeline: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    related_alerts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    impact: Mapped[str | None] = mapped_column(Text)
    customer_impact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    investigation: Mapped["IncidentInvestigation | None"] = relationship(
        back_populates="incident", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_incidents_org_triggered", "org_id", "triggered_at"),
        Index("ix_incidents_org_status", "org_id", "status"),
    )


class IncidentInvestigation(Base):
    """The multi-agent investigation attached to one incident."""

    __tablename__ = "incident_investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text)
    recommended_actions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    agents: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    safety_gates: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)

    incident: Mapped["Incident"] = relationship(back_populates="investigation")

    __table_args__ = (
        Index("ix_incident_investigations_org", "org_id"),
    )


class UsageRecord(Base):
    """Per-tenant usage metering for billing."""

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    investigations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_usage_org_period", "org_id", "period", unique=True),
    )


# --- billing ------------------------------------------------------------------
#
# One table per billing entity, all prefixed `billing_`. The free-form parts of
# each record (Stripe metadata, billing addresses, invoice line items) stay JSON;
# everything that is filtered on, or that is money, is a real column.


class BillingOrganization(Base):
    """An org's billing identity — mainly its Stripe customer."""

    __tablename__ = "billing_organizations"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    slug: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stripe_customer_id: Mapped[str] = mapped_column(
        String(128), default="", nullable=False, index=True
    )
    billing_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    tax_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    billing_address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingSubscription(Base):
    """The org's current subscription.

    One row per org — the store is keyed by org and an org has a single
    subscription — so `org_id` is unique rather than merely indexed.
    """

    __tablename__ = "billing_subscriptions"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(16), nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(128), default="", nullable=False, index=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    trial_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    cancel_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pause_collection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingInvoice(Base):
    """An invoice, mirrored from Stripe."""

    __tablename__ = "billing_invoices"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stripe_invoice_id: Mapped[str] = mapped_column(
        String(128), default="", nullable=False, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_paid_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="usd", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_pdf_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hosted_invoice_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    line_items: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_billing_invoices_org_created", "org_id", "created_at"),
    )


class BillingPaymentMethod(Base):
    """A saved card. Only the last four digits ever land here."""

    __tablename__ = "billing_payment_methods"

    payment_method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_payment_method_id: Mapped[str] = mapped_column(
        String(128), default="", nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), default="card", nullable=False)
    card_brand: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    card_last4: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    card_exp_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    card_exp_year: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    billing_details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BillingUsageRecord(Base):
    """One metered event. Rolled up into BillingUsageSummary for reads."""

    __tablename__ = "billing_usage_records"

    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_billing_usage_org_period", "org_id", "period_start"),
    )


class BillingUsageSummary(Base):
    """Per-month counters, the numbers the quota checks read."""

    __tablename__ = "billing_usage_summaries"

    summary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    ai_investigations_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    api_calls_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    postmortems_generated_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    auto_fix_prs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    similar_searches_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_users_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_bytes_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_billing_summary_org_month", "org_id", "year_month", unique=True),
    )


class BillingFeatureOverride(Base):
    """A feature unlocked for one org outside its plan — a negotiated deal."""

    __tablename__ = "billing_feature_overrides"

    override_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    limit_value: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_billing_override_org_key", "org_id", "feature_key", unique=True),
    )


class KnowledgeDocument(Base):
    """A knowledge base document owned by one org.

    `doc_id` is the id the API exposes, and the seeded built-ins reuse the same
    ids ("kb-001") in every org, so the primary key is a surrogate and the
    identity is (org_id, doc_id).

    There is no embedding column on purpose. `KnowledgeBase._generate_embedding`
    is a word-frequency placeholder derived entirely from `content` — storing it
    would persist a value that has to be invalidated on every edit and that a
    real embedding model would replace anyway. The search index is rebuilt from
    `content` on each query instead.
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    service: Mapped[str | None] = mapped_column(String(128))
    author: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # `metadata` is taken on a declarative class; the column keeps the name.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    # Stamped in Python, not by the server: the document list is ordered by
    # `updated_at`, and SQLite's CURRENT_TIMESTAMP has one-second resolution, so
    # a server default would give every seeded document the same instant and an
    # arbitrary order.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_knowledge_documents_org_doc", "org_id", "doc_id", unique=True),
        Index("ix_knowledge_documents_org_updated", "org_id", "updated_at"),
    )
