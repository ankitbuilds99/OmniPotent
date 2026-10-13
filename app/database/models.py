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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres gets JSONB and a native uuid column; every other backend gets its own
# JSON type and CHAR(32). The rendered PostgreSQL DDL is byte-identical to bare
# `postgresql.JSONB`/`postgresql.UUID` columns, so this is not a schema change —
# but those bare types cannot be COMPILED at all outside PostgreSQL:
#
#   CompileError (in table 'organizations', column 'settings'): Compiler
#   <SQLiteTypeCompiler ...> can't render element of type JSONB
#
# SQLite is the default backend (app/database/engine.py falls back to it whenever
# COPILOT_DATABASE_URL is unset), so `Base.metadata.create_all` raised on the
# default configuration and no table declared in this file could ever be created.
from app.database.types import JSONB, UUID

# `JSONType` is the name the same portable-JSON column is imported under by
# app/oncall/models.py. It is an alias, not a second type: those tables share
# Base.metadata with this file, so one postgresql-only column anywhere in the
# metadata makes every table uncreatable on SQLite.
JSONType = JSONB


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
    # No `investigations` relationship: Investigation.org_id is the live string org
    # id from app/tenancy/store.py, not a foreign key into this table. See the
    # comment on Investigation for why.


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
    """One investigation thread, with its RCA result and dashboard analytics.

    This is the single record of "an investigation happened". It was previously
    two things that never met: this table (declared in migration 001, never read
    or written by anything) and `app/dashboard/metrics.InvestigationRecord`, a
    dataclass in a module-level dict that only tests ever appended to. The
    dashboard now reads this table, and the API records into it.

    `org_id` is a plain string, deliberately NOT a foreign key into
    `organizations`. The org ids the application actually issues are strings
    (`org_abc123`) minted by `app/tenancy/store.py`, which is a separate
    aiosqlite store with its own `orgs` table; the `organizations` table here has
    never held a row, so a UUID FK to it could not be satisfied by any real
    request. Making the type match reality is what let this table be written at
    all. Consolidating the two tenancy stores is worth doing, but it is a change
    to the auth path and does not belong in a persistence move.

    One row per thread, not per turn. `ix_investigations_org_thread` enforces it.
    A multi-turn conversation is one investigation; counting each turn as its own
    investigation inflated every total the dashboard reported.
    """

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # Nullable: a record can be created from a completion hook that sees the
    # outcome but not the original prompt.
    query: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    feedback_rating: Mapped[str | None] = mapped_column(String(16))
    feedback_comment: Mapped[str | None] = mapped_column(Text)

    # Dashboard analytics (app/dashboard/metrics.py aggregates over these).
    outcome: Mapped[str] = mapped_column(
        String(32), default="partial_diagnosis", nullable=False
    )
    severity: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    service: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    hypotheses_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tools_used: Mapped[list[str]] = mapped_column(JSONB, default=list)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Only ever set from a measured before/after, never from an assumed default —
    # ROI in dollars is derived from this column and an invented number here
    # becomes an invented dollar figure on the dashboard.
    time_saved_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # When the investigation began, which is what MTTR is measured from. Distinct
    # from created_at (when the row was inserted): a completion hook inserts the
    # row after the work is done, so created_at would understate every duration.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["InvestigationEvent"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_investigations_org_started", "org_id", "started_at"),
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


class OperatorAnnotationDB(Base):
    """Append-only operator judgement attached to an incident."""

    __tablename__ = "operator_annotations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        UniqueConstraint("org_id", "incident_id", "revision", name="uq_operator_annotation_revision"),
        Index("ix_operator_annotations_org_incident", "org_id", "incident_id", "revision"),
    )


class ConfirmedMemoryDB(Base):
    """A confirmed memory claim linked to the exact authored annotation digest."""

    __tablename__ = "confirmed_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(256), nullable=False)
    confirmation: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "incident_id", "field", name="uq_confirmed_memory_field"),
        Index("ix_confirmed_memories_org_incident", "org_id", "incident_id"),
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


