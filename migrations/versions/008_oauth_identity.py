"""Move OAuth identity and login CSRF state out of process memory.

Two dicts in app/auth/oauth.py become tables:

    _OAUTH_ACCOUNTS  ->  oauth_accounts
    _OAUTH_STATES    ->  oauth_states

`_USER_ACCOUNTS` (user id -> list of account ids) gets no table. It was a
hand-maintained index over the same records, so it is now a query against
`ix_oauth_accounts_user` and cannot drift out of step with what it indexes.

This is not a like-for-like move; two behaviours change deliberately.

1.  A returning user keeps their user id. The callback in
    app/auth/endpoints.py mints `user_{token_hex(8)}` whenever the account
    lookup misses. With the lookup in a per-process dict every restart was a
    miss, so the same person signing in with the same Google account came back
    as a different user id each time, and their team memberships, roles and
    investigation history -- all keyed by user id -- stayed behind under an
    identity that could never be signed into again. Existing deployments have
    no record of those old ids anywhere, so this migration cannot reunite them;
    it stops the bleeding from here on.

2.  Logins survive having more than one replica. The CSRF state is issued on
    one request and presented back on the provider's redirect, which is a
    separate connection that load-balances independently. Held per pod, it
    verified only when both requests happened to hit the same one.

`oauth_states` is a short-lived working table, not history: rows are consumed
on use and swept after 15 minutes. `oauth_accounts.id` is the natural key
`"{provider}:{provider_id}"`, so the primary key enforces one local user per
provider identity.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008_oauth"
down_revision = "007_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.String(length=320), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_login",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oauth_accounts_user", "oauth_accounts", ["user_id"])
    op.create_index("ix_oauth_accounts_email", "oauth_accounts", ["email"])

    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index("ix_oauth_states_created", "oauth_states", ["created_at"])


def downgrade() -> None:
    """Drops both tables.

    Dropping oauth_accounts unlinks every provider identity, so the next sign-in
    by an existing user mints them a new user id and orphans whatever is keyed
    to the old one -- the exact failure this revision was written to stop.
    Dropping oauth_states only invalidates logins that are mid-flight.
    """
    op.drop_index("ix_oauth_states_created", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_oauth_accounts_email", table_name="oauth_accounts")
    op.drop_index("ix_oauth_accounts_user", table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
