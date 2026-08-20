"""Persist runbook definitions and executions

Revision ID: 005_runbooks
Revises: 004_planning_coord
Create Date: 2026-08-20

Moves the runbook API's two request-path stores out of process memory:

  app/runbooks/router.py  _runbooks   -> runbook_definitions
  app/runbooks/router.py  _executions -> runbook_runs

`_runbooks` was seeded at import from DEFAULT_RUNBOOKS and then written to by
`POST /runbooks`. A runbook an operator authored during an incident was visible
only to the replica that served the request and was gone at the next restart —
the worst possible lifetime for a document whose entire purpose is to be there
the next time the same thing breaks.

The built-ins are seeded PER ORG rather than shared. They are starting points an
operator is expected to edit or disable, and a single global copy would make one
tenant's edit rewrite every other tenant's. `is_builtin` records which rows came
from that seed so the two can be told apart later.

Both tables are keyed on the composite primary key (id, org_id): execution ids
arrive in the URL, so without org in the key one tenant could approve, reject or
delete another's run. Making org part of the identity means an unscoped lookup
cannot compile.

`service` and `trigger` are lifted out of the definition payload because the list
endpoint filters on them; `status`, `runbook_id` and the timestamps are lifted
out of the execution payload for the same reason.

No data migration: the state being moved was destroyed on every restart.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005_runbooks"
down_revision: Union[str, None] = "004_planning_coord"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "runbook_definitions",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("service", sa.String(255), nullable=False, server_default="*"),
        sa.Column("trigger", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("definition", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Composite: a runbook id is only unique within an organization.
        sa.PrimaryKeyConstraint("id", "org_id"),
    )
    op.create_index(
        "ix_runbook_defs_org_service", "runbook_definitions", ["org_id", "service"]
    )

    op.create_table(
        "runbook_runs",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("runbook_id", sa.String(128), nullable=False),
        sa.Column("runbook_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "triggered_by", sa.String(128), nullable=False, server_default="manual"
        ),
        sa.Column("execution", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", "org_id"),
    )
    op.create_index(
        "ix_runbook_runs_org_started", "runbook_runs", ["org_id", "started_at"]
    )
    op.create_index(
        "ix_runbook_runs_org_runbook", "runbook_runs", ["org_id", "runbook_id"]
    )


def downgrade() -> None:
    """Drop both tables.

    Reverting means going back to per-pod dicts, which is only safe at
    replicaCount 1 — the same condition that still holds today. Nothing else
    references these tables, so there is nothing to cascade.
    """
    op.drop_index("ix_runbook_runs_org_runbook", table_name="runbook_runs")
    op.drop_index("ix_runbook_runs_org_started", table_name="runbook_runs")
    op.drop_table("runbook_runs")
    op.drop_index("ix_runbook_defs_org_service", table_name="runbook_definitions")
    op.drop_table("runbook_definitions")