class CRMCustomer(Base):
    """A signed-up customer in the trial/conversion funnel.

    Deliberately NOT the same thing as :class:`Organization`. An Organization is
    the authentication and multi-tenancy boundary (API keys, members, RLS); a
    CRMCustomer is the commercial record behind the `/customers/*` routes —
    who signed up, what they are being billed, how engaged they are. The two are
    not linked today because nothing in the signup path creates both.

    Backs `app/customers/manager.py`, which until now kept all of this in a
    module-level dict: a customer created on one pod did not exist on another,
    and every restart destroyed the usage the invoice is computed from.
    """

    __tablename__ = "crm_customers"

    # `cust_<16 hex>` — an opaque public identifier, not a UUID, because it is
    # already handed out in API responses in that form.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Unique: `create_customer` returns the existing record for a repeat email
    # rather than creating a second one. That used to be enforced by an in-memory
    # index; the constraint is what actually enforces it across processes.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    company: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="trial", nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="free", nullable=False)

    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    team_size: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    use_case: Mapped[str] = mapped_column(Text, default="", nullable=False)

    mrr_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_value_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    total_investigations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trial_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    health_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    nps_score: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, default="", nullable=False)

    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # Named `extra`, not `metadata`: `metadata` is reserved on a declarative class
    # (it is the MetaData collection). The dataclass field stays `metadata`.
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The dashboard's "recent customers" list is ORDER BY created_at DESC.
        Index("ix_crm_customers_created", "created_at"),
    )


class CRMTrial(Base):
    """A time- and quota-limited trial attached to a :class:`CRMCustomer`."""

    __tablename__ = "crm_trials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # trial_<16 hex>
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("crm_customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigations_allowed: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    investigations_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_allowed: Mapped[int] = mapped_column(Integer, default=100000, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    __table_args__ = (
        # "which trials are still running", the only query that scans this table.
        Index("ix_crm_trials_status_expires", "status", "expires_at"),
    )


class CRMUsageEvent(Base):
    """One metered event for a :class:`CRMCustomer` — the billing audit trail.

    Distinct from :class:`UsageRecord`, which is a per-organization monthly
    AGGREGATE (org_id, period, counters). This table is the per-event log the
    aggregate should be reconcilable against, keyed by customer rather than org.
    """

    __tablename__ = "crm_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("crm_customers.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    investigation_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    __table_args__ = (
        Index("ix_crm_usage_customer_ts", "customer_id", "timestamp"),
    )


class PlanningGraph(Base):
    """A task graph built through /api/planning, with its full serialized state.

    Graphs are user-authored artifacts, not cache: a caller creates one, adds
    tasks and dependencies to it over several requests, checkpoints it, and
    executes it. They previously lived in `app/planning/api_router.py:_graphs`, a
    module-level dict — so an operator who added ten tasks and then hit a pod
    that had restarted (or simply a different replica) got a 404 on the graph
    they had just built.

    `state` holds `TaskGraph.serialize()` rather than a normalized task table.
    Nothing queries inside a graph — every route loads one whole graph by id and
    operates on the object — so a task table would buy joins nobody performs and
    cost a schema migration every time TaskNode gains a field.

    `org_id` is a plain string, matching `Investigation`: real org ids come from
    app/tenancy/store.py, not from the `organizations` table here.
    """

    __tablename__ = "planning_graphs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    task_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_planning_graphs_org_updated", "org_id", "updated_at"),
    )


class CoordinatorExecution(Base):
    """A DAG execution started through /api/coordinator.

    Written when the run *starts*, not when it finishes. The dict this replaces
    was assigned only after `executor.execute()` returned, so
    `GET /coordinator/executions/{id}` answered 404 for the entire duration of
    the run — precisely the window in which a caller polls it — and then answered
    correctly once the answer no longer mattered.

    `state` is the run's lifecycle (pending/running/completed/failed), duplicated
    out of the serialized payload so that listing executions does not mean
    deserializing every one of them.
    """

    __tablename__ = "coordinator_executions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dag_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    current_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    execution: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_coordinator_exec_org_started", "org_id", "started_at"),
    )


