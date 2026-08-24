"""slo tables

SLO definitions lived in `_SLOS` and their measurement series in `_SLI_DATA`,
two module-level dicts. A restart deleted every objective a customer had defined
along with all the data behind it, so the error budget reset to "no data" and
the burn-rate alerting went quiet — right after a deploy, which is exactly when
the budget is being spent. With more than one worker an SLO existed only for the
process that happened to answer the request that created it.

Two tables, written by app/slo/tracker.py. `metadata` is the column name; the
ORM attribute is `meta`, because `metadata` is taken on a declarative class.

Revision ID: 007_slos
Revises: 006_knowledge
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '007_slos'
down_revision: Union[str, None] = '006_knowledge'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sli_measurements',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('slo_id', sa.String(length=64), nullable=False),
    sa.Column('sli_id', sa.String(length=64), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('good_count', sa.Integer(), nullable=False),
    sa.Column('total_count', sa.Integer(), nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sli_measurements_org_slo_ts', 'sli_measurements', ['org_id', 'slo_id', 'timestamp'], unique=False)
    op.create_table('slos',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('slo_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('service', sa.String(length=128), nullable=False),
    sa.Column('sli_type', sa.String(length=32), nullable=False),
    sa.Column('target', sa.Float(), nullable=False),
    sa.Column('window', sa.String(length=32), nullable=False),
    sa.Column('team_id', sa.String(length=128), nullable=False),
    sa.Column('owner', sa.String(length=256), nullable=False),
    sa.Column('labels', JSONB, nullable=False),
    sa.Column('alert_burn_rate_threshold', sa.Float(), nullable=False),
    sa.Column('alert_channels', JSONB, nullable=False),
    sa.Column('metadata', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_slos_org_service', 'slos', ['org_id', 'service'], unique=False)
    op.create_index('ix_slos_org_slo', 'slos', ['org_id', 'slo_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_slos_org_slo', table_name='slos')
    op.drop_index('ix_slos_org_service', table_name='slos')
    op.drop_table('slos')
    op.drop_index('ix_sli_measurements_org_slo_ts', table_name='sli_measurements')
    op.drop_table('sli_measurements')
