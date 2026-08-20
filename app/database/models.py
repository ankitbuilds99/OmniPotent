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
    UniqueConstraint,
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


class TeamRecord(Base):
    """A team/workspace, as created through /teams.

    Replaces `app/teams/store.py:TeamStore._teams`. That store's docstring said
    "swap for database in production" and never was, while its router was wired
    into the API — so a team created on one pod did not exist on any other, and
    every team, membership and pending invitation was destroyed on each restart.

    `slug` is unique here, which the dict version could not enforce: it
    de-duplicated auto-generated slugs by scanning, but a caller-supplied slug
    was accepted verbatim, so two teams could share one. `get_team_by_slug` then
    returned whichever the scan reached first and the other team was
    unreachable by its own URL.

    Named `TeamRecord` rather than `Team` because `app/teams/models.py` already
    owns `Team` — the Pydantic shape the API speaks in. This is the row behind it.
    """

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TeamMemberRecord(Base):
    """One user's membership in one team.

    Replaces both `TeamStore._members` and `TeamStore._user_teams`. The second
    was a parallel index from user to team ids, maintained by hand on every add,
    remove and delete — the kind of bookkeeping that drifts. Here "which teams
    is this user in" is a query against this table, so it cannot disagree with
    the memberships themselves.

    `(team_id, user_id)` is unique: "already a member" was a read-then-write
    check, which two concurrent invitations both passed.
    """

    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_team_members_team_user", "team_id", "user_id", unique=True),
        # "Which teams am I in" — the read that replaces the _user_teams index.
        Index("ix_team_members_user", "user_id"),
        Index("ix_team_members_team_email", "team_id", "email"),
    )


class TeamInvitationRecord(Base):
    """An outstanding or resolved invitation to join a team.

    Replaces `TeamStore._invitations`. Losing these on restart was the worst of
    the three: an invitation is a promise made to someone outside the system,
    who has already been given a link that stops resolving.

    Expiry is evaluated on read and written back, as it was in memory — nothing
    sweeps this table on a timer, so a row only becomes EXPIRED once somebody
    looks at it. Accepting still re-checks `expires_at` rather than trusting the
    status, so an unobserved expiry is not an accepted invitation.
    """

    __tablename__ = "team_invitations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    invited_by: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_team_invitations_team_status", "team_id", "status"),
        # The invitee's own view: "what am I invited to", by address, across teams.
        Index("ix_team_invitations_email_status", "email", "status"),
    )


class OAuthAccountRecord(Base):
    """A provider identity (Google/GitHub) linked to a local user id.

    Replaces `app.auth.oauth._OAUTH_ACCOUNTS`. This table is what makes a
    returning user the *same* user: the callback in app/auth/endpoints.py looks
    the account up and, on a miss, mints a fresh `user_id` from
    `secrets.token_hex`. While the lookup was a per-process dict, every restart
    was a miss, so every returning person silently became a new user id with the
    same email address -- and everything keyed by user id (team_members.user_id
    above, investigations, roles) was left pointing at an identity nobody could
    sign in as again. Two replicas produced the same split without a restart.

    `id` is the natural key `"{provider}:{provider_id}"` the dict was keyed by,
    kept rather than replaced by a surrogate so the primary key itself enforces
    "one local user per provider identity" -- the property the callback relies
    on when it decides whether this is a returning user.
    """

    __tablename__ = "oauth_accounts"

    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # "Which identities does this user have" -- the read that replaces the
        # _USER_ACCOUNTS index, which is why that dict gets no table of its own.
        Index("ix_oauth_accounts_user", "user_id"),
        # Account-matching by address on first sight of a new provider. Stored
        # lower-cased, so this index is usable rather than decorative.
        Index("ix_oauth_accounts_email", "email"),
    )


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
