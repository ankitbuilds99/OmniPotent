"""Initial schema - all production tables

Revision ID: 001_initial
Revises:
Create Date: 2026-07-27

Creates all core tables for production:
- organizations, api_keys, members (multi-tenant)
- investigations, investigation_events (RCA tracking)
- audit_logs (compliance)
- alert_rules, alert_events (alerting)
- runbook_executions (automation)
- usage_records (billing)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else. The emitted PostgreSQL DDL is
# unchanged (so a database that already ran this revision is unaffected), but a
# bare postgresql.JSONB() cannot be COMPILED outside PostgreSQL — and alembic.ini
# defaults sqlalchemy.url to SQLite, so `alembic upgrade head` failed at this
# revision on the default configuration with:
#   CompileError (in table 'organizations', column 'settings'): ...
#   can't render element of type JSONB
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("settings", JSONType, nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("role", sa.String(32), nullable=False, server_default="responder"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_org_active", "api_keys", ["org_id", "is_active"])

    op.create_table(
        "members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_members_org_email", "members", ["org_id", "email"], unique=True)

    op.create_table(
        "investigations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("report", JSONType, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("feedback_rating", sa.String(16), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investigations_thread_id", "investigations", ["thread_id"])
    op.create_index("ix_investigations_org_created", "investigations", ["org_id", "created_at"])
    op.create_index("ix_investigations_org_thread", "investigations", ["org_id", "thread_id"], unique=True)

    op.create_table(
        "investigation_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("investigation_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("data", JSONType, nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_investigation_created", "investigation_events", ["investigation_id", "created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("actor", sa.String(256), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("data", JSONType, nullable=True, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_entry_hash", "audit_logs", ["entry_hash"])
    op.create_index("ix_audit_org_created", "audit_logs", ["org_id", "created_at"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("service", sa.String(128), nullable=False, server_default="*"),
        sa.Column("metric", sa.String(128), nullable=False),
        sa.Column("condition", sa.String(32), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="high"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notification_channels", JSONType, nullable=True, server_default="[]"),
        sa.Column("labels", JSONType, nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_rules_org_id", "alert_rules", ["org_id"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="firing"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_org_id", "alert_events", ["org_id"])
    op.create_index("ix_alert_events_rule_status", "alert_events", ["rule_id", "status"])
    op.create_index("ix_alert_events_org_started", "alert_events", ["org_id", "started_at"])

    op.create_table(
        "runbook_executions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("runbook_id", sa.String(128), nullable=False),
        sa.Column("runbook_name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("trigger", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("triggered_by", sa.String(256), nullable=True),
        sa.Column("variables", JSONType, nullable=True, server_default="{}"),
        sa.Column("results", JSONType, nullable=True, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runbook_executions_org_id", "runbook_executions", ["org_id"])
    op.create_index("ix_runbook_exec_org_started", "runbook_executions", ["org_id", "started_at"])

    op.create_table(
        "usage_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("investigations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_records_org_id", "usage_records", ["org_id"])
    op.create_index("ix_usage_org_period", "usage_records", ["org_id", "period"], unique=True)


def downgrade() -> None:
    op.drop_table("usage_records")
    op.drop_table("runbook_executions")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
    op.drop_table("audit_logs")
    op.drop_table("investigation_events")
    op.drop_table("investigations")
    op.drop_table("members")
    op.drop_table("api_keys")
    op.drop_table("organizations")
