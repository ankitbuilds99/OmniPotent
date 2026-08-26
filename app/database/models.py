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
    notification_channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
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
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
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
    # 64, matching `runbooks.trigger` above: the store copies the runbook's
    # trigger straight into the execution row, and `CreateRunbookRequest`
    # puts no limit on it. At 32 a runbook created with a 40-character
    # trigger stored fine and then failed to record any execution — a
    # StringDataRightTruncation on Postgres, invisible on SQLite, which
    # ignores VARCHAR lengths.
    trigger: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
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
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    related_alerts: Mapped[list[str]] = mapped_column(JSONB, default=list)
    impact: Mapped[str | None] = mapped_column(Text)
    customer_impact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The sender's own identity for the underlying alert — Alertmanager's
    # `fingerprint`, and whatever the other receivers can offer. Alertmanager
    # re-delivers the same firing group every `repeat_interval` (4h by default,
    # and every 30s while a group is still resolving), so without this one alert
    # became one incident per delivery and the resolved notification had nothing
    # to close.
    dedupe_key: Mapped[str | None] = mapped_column(String(256))

    investigation: Mapped["IncidentInvestigation | None"] = relationship(
        back_populates="incident", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_incidents_org_triggered", "org_id", "triggered_at"),
        Index("ix_incidents_org_status", "org_id", "status"),
        Index("ix_incidents_org_dedupe", "org_id", "dedupe_key"),
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
    recommended_actions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    agents: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    safety_gates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    incident: Mapped["Incident"] = relationship(back_populates="investigation")

    __table_args__ = (
        Index("ix_incident_investigations_org", "org_id"),
    )


class Postmortem(Base):
    """The postmortem document for one incident.

    The page that edits these had no persistence behind it at all: Save on a
    section and the status dropdown on an action item both wrote to React state
    and stopped there, so a reload discarded every edit and re-ran the model
    from scratch. A postmortem is the one artifact of an incident that outlives
    it — it gets reviewed, assigned and referenced weeks later — so losing the
    human's rewrite of the root cause is losing the only part of the document
    that was not machine-generated.

    `sections` and `action_items` are JSON for the same reason the incident
    timeline is: they are read and written whole, and never queried across
    postmortems. One row per incident.
    """

    __tablename__ = "postmortems"

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
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    authors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reviewers: Mapped[list[str]] = mapped_column(JSONB, default=list)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    action_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    doc_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_postmortems_org", "org_id"),
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
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
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

    There is no embedding column on purpose. Search is BM25 over the org's rows
    (`KnowledgeBase.search`), which needs term frequencies for the corpus as it
    stands at query time, not a per-row vector that would have to be invalidated
    on every edit. The index is rebuilt from `content` on each query instead.
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


class SLO(Base):
    """A Service Level Objective owned by one org.

    `slo_id` is the id the API exposes ("slo_<hex>"); it is random per creation
    and never reused across orgs, but the primary key stays a surrogate so the
    identity is (org_id, slo_id) like every other feature table here.
    """

    __tablename__ = "slos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    slo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    sli_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    window: Mapped[str] = mapped_column(String(32), nullable=False)
    team_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    owner: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    alert_burn_rate_threshold: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )
    alert_channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_slos_org_slo", "org_id", "slo_id", unique=True),
        Index("ix_slos_org_service", "org_id", "service"),
    )


class SLIMeasurement(Base):
    """One SLI sample against an SLO.

    Deliberately not a foreign key to `slos`: `runbook_executions` sets the
    precedent of referencing its parent by the API-visible string id, and the
    tracker prunes and deletes these explicitly so it does not rely on a cascade.
    Every query carries `org_id` as well, so a sample can only be read by the org
    that recorded it.
    """

    __tablename__ = "sli_measurements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    slo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sli_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    good_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_sli_measurements_org_slo_ts", "org_id", "slo_id", "timestamp"),
    )


class StatusComponent(Base):
    """A component shown on an org's public status page.

    `component_id` is the id the API and the public page expose. The five
    seeded built-ins use fixed ids ("api", "database") that repeat in every org,
    so the primary key is a surrogate and the identity is
    (org_id, component_id).

    `group` is a reserved word in SQL, so the column is `group_name`; the API
    field stays `group`.
    """

    __tablename__ = "status_components"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(128))
    display_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the current status began, which is not the same as when the row was
    # last touched: renaming a component must not shorten the outage it is in
    # the middle of. Uptime intervals are measured from here.
    status_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_status_components_org_component", "org_id", "component_id", unique=True),
        Index("ix_status_components_org_order", "org_id", "display_order", "name"),
    )


