"""CRM customers, trials and usage events

Revision ID: 002_crm
Revises: 001_initial
Create Date: 2026-08-20

Moves app/customers/manager.py off four module-level Python dicts
(_customer_store / _email_index / _trial_store / _usage_store) and into the
database:
- crm_customers    (the signup + billing record; _email_index becomes a UNIQUE)
- crm_trials       (trial window and quotas)
- crm_usage_events (the per-event metering log invoices are computed from)

No data migration: the state this replaces lived in process memory and was
already destroyed by every restart, deploy and scale-down, so there is nothing
on disk to carry forward.

`crm_` prefix, and NOT reusing `usage_records`: that table is a per-organization
MONTHLY AGGREGATE (org_id, period, counters). These are per-customer, per-event
rows. Different grain, different key — merging them would silently change what
`usage_records.investigations` means.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_crm"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
# A bare postgresql.JSONB() cannot be compiled at all on SQLite, and alembic.ini
# defaults sqlalchemy.url to SQLite.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "crm_customers",
        # `cust_<16 hex>`, not a UUID: this identifier is already public in API
        # responses in that form.
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("company", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column("team_size", sa.String(32), nullable=False),
        sa.Column("industry", sa.String(128), nullable=False),
        sa.Column("use_case", sa.Text(), nullable=False),
        sa.Column("mrr_cents", sa.Integer(), nullable=False),
        sa.Column("lifetime_value_cents", sa.Integer(), nullable=False),
        sa.Column("total_investigations", sa.Integer(), nullable=False),
        sa.Column("total_tokens_used", sa.Integer(), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_id", sa.String(64), nullable=False),
        sa.Column("stripe_customer_id", sa.String(128), nullable=False),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False),
        sa.Column("onboarding_step", sa.Integer(), nullable=False),
        sa.Column("health_score", sa.Float(), nullable=False),
        sa.Column("nps_score", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column("extra", JSONType, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # UNIQUE, not just an index: this is what makes "one customer per email" hold
    # across replicas. It used to be a dict lookup, which held only per-process.
    op.create_index("ix_crm_customers_email", "crm_customers", ["email"], unique=True)
    op.create_index("ix_crm_customers_created", "crm_customers", ["created_at"])

    op.create_table(
        "crm_trials",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("investigations_allowed", sa.Integer(), nullable=False),
        sa.Column("investigations_used", sa.Integer(), nullable=False),
        sa.Column("tokens_allowed", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["crm_customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crm_trials_customer_id", "crm_trials", ["customer_id"])
    op.create_index("ix_crm_trials_status_expires", "crm_trials", ["status", "expires_at"])

    op.create_table(
        "crm_usage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("investigation_id", sa.String(128), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("extra", JSONType, nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["crm_customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crm_usage_customer_ts", "crm_usage_events", ["customer_id", "timestamp"])


def downgrade() -> None:
    op.drop_table("crm_usage_events")
    op.drop_table("crm_trials")
    op.drop_table("crm_customers")
