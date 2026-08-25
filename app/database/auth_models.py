"""SQLAlchemy models for OAuth identity and session revocation.

Both of these used to be module-level dicts in `app/auth/oauth.py`:

* `_OAUTH_ACCOUNTS` / `_USER_ACCOUNTS` held every linked provider account, so a
  restart (or simply a second pod) lost the link. The visible symptom was
  `/auth/me` returning `providers: []` and `avatar_url: null` for a user who had
  just signed in, and the `find_account_by_email` de-duplication silently failing
  so the same person got a fresh `user_...` id on every login.
* There was no revocation record at all. `POST /auth/logout` deleted the cookie
  and returned `{"status": "logged_out"}` while the JWT it had handed out stayed
  valid for its full 24 hours — a copied or leaked token could not be withdrawn
  short of rotating COPILOT_SECRET_KEY, which is also the secrets-vault KEK.

Neither table carries an `org_id`. Identity is deliberately cross-org: one person
can be a member of several orgs, and in single-tenant mode there is no org row to
point at.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base
from app.database.types import UUID


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OAuthAccountDB(Base):
    """One provider identity (github:12345) linked to one local user id."""

    __tablename__ = "oauth_accounts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # `provider` + `provider_id` is the identity the IdP asserts; it is what the
    # callback looks up, and it must be unique or two logins could diverge onto
    # two local users.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_login: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    __table_args__ = (
        Index("uq_oauth_account_provider", "provider", "provider_id", unique=True),
        Index("ix_oauth_accounts_user", "user_id"),
        # Lowercased at write time, so this index serves find_account_by_email.
        Index("ix_oauth_accounts_email", "email"),
    )


class RevokedSessionDB(Base):
    """A session JWT that must no longer be accepted.

    Keyed on the token's `jti`. Rows are only useful until the token would have
    expired anyway, so `expires_at` exists to let the table be swept rather than
    grow forever.
    """

    __tablename__ = "revoked_sessions"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    __table_args__ = (Index("ix_revoked_sessions_expires", "expires_at"),)
