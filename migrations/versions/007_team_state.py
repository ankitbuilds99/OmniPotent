"""Persist teams, memberships and invitations

Revision ID: 007_teams
Revises: 006_waitlist
Create Date: 2026-08-20

Moves the team workspace out of process memory:

  app/teams/store.py  TeamStore._teams       -> teams
  app/teams/store.py  TeamStore._members     -> team_members
  app/teams/store.py  TeamStore._invitations -> team_invitations
  app/teams/store.py  TeamStore._user_teams  -> a query over team_members

The store's own docstring said "swap for database in production"; its router was
wired into the API at app/api/main.py regardless. So a team created on one pod
did not exist on any other, and every team, membership and pending invitation
was destroyed on each restart, deploy and scale-down. An invitation is a promise
made to somebody outside the system, who has already been handed a link — that
link simply stopped resolving.

Two constraints here are new behaviour rather than a like-for-like move:

  teams.slug is UNIQUE. The dict version de-duplicated auto-generated slugs by
  scanning, but accepted a caller-supplied slug verbatim, so two teams could
  hold the same one; get_team_by_slug then returned whichever the scan reached
  first and the loser was unreachable by its own URL.

  team_members (team_id, user_id) is UNIQUE. "Already a member" was a
  read-then-write check that two concurrent invitation accepts both passed.

`_user_teams` gets no table. It was a hand-maintained index from user to team
ids, updated at four call sites; here the same question is a query against
team_members, which cannot disagree with the memberships it is derived from.

No data migration: the state being moved was destroyed on every restart, so
there is nothing anywhere to carry forward.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "007_teams"
down_revision: Union[str, None] = "006_waitlist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON anywhere else — matching app/database/models.py.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("settings", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # A team's URL must identify exactly one team.
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_teams_owner_id", "teams", ["owner_id"])

    op.create_table(
        "team_members",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_active", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_members_team_user", "team_members", ["team_id", "user_id"], unique=True
    )
    op.create_index("ix_team_members_user", "team_members", ["user_id"])
    op.create_index("ix_team_members_team_email", "team_members", ["team_id", "email"])

    op.create_table(
        "team_invitations",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("invited_by", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_invitations_team_status", "team_invitations", ["team_id", "status"]
    )
    op.create_index(
        "ix_team_invitations_email_status", "team_invitations", ["email", "status"]
    )


def downgrade() -> None:
    """Drop the three tables.

    This DESTROYS teams, their membership lists and any outstanding invitation,
    the same way a restart used to. There is no export for these; take a dump
    first if the rows still matter.
    """
    op.drop_index("ix_team_invitations_email_status", table_name="team_invitations")
    op.drop_index("ix_team_invitations_team_status", table_name="team_invitations")
    op.drop_table("team_invitations")

    op.drop_index("ix_team_members_team_email", table_name="team_members")
    op.drop_index("ix_team_members_user", table_name="team_members")
    op.drop_index("ix_team_members_team_user", table_name="team_members")
    op.drop_table("team_members")

    op.drop_index("ix_teams_owner_id", table_name="teams")
    op.drop_table("teams")
