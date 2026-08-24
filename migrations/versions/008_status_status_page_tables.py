"""status page tables

The public status page kept components, incidents, maintenance windows and
subscribers in four dicts on an in-process object. A restart put the page back
to "All Systems Operational" in the middle of an outage, and took the
subscriber list with it — the email addresses and webhook URLs of a customer's
own users, collected through a public form.

Six tables, written by app/status_page/store.py. `group` is reserved in SQL so
the component's group is `group_name`; `status_since` is separate from
`updated_at` because renaming a component must not reset how long it has been
down, which is what the uptime figures are measured from.

`status_incidents` is not `incidents`: that one is the internal incident record
with responders and investigations attached, this one is what customers see.

Revision ID: 008_status
Revises: 007_slos
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.types import JSONB

# revision identifiers, used by Alembic.
revision: str = '008_status'
down_revision: Union[str, None] = '007_slos'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('status_components',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('component_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('group_name', sa.String(length=128), nullable=True),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status_since', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_components_org_component', 'status_components', ['org_id', 'component_id'], unique=True)
    op.create_index('ix_status_components_org_order', 'status_components', ['org_id', 'display_order', 'name'], unique=False)
    op.create_table('status_incident_updates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('update_id', sa.String(length=64), nullable=False),
    sa.Column('incident_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=256), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_incident_updates_org_incident', 'status_incident_updates', ['org_id', 'incident_id', 'created_at', 'sequence'], unique=False)
    op.create_table('status_incidents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('severity', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('affected_components', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_incidents_org_created', 'status_incidents', ['org_id', 'created_at'], unique=False)
    op.create_index('ix_status_incidents_org_incident', 'status_incidents', ['org_id', 'incident_id'], unique=True)
    op.create_table('status_maintenances',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('maintenance_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('affected_components', JSONB, nullable=False),
    sa.Column('scheduled_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('scheduled_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actual_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actual_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_maintenances_org_maintenance', 'status_maintenances', ['org_id', 'maintenance_id'], unique=True)
    op.create_index('ix_status_maintenances_org_start', 'status_maintenances', ['org_id', 'scheduled_start'], unique=False)
    op.create_table('status_subscribers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('subscriber_id', sa.String(length=64), nullable=False),
    sa.Column('subscription_type', sa.String(length=32), nullable=False),
    sa.Column('target', sa.String(length=512), nullable=False),
    sa.Column('components', JSONB, nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('verification_token', sa.String(length=128), nullable=True),
    sa.Column('notify_on_incidents', sa.Boolean(), nullable=False),
    sa.Column('notify_on_maintenance', sa.Boolean(), nullable=False),
    sa.Column('notify_on_resolved', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_subscribers_org_subscriber', 'status_subscribers', ['org_id', 'subscriber_id'], unique=True)
    op.create_index('ix_status_subscribers_org_target', 'status_subscribers', ['org_id', 'target'], unique=True)
    op.create_table('status_uptime_records',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('component_id', sa.String(length=128), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_minutes', sa.Float(), nullable=False),
    sa.Column('was_operational', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_uptime_org_component_at', 'status_uptime_records', ['org_id', 'component_id', 'recorded_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_status_uptime_org_component_at', table_name='status_uptime_records')
    op.drop_table('status_uptime_records')
    op.drop_index('ix_status_subscribers_org_target', table_name='status_subscribers')
    op.drop_index('ix_status_subscribers_org_subscriber', table_name='status_subscribers')
    op.drop_table('status_subscribers')
    op.drop_index('ix_status_maintenances_org_start', table_name='status_maintenances')
    op.drop_index('ix_status_maintenances_org_maintenance', table_name='status_maintenances')
    op.drop_table('status_maintenances')
    op.drop_index('ix_status_incidents_org_incident', table_name='status_incidents')
    op.drop_index('ix_status_incidents_org_created', table_name='status_incidents')
    op.drop_table('status_incidents')
    op.drop_index('ix_status_incident_updates_org_incident', table_name='status_incident_updates')
    op.drop_table('status_incident_updates')
    op.drop_index('ix_status_components_org_order', table_name='status_components')
    op.drop_index('ix_status_components_org_component', table_name='status_components')
    op.drop_table('status_components')
