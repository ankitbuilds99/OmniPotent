"""Persist waitlist signups

Revision ID: 006_waitlist
Revises: 005_runbooks
Create Date: 2026-08-20

Moves the waitlist out of process memory:

  app/waitlist/manager.py  _waitlist_store   -> waitlist_signups
  app/waitlist/manager.py  _email_index      -> waitlist_signups.email (unique)
  app/waitlist/manager.py  _position_counter -> MAX(position) + 1

These were leads: real email addresses submitted by real people through the
public landing page, held in a dict that was emptied on every restart, deploy
and scale-down, with no export having ever run against it. `_position_counter`
was worse than merely volatile — it reset to 0 with the dict, so the first
signup after a deploy was handed position 1, which an earlier signup already
held.

Unlike every other table added in this effort, this one is NOT tenant-scoped. A
waitlist signup happens before the person has an organization, so there is no
org to scope it to; the waitlist is a property of the product, not of a tenant.

`email` is unique because it is the identity a caller looks an entry up by, and
`position` is unique so that two concurrent signups reading the same MAX() lose
loudly and retry rather than silently sharing a position. `ip_hash` stores a
truncated SHA-256, never the address: it exists for duplicate detection, not for
anyone to read back out of the table.

No data migration: the state being moved was destroyed on every restart, so
there is nothing anywhere to carry forward.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "006_waitlist"
down_revision: Union[str, None] = "005_runbooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column(
            "source", sa.String(32), nullable=False
        ),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("team_size", sa.String(64), nullable=False),
        sa.Column("use_case", sa.Text(), nullable=False),
        sa.Column("referral_code", sa.String(32), nullable=False),
        sa.Column("referred_by", sa.String(320), nullable=False),
        sa.Column("referral_count", sa.Integer(), nullable=False),
        sa.Column("utm_source", sa.String(255), nullable=False),
        sa.Column("utm_medium", sa.String(255), nullable=False),
        sa.Column("utm_campaign", sa.String(255), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The identity a caller looks an entry up by, and the queue slot they
        # were promised. Both must be one-to-one with a row.
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("position"),
    )
    op.create_index("ix_waitlist_signups_referral_code", "waitlist_signups", ["referral_code"])
    op.create_index("ix_waitlist_tier_source", "waitlist_signups", ["tier", "source"])
    op.create_index("ix_waitlist_created", "waitlist_signups", ["created_at"])


def downgrade() -> None:
    """Drop the table.

    This DESTROYS captured leads — unlike the other downgrades in this series,
    which only revert to state that was already being thrown away every restart.
    Export first (`export_waitlist`) if the rows still matter.
    """
    op.drop_index("ix_waitlist_created", table_name="waitlist_signups")
    op.drop_index("ix_waitlist_tier_source", table_name="waitlist_signups")
    op.drop_index("ix_waitlist_signups_referral_code", table_name="waitlist_signups")
    op.drop_table("waitlist_signups")