class WaitlistSignup(Base):
    """One early-access signup.

    Deliberately NOT tenant-scoped, unlike every other table added in this
    effort: a waitlist signup happens before the person has an organization, so
    there is no org to scope it to. The waitlist is a property of the product.

    Replaces `app/waitlist/manager.py:_waitlist_store` / `_email_index` /
    `_position_counter`. These were leads — real email addresses submitted by
    real people — held in a dict that was emptied on every restart, deploy and
    scale-down, with no export having ever run. `_position_counter` was worse
    than merely volatile: it reset to 0 on restart, so the signups after a
    deploy were handed positions that earlier signups already held.

    `position` is unique so that a race between two concurrent signups fails
    loudly and retries, rather than silently issuing the same position twice.
    `email` is unique because it is the identity a caller looks an entry up by.
    """

    __tablename__ = "waitlist_signups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="landing_page")

    company: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    team_size: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    use_case: Mapped[str] = mapped_column(Text, default="", nullable=False)

    referral_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    referred_by: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    referral_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    utm_source: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    utm_medium: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    utm_campaign: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # Hashed, never the raw address — this is duplicate-detection material, not
    # something anyone should be able to read back out of the table.
    ip_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_waitlist_tier_source", "tier", "source"),
        Index("ix_waitlist_created", "created_at"),
    )


class RunbookDefinition(Base):
    """A runbook definition managed through /api/runbooks.

    Runbooks are operational procedures an operator authors once and reaches for
    during an incident. They previously lived in `app/runbooks/router.py:_runbooks`,
    a module-level dict seeded at import from DEFAULT_RUNBOOKS — so a runbook
    written on Tuesday was gone after the next deploy, and in the meantime only
    the replica that served the POST could see it.

    `definition` holds `Runbook.to_dict()`. The built-in DEFAULT_RUNBOOKS are
    seeded per org on first access rather than shared, so an operator can edit or
    disable one without changing what every other tenant sees; `is_builtin`
    records which rows came from that seed.

    `service` and `trigger` are lifted out of the payload because listing filters
    on them, and a filter that deserializes every runbook to read one string does
    not stay cheap.
    """

    __tablename__ = "runbook_definitions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(255), nullable=False, default="*")
    trigger: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_runbook_defs_org_service", "org_id", "service"),
    )


class RunbookRun(Base):
    """One execution of a runbook, recorded from the moment it is triggered.

    Written before the steps run, like `CoordinatorExecution` and for the same
    reason: a caller polls the execution while it is in flight, and a row that
    only appears on completion answers 404 for exactly that window.

    The dict this replaces was worse than per-pod. `execute_runbook` accepted a
    `BackgroundTasks` argument, never scheduled anything on it, and stored a
    record with status "running" that nothing would ever advance — so every
    execution this API reported was a status field that no runbook step backed.
    """

    __tablename__ = "runbook_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    runbook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runbook_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    triggered_by: Mapped[str] = mapped_column(String(128), nullable=False, default="manual")
    execution: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_runbook_runs_org_started", "org_id", "started_at"),
        Index("ix_runbook_runs_org_runbook", "org_id", "runbook_id"),
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


# `teams`, `team_members` and `team_invitations` are NOT declared here.
#
# Both branches externalised the same in-memory team store, in different
# modules: origin/main added TeamRecord/TeamMemberRecord/TeamInvitationRecord to
# this file, and this branch added TeamDB/TeamMemberDB/TeamInvitationDB to
# app/database/team_models.py (revision 013_teams). They are the same three
# tables, and two classes cannot claim one `__tablename__` in one MetaData —
# with both present, importing this module raises InvalidRequestError and every
# table in the app becomes uncreatable.
#
# app/database/team_models.py wins because its rows carry `org_id` and its slug
# is unique per org rather than globally: origin/main's `slug` unique constraint
# let one tenant deny a team name to every other tenant and told them so with a
# 409. Everything origin/main's version enforced is enforced there too
# (unique membership, invitation expiry checked on accept).
#
# Consumers of the dropped names must move to the team_models ones:
# tests/test_teams_persistence.py imports TeamRecord/TeamMemberRecord/
# TeamInvitationRecord, and origin/main's app/teams/store.py did as well.


# `oauth_accounts` is NOT declared here either, for the same reason: this branch
# declares it as OAuthAccountDB in app/database/auth_models.py (revision
# 014_auth), which is the class app/auth/oauth.py's account lookup, linking and
# last-login update already go through, and which sits next to the
# `revoked_sessions` table only this branch has. origin/main's
# OAuthAccountRecord keyed the row on the natural key "{provider}:{provider_id}"
# instead of a surrogate uuid; OAuthAccountDB gets the same guarantee from its
# unique constraint on (provider, provider_id).
#
# origin/main's app/auth/oauth.py imports OAuthAccountRecord from this module
# and has to be pointed at OAuthAccountDB.
#
# OAuthStateRecord below is kept: nothing on this branch persists the login CSRF
# state, which is still a process-local dict here.


