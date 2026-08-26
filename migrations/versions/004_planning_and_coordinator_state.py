"""Persist planning task graphs and coordinator DAG executions

Revision ID: 004_planning_coord
Revises: 003_investigations
Create Date: 2026-08-20

Moves the last two request-path stores named in the single-replica pin out of
process memory:

  app/planning/api_router.py     _graphs            -> planning_graphs
  app/coordinator/api_router.py  _active_executions -> coordinator_executions

Postgres rather than Redis, deliberately. `app/redis_pool.py:get_redis()` falls
back to an in-process `MockRedis` dict whenever COPILOT_REDIS_URL is unset, and
that setting defaults to "" (the Helm chart requires an explicit --set). A
Redis-backed move would therefore leave both stores exactly as per-pod as they
are today on the default deployment, while every artifact that tracks the pin
said they had been externalised — and the pin would lift onto state that is
still not shared.

Both tables are keyed on the COMPOSITE primary key (id, org_id). The routes take
a caller-supplied graph/execution id, so without org in the key one tenant could
name a graph the same thing as another's and read, execute or delete it. Making
org part of the identity means a lookup that omits it cannot compile, rather than
relying on every future call site remembering to filter.

`state` / `execution` hold whole serialized objects (`TaskGraph.serialize()`,
`DAGExecution.serialize()`) rather than normalized task rows. Every route loads
one whole graph or execution by id and operates on the reconstructed object;
nothing queries across tasks. The columns lifted out alongside them (task_count,
state, current_level, dag_id) are exactly the fields the two list endpoints read,
so listing does not deserialize every payload.

No data migration: the state being moved was destroyed on every restart.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004_planning_coord"
down_revision: Union[str, None] = "003_investigations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "planning_graphs",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("state", JSONType, nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
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
        # Composite: a graph id is only unique within an organization.
        sa.PrimaryKeyConstraint("id", "org_id"),
    )
    op.create_index(
        "ix_planning_graphs_org_updated", "planning_graphs", ["org_id", "updated_at"]
    )

    op.create_table(
        "coordinator_executions",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("dag_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_level", sa.Integer(), nullable=False),
        sa.Column("execution", JSONType, nullable=False),
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
        "ix_coordinator_exec_org_started",
        "coordinator_executions",
        ["org_id", "started_at"],
    )


def downgrade() -> None:
    """Drop both tables.

    Reverting means going back to per-pod dicts, which is only safe at
    replicaCount 1 — the same condition that still holds today. Nothing else
    references these tables, so there is nothing to cascade.
    """
    op.drop_index("ix_coordinator_exec_org_started", table_name="coordinator_executions")
    op.drop_table("coordinator_executions")
    op.drop_index("ix_planning_graphs_org_updated", table_name="planning_graphs")
    op.drop_table("planning_graphs")
