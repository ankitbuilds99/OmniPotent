"""incidents tables

Revision ID: 003_incidents
Revises: 002_oncall

Incidents lived in a process-memory dict seeded with three fabricated rows, so
every incident a customer declared was lost on the next restart and the seeded
ones showed up in every org's list as if they were real.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '003_incidents'
down_revision: Union[str, None] = '002_oncall'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('incidents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('service', sa.String(length=256), nullable=False),
    sa.Column('triggered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acknowledged_by', sa.String(length=256), nullable=True),
    sa.Column('resolved_by', sa.String(length=256), nullable=True),
    sa.Column('assignee', sa.String(length=256), nullable=True),
    sa.Column('tags', JSONB, nullable=False),
    sa.Column('timeline', JSONB, nullable=False),
    sa.Column('related_alerts', JSONB, nullable=False),
    sa.Column('impact', sa.Text(), nullable=True),
    sa.Column('customer_impact', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_incidents_org_status', 'incidents', ['org_id', 'status'], unique=False)
    op.create_index('ix_incidents_org_triggered', 'incidents', ['org_id', 'triggered_at'], unique=False)
    op.create_table('incident_investigations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('overall_confidence', sa.Float(), nullable=False),
    sa.Column('root_cause', sa.Text(), nullable=True),
    sa.Column('recommended_actions', JSONB, nullable=False),
    sa.Column('agents', JSONB, nullable=False),
    sa.Column('safety_gates', JSONB, nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('incident_id')
    )
    op.create_index('ix_incident_investigations_org', 'incident_investigations', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_incident_investigations_org', table_name='incident_investigations')
    op.drop_table('incident_investigations')
    op.drop_index('ix_incidents_org_triggered', table_name='incidents')
    op.drop_index('ix_incidents_org_status', table_name='incidents')
    op.drop_table('incidents')
