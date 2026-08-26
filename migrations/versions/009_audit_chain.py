"""Move the tamper-evident audit chain out of an in-process ring buffer.

    app/audit_legacy.py::_BUFFER  ->  audit_events

`_BUFFER` was a `deque(maxlen=2000)` backing both GET /audit and GET
/audit/verify. It failed as a tamper-evidence control in two ways, and only the
second one needed more than one replica.

1.  It reported tampering that had not happened. `verify_chain` recomputes the
    chain from a genesis of `prev_hash == ""`. A bounded deque evicts its oldest
    entry once full, so from event 2001 onwards the oldest surviving entry's
    `prev_hash` pointed at a hash no longer in the buffer, and the endpoint
    answered `{"valid": false, "broken_at": 0}` forever after -- on a single
    pod, with nothing wrong. That is the worst possible failure for this
    control: it is indistinguishable from a real detection, so the first real
    one would be read as the same known false alarm.

2.  It attested to one pod's fraction of the trail. Every replica kept its own
    buffer and its own running `_LAST_HASH`, so below 2000 events /audit/verify
    answered `valid: true` about whatever slice happened to land on the pod
    serving the request, and /audit returned that same slice.

The events themselves are the record of who approved which infrastructure
change -- `approval.decided` is written from the websocket approval handler --
so a trail that silently covers a fraction of them is worse than no trail,
because it looks complete.

The chain is per-org, and its position is now explicit rather than implied by
list order. `seq` counts from 0 within an org and `(org_id, seq)` is unique, so
concurrent appends from several processes cannot occupy the same position: the
losing insert raises IntegrityError, re-reads the head and retries. That is the
same optimistic pattern 006 used for waitlist positions, and it is what allows a
strictly serial hash chain to be appended to from more than one replica.

Nothing is evicted, which is what makes verification mean anything: a hash that
does not fold into its successor, or a gap in `seq`, is now evidence rather than
an artifact of the buffer having wrapped.

This migration creates an empty table. Events buffered in memory by a running
process are not migrated -- they were never durable, and on a multi-replica
deployment no single process ever held more than a fraction of them. The JSONL
sink written when COPILOT_AUDIT_LOG is set is unaffected and still written.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009_audit"
down_revision = "008_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        # Stored as the string it was hashed with. Parsing it into a DateTime
        # and re-rendering it on read would change the canonical form and break
        # verification of every row after it.
        sa.Column("ts", sa.String(length=32), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One event per chain position. This is the invariant the deque had no
        # way to hold, and the reason concurrent appends are safe.
        sa.UniqueConstraint("org_id", "seq", name="uq_audit_events_org_seq"),
    )
    op.create_index("ix_audit_events_org_seq", "audit_events", ["org_id", "seq"])
    op.create_index("ix_audit_events_event", "audit_events", ["event"])


def downgrade() -> None:
    """Drops the table, and with it the audit history.

    Unlike the other revisions here this one destroys the only durable copy of
    the trail unless COPILOT_AUDIT_LOG was set, and the trail is exactly the
    thing an auditor asks for after an incident. Export before running it.
    """
    op.drop_index("ix_audit_events_event", table_name="audit_events")
    op.drop_index("ix_audit_events_org_seq", table_name="audit_events")
    op.drop_table("audit_events")
