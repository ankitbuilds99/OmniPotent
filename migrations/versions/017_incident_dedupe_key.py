"""incidents.dedupe_key — the sender's own id for the underlying alert

Alertmanager is a state feed: it re-sends every firing group on
`repeat_interval` (4h in the stock config) and sends a matching
`status: "resolved"` delivery when the alert clears. The receiver treated every
delivery as new and `continue`d past anything that was not firing, so one
flapping alert produced a fresh incident every four hours and none of them ever
closed. `fingerprint` — which Alertmanager computes from the label set, and
which was read nowhere in the webhook module — is what ties those deliveries
together.

Nullable because the other receivers have nothing equivalent to offer yet, and
because every incident that predates this column has no alert behind it to
identify.

Indexed on (org_id, dedupe_key) rather than made unique: the same alert firing,
resolving, and firing again next week is genuinely two incidents, so uniqueness
would have to be "unique among unresolved", which is a partial index Postgres
supports and SQLite does not. The lookup filters on status instead.

Revision ID: 017_dedupe
Revises: 016_escalation
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '017_dedupe'
down_revision: Union[str, None] = '016_escalation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('incidents', sa.Column('dedupe_key', sa.String(length=256), nullable=True))
    op.create_index('ix_incidents_org_dedupe', 'incidents', ['org_id', 'dedupe_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_incidents_org_dedupe', table_name='incidents')
    op.drop_column('incidents', 'dedupe_key')