class OAuthStateRecord(Base):
    """A pending OAuth CSRF state token, consumed exactly once.

    Replaces `app.auth.oauth._OAUTH_STATES`. Unlike the other tables in this
    file this one is not moved for durability -- it is moved because the flow is
    inherently cross-process: the state is issued on the /auth/{provider}/login
    request and presented back on /auth/{provider}/callback, an entirely
    separate request that arrives from the provider's redirect. With the states
    in a per-pod dict, any deployment with more than one replica fails every
    login whose callback happens to land on a different pod than the redirect,
    with "Invalid or expired state parameter" and nothing in the logs to
    explain it.

    Consume-once is a real security property here (it is what stops a captured
    callback URL being replayed), so it is enforced with a DELETE ... rowcount
    rather than a SELECT-then-DELETE, which two concurrent replays could both
    pass.
    """

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Sweeping expired states; the only non-key access path.
        Index("ix_oauth_states_created", "created_at"),
    )


class AuditEventRecord(Base):
    """One hash-chained audit event.

    Replaces `app.audit_legacy._BUFFER`, a `deque(maxlen=2000)` that backed both
    GET /audit and GET /audit/verify. The buffer failed as a tamper-evidence
    control in two ways, and only one of them needed a second replica:

    1.  It broke at 2001 events on a single pod. `verify_chain` recomputes from
        a genesis of `prev_hash == ""`, but the deque evicts its oldest entry
        once full, so the oldest surviving entry points at a hash that is no
        longer there. From that event on the endpoint reported
        `{"valid": false, "broken_at": 0}` permanently -- indistinguishable
        from somebody having actually edited the trail.
    2.  Below 2000 events it reported `valid: true` over whatever fraction of
        events happened to land on the pod serving the request, since every
        replica kept its own buffer and its own running hash.

    So the control was wrong in both directions: falsely reassuring while
    small, falsely alarming once full, and never covering more than one pod.

    The chain is per-org and its position is explicit. `seq` is a per-org
    counter starting at 0, and `(org_id, seq)` is unique, so two concurrent
    appends cannot occupy the same position -- the loser gets an IntegrityError
    and retries against the new head. That is the same optimistic pattern the
    waitlist slice used for `MAX(position)+1`, and it is what lets a serial
    hash chain be appended to from several processes at once.

    Rows are never evicted, which is what makes verification meaningful: a gap
    in `seq` or a hash that does not fold into its successor is now evidence,
    not an artifact of the buffer having wrapped.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Chains are scoped per org: an auditor asks about one tenant, and scoping
    # also keeps the append contention (see the retry above) per-tenant rather
    # than global. '-' in single-tenant deployments, matching tenant_context.
    org_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # `ts` is the string the entry was hashed with, kept verbatim rather than
    # parsed into a DateTime: the hash is computed over the serialized entry, so
    # re-formatting this value on the way in or out would break verification of
    # every row after it.
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    actor: Mapped[str | None] = mapped_column(String(255))
    # The caller's **fields. Kept apart from the fixed columns so the entry can
    # be reassembled exactly as it was hashed.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        # The invariant the deque could not hold: one event per chain position.
        UniqueConstraint("org_id", "seq", name="uq_audit_events_org_seq"),
        # Verification and the read API both walk a chain in order.
        Index("ix_audit_events_org_seq", "org_id", "seq"),
        # GET /audit?event_prefix=... filters on this.
        Index("ix_audit_events_event", "event"),
    )


class EvidenceObservation(Base):
    """Immutable structured evidence and provenance, scoped to one tenant."""

    __tablename__ = "evidence_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(36), nullable=False)
    incident_id: Mapped[str] = mapped_column(String(256), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "evidence_id", name="uq_evidence_observations_org_id"),
        Index("ix_evidence_observations_org_incident", "org_id", "incident_id"),
        Index("ix_evidence_observations_org_hash", "org_id", "artifact_hash"),
    )
