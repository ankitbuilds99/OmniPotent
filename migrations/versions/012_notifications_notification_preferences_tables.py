"""notification preferences tables

The Notification Preferences page had no backend: `GET
/api/notifications/preferences` was a 404 and nothing in the route dump matched
"notif". Its nine channels and eight routing rules were component literals and
its Save button was `setSaved(true); setTimeout(...)` — a two-second checkmark
that issued no request — so enabling PagerDuty or pasting a Slack webhook was
lost on reload.

These three tables are where those preferences live. Secrets (webhook URLs,
routing keys) go in `notification_channel_preferences.secrets` and are never
returned by the API; the read path reports `is_set` instead.

Revision ID: 012_notifications
Revises: 011_llm_usage
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# JSONB on Postgres, JSON on SQLite. Rendering `postgresql.JSONB` directly is
# what made revision 001 die with "can't render element of type JSONB" against
# the default SQLite URL.
from app.database.types import JSONB


# revision identifiers, used by Alembic.
revision: str = '012_notifications'
down_revision: Union[str, None] = '011_llm_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('notification_channel_preferences',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('channel', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('config', JSONB, nullable=False),
    sa.Column('secrets', JSONB, nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notif_channel_org_channel', 'notification_channel_preferences', ['org_id', 'channel'], unique=True)
    op.create_table('notification_delivery_settings',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('quiet_hours_enabled', sa.Boolean(), nullable=False),
    sa.Column('quiet_hours_start', sa.String(length=5), nullable=False),
    sa.Column('quiet_hours_end', sa.String(length=5), nullable=False),
    sa.Column('digest_mode', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id')
    )
    op.create_table('notification_rules',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('event', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notif_rule_org_event', 'notification_rules', ['org_id', 'event'], unique=False)
    op.create_index('ix_notif_rule_org_event_channel', 'notification_rules', ['org_id', 'event', 'channel'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_notif_rule_org_event_channel', table_name='notification_rules')
    op.drop_index('ix_notif_rule_org_event', table_name='notification_rules')
    op.drop_table('notification_rules')
    op.drop_table('notification_delivery_settings')
    op.drop_index('ix_notif_channel_org_channel', table_name='notification_channel_preferences')
    op.drop_table('notification_channel_preferences')
