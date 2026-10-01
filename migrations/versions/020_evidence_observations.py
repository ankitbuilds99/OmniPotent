"""Persist V2 structured evidence with tenant and incident scoping.

Revision ID: 020_evidence_observations
Revises: 019_postmortems
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

revision: str = "020_evidence_observations"
down_revision: Union[str, None] = "019_postmortems"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=256), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=256), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "evidence_id", name="uq_evidence_observations_org_id"),
    )
    op.create_index(
        "ix_evidence_observations_org_incident",
        "evidence_observations",
        ["org_id", "incident_id"],
    )
    op.create_index(
        "ix_evidence_observations_org_hash",
        "evidence_observations",
        ["org_id", "artifact_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_observations_org_hash", table_name="evidence_observations")
    op.drop_index("ix_evidence_observations_org_incident", table_name="evidence_observations")
    op.drop_table("evidence_observations")
