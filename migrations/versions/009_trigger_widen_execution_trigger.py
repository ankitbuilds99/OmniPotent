"""widen runbook_executions.trigger to match runbooks.trigger

`runbooks.trigger` is VARCHAR(64) and `runbook_executions.trigger` was
VARCHAR(32), and `record_execution` copies the runbook's trigger straight into
the execution row. Nothing caps the trigger on the way in — `CreateRunbookRequest`
declares it as a plain `str` — so a runbook created with a 33-to-64 character
trigger saved fine and then failed to record a single execution: on Postgres
the insert raises StringDataRightTruncation and the execute request 500s, so
the audit trail for a script that runs against production infrastructure has a
hole in it. SQLite ignores VARCHAR lengths, which is why it never showed up in
development.

Revision ID: 009_trigger
Revises: 008_status
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '009_trigger'
down_revision: Union[str, None] = '008_status'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('runbook_executions') as batch_op:
        batch_op.alter_column(
            'trigger',
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Narrowing truncates anything already stored past 32 characters, which is
    # the data this revision exists to keep. Postgres refuses the ALTER rather
    # than doing it silently; that is the correct outcome.
    with op.batch_alter_table('runbook_executions') as batch_op:
        batch_op.alter_column(
            'trigger',
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
