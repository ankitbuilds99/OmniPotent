"""SQLAlchemy ORM models for production persistence.

All models inherit from Base and use UUID primary keys for multi-tenant safety.
Timestamps are auto-managed. Indexes are tuned for common query patterns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres gets JSONB; every other backend gets its own JSON type. The rendered
# PostgreSQL DDL is byte-identical to a bare JSONB column, so this is not a schema
# change — but a bare `JSONB` cannot be COMPILED at all outside PostgreSQL:
#
#   CompileError (in table 'organizations', column 'settings'): Compiler
#   <SQLiteTypeCompiler ...> can't render element of type JSONB
#
# SQLite is the default backend (app/database/engine.py falls back to it whenever
# COPILOT_DATABASE_URL is unset), so `Base.metadata.create_all` raised on the
# default configuration and no table declared in this file could ever be created.
# `Uuid` below is the same story: it renders native `UUID` on PostgreSQL and
# CHAR(32) elsewhere, where `postgresql.UUID` renders nowhere else at all.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSONType,
    }


class Organization(Base):
    """Multi-tenant organization."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # Nullable: a record can be created from a completion hook that sees the
    # outcome but not the original prompt.
    query: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
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
    tools_used: Mapped[list[str]] = mapped_column(JSONType, default=list)
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
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
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    actor: Mapped[str | None] = mapped_column(String(256))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    service: Mapped[str] = mapped_column(String(128), default="*", nullable=False)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notification_channels: Mapped[dict[str, Any]] = mapped_column(JSONType, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
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
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
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


class RunbookExecution(Base):
    """Runbook execution history."""

    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    runbook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runbook_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(256))
    variables: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_runbook_exec_org_started", "org_id", "started_at"),
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


class UsageRecord(Base):
    """Per-tenant usage metering for billing."""

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
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
