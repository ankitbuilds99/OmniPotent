"""runbooks table

Runbooks lived in a module-level dict. `runbook_executions` has existed since
001 with no writer, so the audit trail for "who ran what against production"
was never recorded; this adds the missing half — the runbook definitions
themselves — and both are now written from app/runbooks/store.py.

The primary key is a surrogate because `runbook_id` is the id the API exposes
and the built-ins reuse it across orgs ("restart-service"); identity is the
unique (org_id, runbook_id) index.

Revision ID: 004_runbooks
Revises: 003_incidents
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '004_runbooks'
down_revision: Union[str, None] = '003_incidents'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('runbooks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('runbook_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('service', sa.String(length=128), nullable=False),
    sa.Column('trigger', sa.String(length=64), nullable=False),
    sa.Column('execution_mode', sa.String(length=32), nullable=False),
    sa.Column('steps', JSONB, nullable=False),
    sa.Column('labels', JSONB, nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_runbooks_org_runbook', 'runbooks', ['org_id', 'runbook_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_runbooks_org_runbook', table_name='runbooks')
    op.drop_table('runbooks')