class StatusUptimeRecord(Base):
    """One interval a component spent operational, or not.

    Written when a component changes status: the row covers the time it sat in
    the *previous* status. `calculate_uptime` sums the durations over a window,
    which is why the interval is stored rather than the sample.

    Bounded by age, not by count. The in-memory version kept the last 10,000
    tuples per component; nothing reads further back than the 90-day figure on
    the status page, so the store prunes past that instead — a component that
    flaps 10,000 times in a day no longer pushes last week off the end.
    """

    __tablename__ = "status_uptime_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    was_operational: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        Index(
            "ix_status_uptime_org_component_at",
            "org_id",
            "component_id",
            "recorded_at",
        ),
    )


class StatusIncident(Base):
    """An incident published on an org's status page.

    Distinct from `incidents`, which is the internal incident record: this one
    is customer-facing, carries no responder or severity taxonomy beyond
    minor/major/critical, and its timeline is `status_incident_updates`.
    """

    __tablename__ = "status_incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    affected_components: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_status_incidents_org_incident", "org_id", "incident_id", unique=True),
        Index("ix_status_incidents_org_created", "org_id", "created_at"),
    )


class StatusIncidentUpdate(Base):
    """One entry on a status page incident's timeline.

    Referenced by the API-visible `incident_id` rather than by a foreign key,
    the same way `runbook_executions` points at its runbook. Every query carries
    `org_id`, so an update is only readable by the org that posted it.
    """

    __tablename__ = "status_incident_updates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    update_id: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(256))
    # The timeline is ordered by `created_at`, and two updates posted in the
    # same second are common when an incident is resolved from a script. This
    # breaks the tie so the order is total and stable.
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index(
            "ix_status_incident_updates_org_incident",
            "org_id",
            "incident_id",
            "created_at",
            "sequence",
        ),
    )


class StatusMaintenance(Base):
    """A scheduled maintenance window on an org's status page."""

    __tablename__ = "status_maintenances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    maintenance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    affected_components: Mapped[list[str]] = mapped_column(JSONB, default=list)
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_status_maintenances_org_maintenance",
            "org_id",
            "maintenance_id",
            unique=True,
        ),
        Index("ix_status_maintenances_org_start", "org_id", "scheduled_start"),
    )


class StatusSubscriber(Base):
    """Someone subscribed to an org's status page updates.

    This table holds customers' customers: email addresses and webhook URLs
    belonging to the people an org notifies. `target` is unique per org so a
    second subscribe with the same address returns the existing subscription
    instead of creating a duplicate that would be notified twice.
    """

    __tablename__ = "status_subscribers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscriber_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subscription_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    components: Mapped[list[str]] = mapped_column(JSONB, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(128))
    notify_on_incidents: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_on_maintenance: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_on_resolved: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_status_subscribers_org_subscriber",
            "org_id",
            "subscriber_id",
            unique=True,
        ),
        Index("ix_status_subscribers_org_target", "org_id", "target", unique=True),
    )


class GraphNode(Base):
    """One node of an org's infrastructure knowledge graph.

    `Neo4jKnowledgeGraph` falls back to a plain dict when no Neo4j server is
    configured — which is the default deployment — so every service, deployment,
    incident, change and alert the sync engine discovered disappeared on
    restart, and a blast radius computed by one worker disagreed with the next.
    These two tables are that fallback's storage.

    The shape is deliberately generic (`label` + `properties`) rather than one
    table per node type: the graph's whole point is that the traversals do not
    care what a node is, and Neo4j itself stores labelled property bags. `label`
    is a `NodeType` value ("Service", "Deployment", …) and `node_id` is the id
    the API exposes, so identity is (org_id, label, node_id) — the same
    `MERGE (s:Service {id: $id})` key the Cypher branch uses.
    """

    __tablename__ = "graph_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str] = mapped_column(String(256), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_graph_nodes_org_label_node", "org_id", "label", "node_id", unique=True),
        Index("ix_graph_nodes_org_label", "org_id", "label"),
    )


class GraphEdge(Base):
    """One relationship of an org's infrastructure knowledge graph.

    Endpoints are stored as the bare node ids the API accepts, because
    `create_relationship` matches on `(a {id: $source_id})` without a label —
    a relationship may join any two node types. The traversals resolve an id to
    its node by looking it up in `graph_nodes`.
    """

    __tablename__ = "graph_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    target_id: Mapped[str] = mapped_column(String(256), nullable=False)
    rel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_graph_edges_org_triple",
            "org_id",
            "source_id",
            "target_id",
            "rel_type",
            unique=True,
        ),
        Index("ix_graph_edges_org_target", "org_id", "target_id"),
    )
