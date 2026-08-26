"""postmortems — persist the document, not just the generated draft

The Postmortem page let an operator rewrite any section and set a status on any
action item. Both handlers called `setPostmortem(...)` and nothing else: there
was no endpoint to call, because there was no table to write to. Every edit
lived until the next reload, at which point the page re-ran the model and
produced a fresh draft that had never heard of it.

One row per incident (`incident_id` unique) holding the whole document.
`sections` and `action_items` are JSON because they are read and written whole
and never queried across postmortems — the same reasoning as the incident
timeline. `doc_metrics` rather than `metrics`: `metadata` and `metrics` are both
awkward names on a SQLAlchemy model, and the column carries the generator's
duration figures for the document, not table statistics.

Revision ID: 019_postmortems
Revises: 018_digest
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# JSONB on Postgres, JSON on SQLite — see revision 012.
from app.database.types import JSONB


# revision identifiers, used by Alembic.
revision: str = '019_postmortems'
down_revision: Union[str, None] = '018_digest'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'postmortems',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('incident_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        # `status` defaults to "draft" in Python, not in the DDL: every row is
        # written through the store, and a server default here would be drift
        # against the model.
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=32), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Empty list, never NULL — the reader treats these as lists unconditionally.
        sa.Column('authors', JSONB, nullable=False),
        sa.Column('reviewers', JSONB, nullable=False),
        sa.Column('sections', JSONB, nullable=False),
        sa.Column('action_items', JSONB, nullable=False),
        sa.Column('timeline', JSONB, nullable=False),
        sa.Column('doc_metrics', JSONB, nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('incident_id'),
    )
    op.create_index('ix_postmortems_org', 'postmortems', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_postmortems_org', table_name='postmortems')
    op.drop_table('postmortems')
