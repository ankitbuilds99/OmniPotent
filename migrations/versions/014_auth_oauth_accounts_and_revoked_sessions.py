"""oauth accounts and revoked sessions

Both of these were module-level dicts in `app/auth/oauth.py`. `_OAUTH_ACCOUNTS`
lost every provider link on restart (so `/auth/me` reported `providers: []` for a
user who had just signed in, and the same person got a new local user id on their
next login), and there was no revocation record at all — `POST /auth/logout`
deleted the cookie while the JWT it had issued stayed valid for its full 24h.

Neither table is org-scoped: identity is cross-org, and single-tenant deployments
have no org row to reference.

Revision ID: 014_auth
Revises: 013_teams
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '014_auth'
down_revision: Union[str, None] = '013_teams'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oauth_accounts',
        sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('provider_id', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('avatar_url', sa.String(length=1024), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique: two logins from the same provider identity must never diverge onto
    # two local users.
    op.create_index(
        'uq_oauth_account_provider', 'oauth_accounts', ['provider', 'provider_id'], unique=True
    )
    op.create_index('ix_oauth_accounts_user', 'oauth_accounts', ['user_id'], unique=False)
    op.create_index('ix_oauth_accounts_email', 'oauth_accounts', ['email'], unique=False)

    op.create_table(
        'revoked_sessions',
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('jti'),
    )
    op.create_index(
        'ix_revoked_sessions_expires', 'revoked_sessions', ['expires_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_revoked_sessions_expires', table_name='revoked_sessions')
    op.drop_table('revoked_sessions')
    op.drop_index('ix_oauth_accounts_email', table_name='oauth_accounts')
    op.drop_index('ix_oauth_accounts_user', table_name='oauth_accounts')
    op.drop_index('uq_oauth_account_provider', table_name='oauth_accounts')
    op.drop_table('oauth_accounts')
