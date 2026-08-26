"""notification_digest_entries — notifications held back for an hourly/daily digest

The Notification Preferences page offers "Batch non-critical notifications into
periodic digests" with hourly and daily options. Nothing read the setting, along
with everything else on that page. Honouring it needs somewhere to keep a
notification between the moment it happens and the moment its window closes: an
hour or a day is longer than a process is guaranteed to live, and an org's
pending digest has to survive a deploy in the middle of it.

`channels` is stored per entry rather than re-derived when the digest is sent,
so an org editing its routing matrix mid-window does not silently discard
everything already collected under the old rules.

Revision ID: 018_digest
Revises: 017_dedupe
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# JSONB on Postgres, JSON on SQLite — see revision 012.
from app.database.types import JSONB


# revision identifiers, used by Alembic.
revision: str = '018_digest'
down_revision: Union[str, None] = '017_dedupe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_digest_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('event', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('channels', JSONB, nullable=False),
        sa.Column('data', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_notif_digest_org_created',
        'notification_digest_entries',
        ['org_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_notif_digest_org_created', table_name='notification_digest_entries')
    op.drop_table('notification_digest_entries')
