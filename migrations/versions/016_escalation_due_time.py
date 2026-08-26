"""oncall_alerts.next_escalation_at — when the alert's next step comes due

`EscalationService._schedule_next_escalation` was a single log line, so nothing
in the codebase ever called `escalate_to_next_step` and every alert sat on step
0 forever. The second and third levels of an escalation policy were unreachable
no matter how long the alert went unacknowledged, and `delay_minutes` was
decorative.

The due time is persisted rather than held in an in-process timer for two
reasons: a timer loses every pending escalation on exactly the restart that is
most likely to happen mid-incident, and with more than one worker running each
one would fire the same step.

Indexed because the sweeper's only query is "the due ones, oldest first", and it
runs every 30 seconds against a table that grows with every alert ever received.

Revision ID: 016_escalation
Revises: 015_growth
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '016_escalation'
down_revision: Union[str, None] = '015_growth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'oncall_alerts',
        sa.Column('next_escalation_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f('ix_oncall_alerts_next_escalation_at'),
        'oncall_alerts',
        ['next_escalation_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_oncall_alerts_next_escalation_at'), table_name='oncall_alerts')
    op.drop_column('oncall_alerts', 'next_escalation_at')
