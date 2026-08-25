"""teams, team members and team invitations

The Teams page had no tables behind it. `app/teams/store.py` opened with
"In-memory store for teams (swap for database in production)" and kept four
dicts, so every team, roster change and pending invitation was lost on restart.
`app/database/team_models.py` was written to back it but did
`from app.database.base import Base` — a module that does not exist — so nothing
could import it and no revision ever created its tables.

Each table carries `org_id`: the in-memory store got its tenant isolation from
one dict per org, which does not survive the move to a shared table.

Revision ID: 013_teams
Revises: 012_notifications
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# JSONB on Postgres, JSON on SQLite. Autogenerate emits
# `postgresql.JSONB(astext_type=Text())` here, which is a NameError — `Text` is
# not imported — and would render JSONB on SQLite even if it were.
from app.database.types import JSONB


# revision identifiers, used by Alembic.
revision: str = '013_teams'
down_revision: Union[str, None] = '012_notifications'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('teams',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('owner_id', sa.String(length=255), nullable=False),
    sa.Column('settings', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'slug', name='uq_team_org_slug')
    )
    op.create_index('ix_teams_org_owner', 'teams', ['org_id', 'owner_id'], unique=False)
    op.create_table('team_invitations',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('invited_by', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_team_invitations_org_email', 'team_invitations', ['org_id', 'email'], unique=False)
    op.create_index('ix_team_invitations_team_status', 'team_invitations', ['team_id', 'status'], unique=False)
    op.create_table('team_members',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('user_id', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_active', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'user_id', name='uq_team_member')
    )
    op.create_index('ix_team_members_org_user', 'team_members', ['org_id', 'user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_team_members_org_user', table_name='team_members')
    op.drop_table('team_members')
    op.drop_index('ix_team_invitations_team_status', table_name='team_invitations')
    op.drop_index('ix_team_invitations_org_email', table_name='team_invitations')
    op.drop_table('team_invitations')
    op.drop_index('ix_teams_org_owner', table_name='teams')
    op.drop_table('teams')
