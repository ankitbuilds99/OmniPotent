"""Make `investigations` the real investigation-history table

Revision ID: 003_investigations
Revises: 002_crm
Create Date: 2026-08-20

Moves app/dashboard/metrics.py off the module-level `_investigation_store` dict
and onto this table, and reshapes the table so it can actually hold what the
application produces.

Two changes, both forced by the same fact — `investigations` has never held a
row, because nothing in the codebase read or wrote it:

1. `org_id` was `UUID REFERENCES organizations(id)`. The org ids the app issues
   are strings (`org_abc123`) minted by app/tenancy/store.py, a separate
   aiosqlite store with its own `orgs` table. `organizations` has never held a
   row either, so the FK could not be satisfied by any real request and the
   column type could not hold a real org id. It is now `VARCHAR(128)`, indexed,
   no FK.
2. `query` was NOT NULL. The completion hook that records an investigation sees
   the outcome but not the original prompt, so it is now nullable.

Plus the columns the dashboard aggregates over: outcome, severity, service,
hypotheses_generated, tools_used, duration_seconds, time_saved_minutes, and
started_at (when the investigation began — created_at is when the row was
inserted, which is *after* completion and would understate every duration).

DROP and CREATE rather than ALTER. Both tables are provably empty in every
deployment: `investigations` had no writer, and `investigation_events` is
FK-child to it so it can have no orphan rows either. Recreating avoids
`batch_alter_table` gymnastics — SQLite cannot drop a constraint or retype a
column in place, so an ALTER path would rebuild these tables anyway, just less
legibly and with a different result on each dialect.

`ix_investigations_org_created` becomes `ix_investigations_org_started`: the
dashboard filters by when the investigation ran, not when the row was written.
The unique index on (org_id, thread_id) is kept and is now load-bearing — it is
what makes "one row per investigation" true, so a multi-turn conversation counts
once instead of once per turn.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003_investigations"
down_revision: Union[str, None] = "002_crm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Child first: investigation_events carries an FK to investigations.id.
    op.drop_table("investigation_events")
    op.drop_table("investigations")

    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        # String, not UUID, and no FK to organizations — see the module docstring.
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("report", JSONType, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("feedback_rating", sa.String(16), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        # Dashboard analytics.
        sa.Column(
            "outcome", sa.String(32), nullable=False, server_default="partial_diagnosis"
        ),
        sa.Column("severity", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("service", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "hypotheses_generated", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("tools_used", JSONType, nullable=False, server_default="[]"),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("time_saved_minutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investigations_org_id", "investigations", ["org_id"])
    op.create_index("ix_investigations_thread_id", "investigations", ["thread_id"])
    op.create_index(
        "ix_investigations_org_started", "investigations", ["org_id", "started_at"]
    )
    op.create_index(
        "ix_investigations_org_thread",
        "investigations",
        ["org_id", "thread_id"],
        unique=True,
    )

    op.create_table(
        "investigation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        # NOT NULL, unlike revision 001. The ORM declares this as
        # `Mapped[dict[str, Any]]` with `default=dict` — a non-Optional
        # annotation, so `create_all` emits NOT NULL and the two schema paths
        # disagreed. Recreating the table here is the natural place to settle it,
        # and the stricter side is the correct one: every writer supplies a dict.
        sa.Column("data", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"], ["investigations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_events_investigation_created",
        "investigation_events",
        ["investigation_id", "created_at"],
    )


def downgrade() -> None:
    """Restore the 001 shape.

    This DROPS the recorded investigation history rather than converting it: the
    001 shape cannot hold it. `org_id` there is a UUID with an FK into
    `organizations`, and the string org ids written by this revision have no
    corresponding row to point at, so every row would violate the constraint.
    Downgrading past this revision means giving up the dashboard's data.
    """
    op.drop_table("investigation_events")
    op.drop_table("investigations")

    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("report", JSONType, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("feedback_rating", sa.String(16), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investigations_thread_id", "investigations", ["thread_id"])
    op.create_index(
        "ix_investigations_org_created", "investigations", ["org_id", "created_at"]
    )
    op.create_index(
        "ix_investigations_org_thread",
        "investigations",
        ["org_id", "thread_id"],
        unique=True,
    )

    op.create_table(
        "investigation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("data", JSONType, nullable=True, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"], ["investigations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_events_investigation_created",
        "investigation_events",
        ["investigation_id", "created_at"],
    )
