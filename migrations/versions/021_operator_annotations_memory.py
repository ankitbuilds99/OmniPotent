"""Persist operator corrections and confirmed incident memory."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import UUID

revision: str = "021_operator_annotations"
down_revision: Union[str, None] = "020_evidence_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operator_annotations",
        sa.Column("id", UUID(), nullable=False),
        sa.Column("org_id", UUID(), nullable=False),
        sa.Column("incident_id", UUID(), nullable=False),
        sa.Column("field", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64)),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
        sa.UniqueConstraint("org_id", "incident_id", "revision", name="uq_operator_annotation_revision"),
    )
    op.create_index("ix_operator_annotations_org_incident", "operator_annotations", ["org_id", "incident_id", "revision"])
    op.create_table(
        "confirmed_memories",
        sa.Column("id", UUID(), nullable=False),
        sa.Column("org_id", UUID(), nullable=False),
        sa.Column("incident_id", UUID(), nullable=False),
        sa.Column("field", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by", sa.String(length=256), nullable=False),
        sa.Column("confirmation", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "incident_id", "field", name="uq_confirmed_memory_field"),
    )
    op.create_index("ix_confirmed_memories_org_incident", "confirmed_memories", ["org_id", "incident_id"])


def downgrade() -> None:
    op.drop_index("ix_confirmed_memories_org_incident", table_name="confirmed_memories")
    op.drop_table("confirmed_memories")
    op.drop_index("ix_operator_annotations_org_incident", table_name="operator_annotations")
    op.drop_table("operator_annotations")
